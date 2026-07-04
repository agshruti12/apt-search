"""Scraper for Apartments.com."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import random
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, BrowserContext

from scrapers.base import (
    BaseScraper, normalize_amenities, make_external_id, parse_price,
    extract_building_amenities,
)
from config import budget_for_beds

_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CHROME_PROFILE = os.path.expanduser("~/Library/Application Support/Google/Chrome")

# Primary area in the path; additional areas in the n= param
_PRIMARY_AREA = "east-village-new-york-ny"
_ADDITIONAL_AREAS = [
    "financial-district_new-york_ny",
    "midtown-manhattan_new-york_ny",
    "chelsea_new-york_ny",
    "kips-bay_new-york_ny",
    "nomad_new-york_ny",
    "murray-hill_new-york_ny",
    "gramercy-park_new-york_ny",
    "hells-kitchen_new-york_ny",
]

# Amenity slug map — fitness center intentionally omitted (nice-to-have, shrinks pool)
_AMENITY_SLUGS = {
    "dishwasher": "dishwasher",
    "fitness_center": "fitness-center",  # reference only, not added to URLs
    "laundry": "laundry-facilities",
}


def _build_url(beds: int, min_baths: int, max_price: int, amenity_slugs: list[str]) -> str:
    """
    Construct an Apartments.com search URL using broad Manhattan area slugs.

    Format:
      /downtown-manhattan-new-york-ny/min-{beds}-bedrooms-{baths}-bathrooms-under-{price}/
      {amenity1-amenity2}/?bb=...&n=midtown-manhattan_new-york_ny+...
    """
    filter_segment = f"min-{beds}-bedrooms-{min_baths}-bathrooms-under-{max_price}"
    unique_slugs = sorted(set(amenity_slugs))
    amenity_segment = "-".join(unique_slugs) + "/" if unique_slugs else ""

    url = f"https://www.apartments.com/{_PRIMARY_AREA}/{filter_segment}/{amenity_segment}"
    url += "?n=" + "+".join(_ADDITIONAL_AREAS)
    return url


def _parse_move_in(text: str) -> str | None:
    """Convert apartments.com availability text to a move-in date string."""
    t = text.strip()
    if not t:
        return None
    if "now" in t.lower():
        return "Immediate"
    # Try to parse a date like "Jul 15, 2026" or "August 1, 2026"
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return t  # Return as-is if we can't parse it


class ApartmentsComScraper(BaseScraper):
    source = "apartments_com"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [1])
        min_baths: int = preferences.get("min_baths", 2)
        nice = preferences.get("nice_to_haves", {})

        amenity_slugs: list[str] = []
        if nice.get("dishwasher"):
            amenity_slugs.append(_AMENITY_SLUGS["dishwasher"])
        if nice.get("laundry_in_unit") or nice.get("laundry_in_building"):
            amenity_slugs.append(_AMENITY_SLUGS["laundry"])

        all_listings: list[dict] = []

        # Single Chrome session for the entire scrape (search + all detail pages)
        tmp_profile = tempfile.mkdtemp(prefix="apts_chrome_")
        try:
            self._copy_chrome_profile(tmp_profile)

            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=tmp_profile,
                    executable_path=_CHROME_BINARY,
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-extensions",
                        "--window-size=1280,900",
                    ],
                    ignore_default_args=["--enable-automation"],
                )

                for beds in beds_list:
                    max_price = budget_for_beds(preferences, beds)
                    search_url = _build_url(beds, min_baths, max_price, amenity_slugs)
                    print(f"[apartments.com] Scraping {beds}BR: {search_url}")
                    try:
                        cards = self._fetch_search_cards(ctx, search_url)
                        print(f"[apartments.com] {beds}BR: {len(cards)} cards found")

                        for card_data in cards:
                            listing = dict(card_data)  # copy
                            if listing.get("url"):
                                detail = self._fetch_detail(ctx, listing["url"])
                                listing.update({k: v for k, v in detail.items() if v is not None})
                            all_listings.append(listing)
                            time.sleep(random.uniform(1, 2))

                    except Exception as e:
                        print(f"[apartments.com] Error scraping {beds}BR: {e}")

                ctx.close()
        finally:
            shutil.rmtree(tmp_profile, ignore_errors=True)

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[apartments.com] {saved} new listings saved.")
        return deduped

    # ── Chrome helpers ────────────────────────────────────────────────────────

    def _copy_chrome_profile(self, tmp_dir: str) -> None:
        default_src = os.path.join(_CHROME_PROFILE, "Default")
        default_dst = os.path.join(tmp_dir, "Default")
        if os.path.isdir(default_src):
            shutil.copytree(
                default_src, default_dst,
                ignore=shutil.ignore_patterns(
                    "Lock", "SingletonLock", "SingletonCookie",
                    "*.log", "*.ldb", "IndexedDB", "GPUCache",
                    "ShaderCache", "DawnCache", "Code Cache",
                ),
                dirs_exist_ok=True,
            )

    def _navigate(self, ctx: BrowserContext, url: str, wait_selector: str, timeout: int = 15000) -> str:
        """Navigate to url in a new tab, wait for selector, return HTML."""
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                # Akamai fires location.reload() after JS challenge (~2-5s); wait for real content
                page.wait_for_selector(wait_selector, timeout=timeout)
            except Exception:
                pass
            html = page.content()
        finally:
            page.close()
        return html

    # ── Search page ───────────────────────────────────────────────────────────

    def _fetch_search_cards(self, ctx: BrowserContext, url: str) -> list[dict]:
        html = self._navigate(ctx, url, wait_selector="article[data-listingid]")
        soup = BeautifulSoup(html, "lxml")
        cards = soup.find_all("article", attrs={"data-listingid": True})
        results = []
        for card in cards:
            try:
                listing = self._parse_card(card)
                if listing:
                    results.append(listing)
            except Exception:
                continue
        return results

    def _parse_card(self, card) -> dict | None:
        url = card.get("data-url") or ""
        listing_id = card.get("data-listingid") or ""

        addr_el = card.find(class_="property-address")
        if addr_el:
            address = addr_el.get_text(strip=True)
        else:
            street = card.get("data-streetaddress", "")
            unit = card.get("data-unitnumber", "")
            address = f"{street} #{unit}, New York, NY".strip("# ") if unit else f"{street}, New York, NY"

        price_el = card.find(class_="priceTextBox")
        price = parse_price(price_el.get_text() if price_el else "")

        beds_el = card.find(class_="bedTextBox")
        beds = self._parse_beds(beds_el.get_text() if beds_el else "")

        phone_el = card.find("button", class_=lambda c: c and "phone" in c)
        contact_phone = phone_el.get_text(strip=True) if phone_el else None

        has_photos = bool(card.find("img"))

        if not url and not address:
            return None

        return {
            "external_id": make_external_id(self.source, listing_id or url or address),
            "source": self.source,
            "url": url or None,
            "address": address,
            "neighborhood": "",   # filled by detail page (title tag has it)
            "borough": "Manhattan",
            "beds": beds,
            "baths": None,        # filled by detail page
            "price": price,
            "broker_fee": None,
            "broker_fee_source": "assumed",
            "amenities_raw": [],
            "building_amenities": None,
            "nearest_subway": None,
            "contact_name": None,
            "contact_email": None,
            "contact_phone": contact_phone,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "move_in_date": None,
            "has_photos": has_photos,
            "laundry_in_unit": False,
            "laundry_in_building": False,
            "dishwasher": False,
            "near_subway": False,
            "gym": False,
            "rooftop": False,
        }

    # ── Detail page ───────────────────────────────────────────────────────────

    def _fetch_detail(self, ctx: BrowserContext, url: str) -> dict:
        try:
            html = self._navigate(ctx, url, wait_selector=".rentInfoLabel", timeout=15000)
        except Exception as e:
            print(f"[apartments.com] detail fetch failed: {url} — {e}")
            return {}
        return self._parse_detail(html)

    def _parse_detail(self, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")

        # ── Beds / baths / move-in from rentInfoLabel → rentInfoDetail pairs ─
        baths = None
        move_in_date = None
        for label_el in soup.find_all(class_="rentInfoLabel"):
            label = label_el.get_text(strip=True).lower()
            detail_el = label_el.find_next_sibling(class_="rentInfoDetail")
            if not detail_el:
                continue
            value = detail_el.get_text(strip=True)
            if label == "bathrooms":
                baths = self._parse_baths_range(value)
            elif label == "available":
                move_in_date = _parse_move_in(value)

        # ── Neighborhood from "Learn more about living in X" link ─────────────
        neighborhood = ""
        for a in soup.find_all("a", href=lambda h: h and "/local-guide/" in h and "new-york" in h):
            txt = a.get_text(strip=True)
            prefix = "Learn more about living in "
            if txt.startswith(prefix):
                neighborhood = txt[len(prefix):].strip()
                break

        # ── Amenities from .specInfo items + description text ─────────────────
        amenity_strings = [
            el.get_text(strip=True)
            for el in soup.find_all(class_="specInfo")
            if el.get_text(strip=True)
        ]
        amenity_flags = normalize_amenities(amenity_strings)
        building_amenities = extract_building_amenities(amenity_strings)

        desc_el = soup.find(class_="descriptionWrapper") or soup.find(class_="description")
        if desc_el:
            desc_flags = normalize_amenities([desc_el.get_text(strip=True)])
            for k, v in desc_flags.items():
                if v:
                    amenity_flags[k] = True

        # ── Broker fee from the Fees and Policies section ─────────────────────
        # Look for a feeName row containing "Broker Fee" and read its dollar amount.
        # "Broker Fee $0" → no fee; any other amount → has fee (amount unknown in months);
        # absent entirely → unknown.
        broker_fee: float | None = None
        broker_fee_source = "assumed"
        for fee_el in soup.find_all(class_="feeName"):
            if "broker" in fee_el.get_text(strip=True).lower():
                row = fee_el.find_parent(class_=lambda c: c and "fee" in c.lower())
                row_text = row.get_text(" ", strip=True) if row else ""
                m = re.search(r"\$\s*([\d,]+)", row_text)
                if m:
                    amount = int(m.group(1).replace(",", ""))
                    broker_fee = 0.0 if amount == 0 else None
                    broker_fee_source = "listed"
                break

        # ── Contact phone from tel: links ─────────────────────────────────────
        contact_phone = None
        for a in soup.find_all("a", href=lambda h: h and h.startswith("tel:")):
            txt = re.sub(r"^call\s*", "", a.get_text(strip=True), flags=re.I)
            if re.search(r"\d{3}.*\d{4}", txt):
                contact_phone = txt
                break

        result: dict = {
            "baths": baths,
            "move_in_date": move_in_date,
            "amenities_raw": amenity_strings,
            "building_amenities": building_amenities or None,
            "broker_fee": broker_fee,
            "broker_fee_source": broker_fee_source,
            "contact_phone": contact_phone,
            **amenity_flags,
        }
        if neighborhood:
            result["neighborhood"] = neighborhood
        return result

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_beds(self, text: str) -> int | None:
        m = re.search(r"(\d+)\s*bed", text, re.I)
        return int(m.group(1)) if m else None

    def _parse_baths_range(self, text: str) -> float | None:
        """Parse '2', '1 - 2', or '1 - 2 ba' → take the max value."""
        nums = re.findall(r"[\d]+(?:\.\d+)?", text)
        if not nums:
            return None
        return float(max(nums, key=float))

    def _infer_borough(self, neighborhood: str) -> str:
        queens = ["astoria", "long island city", "jackson heights", "flushing", "lic"]
        brooklyn = ["williamsburg", "park slope", "bushwick", "brooklyn heights", "dumbo"]
        bronx = ["riverdale", "fordham", "mott haven"]
        n = neighborhood.lower()
        if any(q in n for q in queens): return "Queens"
        if any(b in n for b in brooklyn): return "Brooklyn"
        if any(b in n for b in bronx): return "Bronx"
        return "Manhattan"
