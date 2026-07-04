"""Scraper for Zumper — tries __INITIAL_STATE__, __NEXT_DATA__, then HTML cards."""
from __future__ import annotations

import json
import re
import time
import random
from datetime import datetime

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, normalize_amenities, make_external_id, parse_price, _PRICE_MIN, _PRICE_MAX
from config import budget_for_beds


class ZumperScraper(BaseScraper):
    source = "zumper"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [1])
        price_min: int = preferences.get("price_min", 0)

        all_listings: list[dict] = []

        for beds in beds_list:
            price_max = budget_for_beds(preferences, beds)
            url = (
                f"https://www.zumper.com/apartments-for-rent/new-york-ny"
                f"?beds={beds}&price_min={price_min}&price_max={price_max}"
            )
            print(f"[zumper] Scraping: {url}")
            try:
                html = self._get_page_html(url)
                listings = self._parse(html, beds, preferences.get("neighborhoods", []))
                print(f"[zumper] Found {len(listings)} listings for {beds}BR")
                all_listings.extend(listings)
                time.sleep(random.uniform(2, 4))
            except Exception as e:
                print(f"[zumper] Error scraping {url}: {e}")

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[zumper] {saved} new listings saved.")
        return deduped

    def _parse(self, html: str, beds: int, preferred_neighborhoods: list[str]) -> list[dict]:
        """Try three strategies in order: __INITIAL_STATE__, __NEXT_DATA__, HTML cards."""
        listings = self._try_initial_state(html, beds, preferred_neighborhoods)
        if listings:
            return listings

        listings = self._try_next_data(html, beds, preferred_neighborhoods)
        if listings:
            return listings

        return self._try_html_cards(html, beds, preferred_neighborhoods)

    # ── Strategy 1: window.__INITIAL_STATE__ ─────────────────────────────────

    def _try_initial_state(self, html: str, beds: int, preferred_neighborhoods: list[str]) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script"):
            text = script.string or ""
            if "__INITIAL_STATE__" not in text:
                continue
            # Extract the JSON — may be followed by ; or </script>
            m = re.search(r"__INITIAL_STATE__\s*=\s*(\{.+)", text, re.DOTALL)
            if not m:
                continue
            raw = m.group(1).rstrip()
            # Trim trailing junk after the closing brace
            depth, end = 0, 0
            for i, ch in enumerate(raw):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            try:
                data = json.loads(raw[:end])
                raw_listings = self._find_listings_in_obj(data)
                return [r for r in (self._from_item(item, beds, preferred_neighborhoods) for item in raw_listings) if r]
            except (json.JSONDecodeError, Exception):
                continue
        return []

    # ── Strategy 2: __NEXT_DATA__ ────────────────────────────────────────────

    def _try_next_data(self, html: str, beds: int, preferred_neighborhoods: list[str]) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return []
        try:
            data = json.loads(tag.string or "")
            raw_listings = self._find_listings_in_obj(data)
            return [r for r in (self._from_item(item, beds, preferred_neighborhoods) for item in raw_listings) if r]
        except Exception:
            return []

    # ── Strategy 3: parse HTML listing cards ─────────────────────────────────

    def _try_html_cards(self, html: str, beds: int, preferred_neighborhoods: list[str]) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # Zumper renders cards as <article> or <li> elements with data attributes or aria labels
        cards = (
            soup.find_all("article", attrs={"data-testid": re.compile(r"listing", re.I)})
            or soup.find_all("li", attrs={"data-testid": re.compile(r"listing", re.I)})
            or soup.find_all("div", class_=re.compile(r"listing-card|PropertyCard|listingCard", re.I))
        )

        for card in cards:
            try:
                listing = self._from_card(card, beds, preferred_neighborhoods)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        return listings

    def _from_card(self, card, beds: int, preferred_neighborhoods: list[str]) -> dict | None:
        # URL + ID
        link = card.find("a", href=True)
        url = link["href"] if link else None
        if url and not url.startswith("http"):
            url = "https://www.zumper.com" + url

        # Address
        address = None
        for attr in ["data-address", "aria-label"]:
            val = card.get(attr)
            if val:
                address = val
                break
        if not address:
            addr_tag = card.find(attrs={"data-testid": re.compile(r"address", re.I)}) or \
                       card.find(class_=re.compile(r"address|street", re.I))
            if addr_tag:
                address = addr_tag.get_text(strip=True)

        # Price — require $ to avoid confusing street numbers with rent
        price = None
        price_tag = card.find(attrs={"data-testid": re.compile(r"price", re.I)}) or \
                    card.find(class_=re.compile(r"price|rent", re.I))
        if price_tag:
            price = parse_price(price_tag.get_text())

        if not url and not address:
            return None

        listing_id = re.search(r"/a(\d+)", url or "")
        ext_id = make_external_id(self.source, listing_id.group(1) if listing_id else (url or address or ""))

        neighborhood = self._match_neighborhood(address or "", preferred_neighborhoods)

        result = {
            "external_id": ext_id,
            "source": self.source,
            "url": url,
            "address": address,
            "neighborhood": neighborhood,
            "borough": None,
            "beds": beds,
            "baths": None,
            "price": price,
            "broker_fee": None,
            "amenities_raw": [],
            "contact_name": None,
            "contact_email": None,
            "contact_phone": None,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "laundry_in_unit": False,
            "laundry_in_building": False,
            "dishwasher": False,
            "near_subway": False,
            "gym": False,
            "rooftop": False,
        }
        self._apply_broker_fee_default(result)
        return result

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _find_listings_in_obj(self, node, depth: int = 0) -> list:
        """Recursively walk JSON to find the listings array."""
        if depth > 7:
            return []
        if isinstance(node, list) and len(node) > 0 and isinstance(node[0], dict):
            first = node[0]
            if any(k in first for k in ("id", "address", "price", "bedrooms", "listing_id", "zpid")):
                return node
        if isinstance(node, dict):
            # Prefer keys that sound like listing collections
            for priority_key in ("listings", "homes", "results", "units", "data"):
                if priority_key in node:
                    result = self._find_listings_in_obj(node[priority_key], depth + 1)
                    if result:
                        return result
            for v in node.values():
                result = self._find_listings_in_obj(v, depth + 1)
                if result:
                    return result
        return []

    def _from_item(self, item: dict, beds: int, preferred_neighborhoods: list[str]) -> dict | None:
        # Address
        address = (
            item.get("address") or item.get("street") or
            item.get("fullAddress") or item.get("location", {}).get("address", "")
        )
        city = item.get("city", "")
        state = item.get("state", "NY")
        if city and address and city not in address:
            address = f"{address}, {city}, {state}".strip(", ")

        # URL
        listing_id = str(item.get("id") or item.get("listing_id") or item.get("zpid") or "")
        url = item.get("url") or item.get("listing_url") or ""
        if not url and listing_id:
            url = f"https://www.zumper.com/apartments-for-rent/a{listing_id}"
        if url and not url.startswith("http"):
            url = "https://www.zumper.com" + url

        # Price — use $ parser for strings; validate numeric values directly
        price = None
        p = item.get("price") or item.get("min_price") or item.get("rent") or item.get("listed_price")
        if p is not None:
            if isinstance(p, (int, float)) and _PRICE_MIN < p < _PRICE_MAX:
                price = int(p)
            else:
                price = parse_price(str(p))

        # Baths
        baths = None
        b = item.get("baths") or item.get("bathrooms") or item.get("bath_count")
        if b is not None:
            try:
                baths = float(b)
            except (ValueError, TypeError):
                pass

        # Amenities
        amenities_raw = item.get("amenities") or item.get("features") or []
        amenity_strings = [
            a if isinstance(a, str) else a.get("name", "") or a.get("label", "")
            for a in amenities_raw
        ]
        description = (item.get("description") or "").lower()
        # Also scan description for amenity keywords
        amenity_strings.append(description)
        amenity_flags = normalize_amenities(amenity_strings)
        amenity_strings_clean = [s for s in amenity_strings if s != description]

        # Broker fee
        broker_fee = None
        all_text = description + " " + " ".join(amenity_strings_clean)
        if "no fee" in all_text or "no broker fee" in all_text:
            broker_fee = 0.0

        # Neighborhood
        neighborhood = (
            item.get("neighborhood") or item.get("neighborhood_name") or
            item.get("location", {}).get("neighborhood", "")
        )
        if not neighborhood:
            neighborhood = self._match_neighborhood(address or "", preferred_neighborhoods)

        contact_name = item.get("contact_name") or item.get("agent_name")
        contact_phone = item.get("phone") or item.get("contact_phone")
        contact_email = item.get("email") or item.get("contact_email")

        if not address and not url:
            return None

        ext_id = make_external_id(self.source, listing_id or url or address or "")

        result = {
            "external_id": ext_id,
            "source": self.source,
            "url": url or None,
            "address": address or None,
            "neighborhood": neighborhood or None,
            "borough": None,
            "beds": beds,
            "baths": baths,
            "price": price,
            "broker_fee": broker_fee,
            "amenities_raw": amenity_strings_clean,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            **amenity_flags,
        }
        self._apply_broker_fee_default(result)
        return result

    def _match_neighborhood(self, address: str, preferred: list[str]) -> str | None:
        address_lower = address.lower()
        for n in preferred:
            if n.lower() in address_lower:
                return n
        return None
