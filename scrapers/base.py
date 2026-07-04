"""Base scraper: headless Playwright + BeautifulSoup with stealth/delays."""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

from models.db import Listing, get_session
from config import Config, price_max_for_beds, budget_for_beds
from services.ranker import score_listing
from services.geocoder import geocode_listing

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

AMENITY_MAP = {
    "laundry_in_unit": [
        "laundry in unit", "in-unit washer", "in-unit laundry", "w/d in unit",
        "washer/dryer in unit", "washer and dryer in unit", "washer & dryer in unit",
    ],
    "laundry_in_building": [
        "laundry in building", "laundry facility", "laundry facilities",
        "on-site laundry", "shared laundry", "laundry room",
    ],
    "dishwasher": ["dishwasher"],
    "near_subway": [
        "near subway", "steps to subway", "walk to subway", "subway access",
        "close to train", "near train", "near metro", "transit",
        "steps from subway", "near mta",
    ],
    "gym": ["fitness center", "gym", "fitness room", "workout room"],
    "rooftop": ["rooftop deck", "roof terrace", "rooftop", "roof deck"],
}

# Amenities that indicate a luxury/high-rise building — shown in "Building Amenities" column
BUILDING_AMENITY_KEYWORDS = [
    "doorman", "concierge", "elevator", "fitness center", "gym", "pool",
    "rooftop", "roof deck", "roof terrace", "valet", "parking",
    "common outdoor space", "courtyard", "storage", "bike room",
    "dishwasher", "central air", "a/c", "air conditioning",
]



_PRICE_MIN = 500
_PRICE_MAX = 30_000

FLEX_KEYWORDS = [
    "flex", "convertible", "alcove", "jr4", "junior 4",
    "home office", "can be used as", "convert",
]


def parse_price(text: str) -> int | None:
    """Extract monthly rent from text. Requires a $ sign.
    For ranges like '$5,000–$8,000' returns the midpoint."""
    hits = re.findall(r'\$\s*([\d,]+)', text)
    if not hits:
        return None
    prices = []
    for h in hits:
        try:
            p = int(h.replace(',', ''))
            if _PRICE_MIN < p < _PRICE_MAX:
                prices.append(p)
        except ValueError:
            pass
    if not prices:
        return None
    if len(prices) >= 2:
        return int(sum(prices[:2]) / 2)  # midpoint of range
    return prices[0]


def detect_flex(texts: list[str]) -> bool:
    combined = " ".join(texts).lower()
    return any(kw in combined for kw in FLEX_KEYWORDS)


def normalize_amenities(amenities: list[str]) -> dict[str, bool]:
    result = {k: False for k in AMENITY_MAP}
    for amenity_str in amenities:
        lower = amenity_str.lower()
        for key, keywords in AMENITY_MAP.items():
            if any(kw in lower for kw in keywords):
                result[key] = True
    return result


def extract_building_amenities(amenity_strings: list[str]) -> str:
    """Return a comma-separated string of recognised luxury/building amenities."""
    found = []
    combined = " ".join(amenity_strings).lower()
    for kw in BUILDING_AMENITY_KEYWORDS:
        if kw in combined and kw.title() not in found:
            found.append(kw.title())
    return ", ".join(found)


def make_external_id(source: str, *parts: str) -> str:
    raw = f"{source}:" + "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


class BaseScraper(ABC):
    source: str = ""

    def __init__(self):
        self._session = None

    def scrape(self, preferences: dict) -> list[dict]:
        """Run the scraper and return a list of normalized listing dicts."""
        raise NotImplementedError

    def _get_page_html(self, url: str, wait_for: str | None = None) -> str:
        """Fetch a URL with Playwright (headless) and return the HTML."""
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=10000)
                except Exception:
                    pass
            time.sleep(random.uniform(1, 3))
            html = page.content()
            browser.close()
        return html

    def _get_json_blob(self, url: str, js_var: str) -> Any:
        """Extract a JSON blob assigned to a JS variable in the page source."""
        html = self._get_page_html(url)
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            text = script.string or ""
            if js_var in text:
                start = text.find(js_var)
                # Find the JSON start
                eq_pos = text.find("=", start) + 1
                json_str = text[eq_pos:].strip().rstrip(";")
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
        return None

    def _apply_broker_fee_default(self, listing: dict) -> None:
        if listing.get("broker_fee") is None:
            listing["broker_fee"] = 1.0
            listing["broker_fee_source"] = "assumed"
        else:
            listing["broker_fee_source"] = "listed"

    def save_listings(self, raw_listings: list[dict], preferences: dict) -> int:
        """
        Upsert raw listing dicts into SQLite.
        Returns count of new listings inserted.
        """
        session = get_session(Config.DB_PATH)
        new_count = 0
        try:
            for raw in raw_listings:
                ext_id = raw.get("external_id")
                if not ext_id:
                    continue

                existing = (
                    session.query(Listing)
                    .filter_by(external_id=ext_id)
                    .first()
                )

                if existing:
                    existing.price = raw.get("price", existing.price)
                    existing.address = raw.get("address") or existing.address
                    existing.neighborhood = raw.get("neighborhood") or existing.neighborhood
                    existing.baths = raw.get("baths") or existing.baths
                    existing.broker_fee = raw.get("broker_fee") if raw.get("broker_fee") is not None else existing.broker_fee
                    existing.broker_fee_source = raw.get("broker_fee_source") or existing.broker_fee_source
                    existing.laundry_in_unit = raw.get("laundry_in_unit", existing.laundry_in_unit)
                    existing.laundry_in_building = raw.get("laundry_in_building", existing.laundry_in_building)
                    existing.dishwasher = raw.get("dishwasher", existing.dishwasher)
                    existing.near_subway = raw.get("near_subway", existing.near_subway)
                    existing.gym = raw.get("gym", existing.gym)
                    existing.rooftop = raw.get("rooftop", existing.rooftop)
                    existing.has_photos = raw.get("has_photos") if raw.get("has_photos") is not None else existing.has_photos
                    existing.has_flex = raw.get("has_flex") if raw.get("has_flex") is not None else existing.has_flex
                    existing.nearest_subway = raw.get("nearest_subway") or existing.nearest_subway
                    existing.building_amenities = raw.get("building_amenities") or existing.building_amenities
                    existing.listed_date = raw.get("listed_date") or existing.listed_date
                    existing.move_in_date = raw.get("move_in_date") or existing.move_in_date
                    existing.contact_name = raw.get("contact_name") or existing.contact_name
                    existing.contact_email = raw.get("contact_email") or existing.contact_email
                    existing.contact_phone = raw.get("contact_phone") or existing.contact_phone
                    existing.updated_at = __import__("datetime").datetime.utcnow().isoformat()
                    listing = existing
                else:
                    amenities_list = raw.get("amenities_raw", [])
                    amenities_raw = json.dumps(amenities_list)
                    all_text = amenities_list + [raw.get("description", "")]
                    has_flex = raw.get("has_flex")
                    if has_flex is None:
                        has_flex = detect_flex(all_text) or None
                    listing = Listing(
                        external_id=ext_id,
                        source=raw.get("source", self.source),
                        url=raw.get("url"),
                        address=raw.get("address"),
                        neighborhood=raw.get("neighborhood"),
                        borough=raw.get("borough"),
                        beds=raw.get("beds"),
                        baths=raw.get("baths"),
                        price=raw.get("price"),
                        broker_fee=raw.get("broker_fee"),
                        broker_fee_source=raw.get("broker_fee_source"),
                        amenities_raw=amenities_raw,
                        laundry_in_unit=raw.get("laundry_in_unit", False),
                        laundry_in_building=raw.get("laundry_in_building", False),
                        dishwasher=raw.get("dishwasher", False),
                        near_subway=raw.get("near_subway", False),
                        gym=raw.get("gym", False),
                        rooftop=raw.get("rooftop", False),
                        has_flex=has_flex,
                        has_photos=raw.get("has_photos"),
                        nearest_subway=raw.get("nearest_subway"),
                        building_amenities=raw.get("building_amenities"),
                        move_in_date=raw.get("move_in_date"),
                        listed_date=raw.get("listed_date"),
                        contact_name=raw.get("contact_name"),
                        contact_email=raw.get("contact_email"),
                        contact_phone=raw.get("contact_phone"),
                        status="new",
                    )
                    session.add(listing)
                    new_count += 1
                    # Fill neighborhood via Google Maps if scraper couldn't determine it
                    if not listing.neighborhood:
                        geocode_listing(listing)

                session.flush()
                listing.pre_tour_score = score_listing(listing, preferences)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return new_count

    def _dedupe_by_address(self, listings: list[dict]) -> list[dict]:
        """Remove duplicates within a single scrape run by (address, price, beds)."""
        seen: set[tuple] = set()
        unique = []
        for l in listings:
            key = (
                (l.get("address") or "").lower().strip(),
                l.get("price"),
                l.get("beds"),
            )
            if key not in seen:
                seen.add(key)
                unique.append(l)
        return unique
