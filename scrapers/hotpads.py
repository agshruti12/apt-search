"""Scraper for HotPads — parses JSON-LD structured data."""
from __future__ import annotations

import json
import re
import time
import random
from datetime import datetime

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, normalize_amenities, make_external_id
from config import budget_for_beds


class HotPadsScraper(BaseScraper):
    source = "hotpads"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [1])
        price_min: int = preferences.get("price_min", 0)
        neighborhoods: list[str] = preferences.get("neighborhoods", [])

        all_listings: list[dict] = []

        for neighborhood in neighborhoods:
            for beds in beds_list:
                price_max = budget_for_beds(preferences, beds)
                beds_param = f"{beds}bd"
                url = (
                    f"https://hotpads.com/new-york-ny/apartments-for-rent"
                    f"?beds={beds_param}&price={price_min}-{price_max}"
                    f"&q={neighborhood.replace(' ', '+')}"
                )
                print(f"[hotpads] Scraping: {url}")
                try:
                    html = self._get_page_html(url)
                    listings = self._parse_page(html, neighborhood, beds)
                    all_listings.extend(listings)
                    time.sleep(random.uniform(2, 4))
                except Exception as e:
                    print(f"[hotpads] Error scraping {url}: {e}")

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[hotpads] {saved} new listings saved.")
        return deduped

    def _parse_page(self, html: str, neighborhood: str, beds: int) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        listings = []

        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        listing = self._from_json_ld(item, neighborhood, beds)
                        if listing:
                            listings.append(listing)
                elif isinstance(data, dict):
                    listing = self._from_json_ld(data, neighborhood, beds)
                    if listing:
                        listings.append(listing)
            except json.JSONDecodeError:
                continue

        return listings

    def _from_json_ld(self, data: dict, neighborhood: str, beds: int) -> dict | None:
        if data.get("@type") not in ("Apartment", "ApartmentComplex", "LodgingBusiness", "Residence"):
            return None

        address_obj = data.get("address", {})
        street = address_obj.get("streetAddress", "")
        city = address_obj.get("addressLocality", "")
        state = address_obj.get("addressRegion", "")
        address = f"{street}, {city}, {state}".strip(", ")

        url = data.get("url") or data.get("@id")
        if url and not url.startswith("http"):
            url = "https://hotpads.com" + url

        # Price from offers
        price = None
        offers = data.get("offers", {})
        if isinstance(offers, dict):
            price_str = str(offers.get("price", "") or offers.get("lowPrice", ""))
            nums = re.findall(r"\d+", price_str.replace(",", ""))
            if nums:
                price = int(nums[0])

        # Amenities
        amenity_list = data.get("amenityFeature", [])
        amenity_strings = [a.get("name", "") for a in amenity_list if isinstance(a, dict)]
        amenity_flags = normalize_amenities(amenity_strings)

        # Broker fee
        description = data.get("description", "").lower()
        broker_fee = None
        if "no fee" in description or "no broker" in description:
            broker_fee = 0.0
        elif "one month" in description or "1 month" in description:
            broker_fee = 1.0

        external_id = make_external_id(self.source, url or address or "")

        result = {
            "external_id": external_id,
            "source": self.source,
            "url": url,
            "address": address or None,
            "neighborhood": neighborhood,
            "borough": None,
            "beds": beds,
            "baths": None,
            "price": price,
            "broker_fee": broker_fee,
            "amenities_raw": amenity_strings,
            "contact_name": None,
            "contact_email": None,
            "contact_phone": None,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            **amenity_flags,
        }
        self._apply_broker_fee_default(result)
        return result
