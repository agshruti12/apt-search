"""Scraper for Trulia — parses __NEXT_DATA__ JSON blob."""
from __future__ import annotations

import json
import re
import time
import random
from datetime import datetime

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, normalize_amenities, make_external_id
from config import budget_for_beds


class TruliaScraper(BaseScraper):
    source = "trulia"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [1])
        price_min: int = preferences.get("price_min", 0)
        neighborhoods: list[str] = preferences.get("neighborhoods", [])

        all_listings: list[dict] = []

        for neighborhood in neighborhoods:
            for beds in beds_list:
                price_max = budget_for_beds(preferences, beds)
                price_range = f"{price_min}-{price_max}"
                url = (
                    f"https://www.trulia.com/for_rent/New_York,NY/"
                    f"{price_range}/{beds}bd/"
                    f"?search%5BcurrentPage%5D=1"
                )
                print(f"[trulia] Scraping: {url}")
                try:
                    html = self._get_page_html(url)
                    listings = self._parse_next_data(html, neighborhood, beds)
                    all_listings.extend(listings)
                    time.sleep(random.uniform(2, 4))
                except Exception as e:
                    print(f"[trulia] Error scraping {url}: {e}")

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[trulia] {saved} new listings saved.")
        return deduped

    def _parse_next_data(self, html: str, neighborhood: str, beds: int) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        next_data_tag = soup.find("script", id="__NEXT_DATA__")
        if not next_data_tag:
            return []

        try:
            data = json.loads(next_data_tag.string or "")
        except json.JSONDecodeError:
            return []

        # Walk the nested structure to find listings
        props = data.get("props", {}).get("pageProps", {})
        search_results = (
            props.get("searchResults", {})
            or props.get("initialReduxState", {}).get("trulia/search/SEARCH", {})
        )

        # Try several known nesting patterns
        raw_listings = (
            search_results.get("homes", [])
            or search_results.get("listings", [])
            or self._walk_for_listings(search_results)
        )

        listings = []
        for item in raw_listings:
            listing = self._from_trulia_item(item, neighborhood, beds)
            if listing:
                listings.append(listing)
        return listings

    def _walk_for_listings(self, node, depth: int = 0) -> list:
        if depth > 5:
            return []
        if isinstance(node, list) and node and isinstance(node[0], dict):
            if "location" in node[0] or "price" in node[0] or "listingId" in node[0]:
                return node
        if isinstance(node, dict):
            for v in node.values():
                result = self._walk_for_listings(v, depth + 1)
                if result:
                    return result
        return []

    def _from_trulia_item(self, item: dict, neighborhood: str, beds: int) -> dict | None:
        # Address
        location = item.get("location", {})
        address_obj = location.get("formattedAddress") or ""
        if isinstance(address_obj, dict):
            address = address_obj.get("fullAddress", "")
        else:
            address = str(address_obj)

        # URL
        url = item.get("url") or item.get("listingUrl") or ""
        if url and not url.startswith("http"):
            url = "https://www.trulia.com" + url

        # Price
        price = None
        price_data = item.get("price") or item.get("listingPrice", {})
        if isinstance(price_data, dict):
            p = price_data.get("price") or price_data.get("calloutPrice")
            if p:
                nums = re.findall(r"\d+", str(p).replace(",", ""))
                if nums:
                    price = int(nums[0])
        elif isinstance(price_data, (int, float)):
            price = int(price_data)

        # Baths
        baths = None
        floorplan = item.get("floorPlan", {}) or item.get("beds", {})
        bath_val = (
            floorplan.get("baths") or floorplan.get("bathrooms")
            or item.get("baths") or item.get("bathrooms")
        )
        if bath_val is not None:
            try:
                baths = float(bath_val)
            except (ValueError, TypeError):
                pass

        # Amenities
        amenity_strings = [
            a.get("description", "") if isinstance(a, dict) else str(a)
            for a in (item.get("amenities") or item.get("tags") or [])
        ]
        amenity_flags = normalize_amenities(amenity_strings)

        if not address and not url:
            return None

        listing_id = item.get("listingId") or item.get("id") or url
        external_id = make_external_id(self.source, str(listing_id))

        result = {
            "external_id": external_id,
            "source": self.source,
            "url": url or None,
            "address": address or None,
            "neighborhood": neighborhood,
            "borough": None,
            "beds": beds,
            "baths": baths,
            "price": price,
            "broker_fee": None,
            "amenities_raw": amenity_strings,
            "contact_name": None,
            "contact_email": None,
            "contact_phone": None,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            **amenity_flags,
        }
        self._apply_broker_fee_default(result)
        return result
