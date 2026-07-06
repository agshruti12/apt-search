"""
Scraper for StreetEasy.
Uses StreetEasy's internal GraphQL API (api-v6.streeteasy.com).
Launches a visible Chrome window with the user's real profile to pass
PerimeterX fingerprinting, captures the API headers, then calls the
API directly via requests for each bedroom count.
"""
from __future__ import annotations

import os
import random
import shutil
import tempfile
import time
import uuid
from datetime import datetime

import requests
from playwright.sync_api import sync_playwright

from scrapers.base import (
    BaseScraper, make_external_id,
    _PRICE_MIN, _PRICE_MAX,
)
from config import budget_for_beds

# StreetEasy area codes: 102 = All Downtown Manhattan, 119 = All Midtown Manhattan
_AREA_CODES = [102, 119]

# Manhattan bounding box
_BOUNDING_BOX = {
    "topLeft": {"latitude": 40.771, "longitude": -74.054},
    "bottomRight": {"latitude": 40.694, "longitude": -73.916},
}

_GRAPHQL_URL = "https://api-v6.streeteasy.com/"

# availableAt is the only extra field confirmed available on the search type
_GRAPHQL_QUERY = """
  query GetListingRental($input: SearchRentalsInput!) {
    searchRentals(input: $input) {
      search { criteria }
      totalCount
      edges {
        ... on OrganicRentalEdge {
          node {
            id areaName bedroomCount buildingType
            fullBathroomCount halfBathroomCount
            geoPoint { latitude longitude }
            leadMedia { photo { key } }
            price totalMonthlyPrice
            sourceGroupLabel status
            street unit urlPath tier
            availableAt
          }
        }
        ... on FeaturedRentalEdge {
          node {
            id areaName bedroomCount buildingType
            fullBathroomCount halfBathroomCount
            geoPoint { latitude longitude }
            leadMedia { photo { key } }
            price totalMonthlyPrice
            sourceGroupLabel status
            street unit urlPath tier
            availableAt
          }
        }
      }
    }
  }
"""

# Real Chrome paths on macOS
_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CHROME_PROFILE = os.path.expanduser("~/Library/Application Support/Google/Chrome")


class StreetEasyScraper(BaseScraper):
    source = "streeteasy"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [3, 4])

        # One browser launch to get valid PerimeterX-authenticated headers
        session_headers = self._get_api_headers(preferences, beds_list[0])
        if not session_headers:
            print("[streeteasy] Aborting — could not get API headers.")
            return []

        all_listings: list[dict] = []
        for beds in beds_list:
            max_price = budget_for_beds(preferences, beds)
            listings = self._query_api(beds, max_price, [], session_headers)
            print(f"[streeteasy] {beds}BR: {len(listings)} listings from API")
            all_listings.extend(listings)
            time.sleep(random.uniform(1, 2))

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[streeteasy] {saved} new listings saved.")
        return deduped

    # ── API session bootstrap ─────────────────────────────────────────────────

    def _get_api_headers(self, preferences: dict, beds: int) -> dict:
        """
        Load the StreetEasy search page using the user's real Chrome profile so
        PerimeterX sees a genuine browser fingerprint. A Chrome window will
        briefly appear and close. Returns captured GraphQL headers, or {}.
        """
        max_price = budget_for_beds(preferences, beds)
        url = self._build_search_url(beds, max_price)
        captured: dict = {}

        tmp_profile = tempfile.mkdtemp(prefix="se_chrome_")
        try:
            default_src = os.path.join(_CHROME_PROFILE, "Default")
            default_dst = os.path.join(tmp_profile, "Default")
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
                        "--window-size=1280,800",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                page = ctx.new_page()

                def on_request(request):
                    if "api-v6.streeteasy.com" in request.url and not captured:
                        captured.update(dict(request.headers))

                page.on("request", on_request)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(6000)
                except Exception:
                    pass
                ctx.close()

        finally:
            shutil.rmtree(tmp_profile, ignore_errors=True)

        if not captured:
            print("[streeteasy] Warning: page blocked by PerimeterX — no API headers captured.")
        return captured

    # ── GraphQL query ─────────────────────────────────────────────────────────

    def _query_api(
        self,
        beds: int,
        max_price: int,
        amenities: list[str],
        headers: dict,
    ) -> list[dict]:
        payload = {
            "query": _GRAPHQL_QUERY,
            "variables": {
                "input": {
                    "filters": {
                        "rentalStatus": "ACTIVE",
                        "areas": _AREA_CODES,
                        "price": {"lowerBound": None, "upperBound": max_price},
                        "bedrooms": {"lowerBound": beds, "upperBound": beds},
                        "bathrooms": {"lowerBound": 1, "upperBound": None},
                        "boundingBox": _BOUNDING_BOX,
                        "amenities": amenities,
                    },
                    "page": 1,
                    "perPage": 500,
                    "sorting": {
                        "attribute": "RECOMMENDED",
                        "direction": "DESCENDING",
                    },
                    "userSearchToken": str(uuid.uuid4()),
                    "adStrategy": "NONE",
                }
            },
        }

        try:
            resp = requests.post(
                _GRAPHQL_URL,
                json=payload,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            edges = data.get("data", {}).get("searchRentals", {}).get("edges", [])
            return [
                r for r in (self._from_node(e.get("node", {})) for e in edges)
                if r
            ]
        except Exception as e:
            print(f"[streeteasy] API error for {beds}BR: {e}")
            return []

    # ── Listing normalisation ─────────────────────────────────────────────────

    def _from_node(self, node: dict) -> dict | None:
        listing_id = str(node.get("id") or "")
        url_path = node.get("urlPath") or ""
        url = f"https://streeteasy.com{url_path}" if url_path else None

        street = node.get("street") or ""
        unit = node.get("unit") or ""
        address = f"{street} #{unit}".strip("# ") if unit else street
        if address:
            address = f"{address}, New York, NY"

        price_raw = node.get("price") or node.get("totalMonthlyPrice")
        price = None
        if price_raw is not None:
            try:
                p = int(price_raw)
                price = p if _PRICE_MIN < p < _PRICE_MAX else None
            except (ValueError, TypeError):
                pass

        beds = node.get("bedroomCount")
        full_baths = node.get("fullBathroomCount") or 0
        half_baths = node.get("halfBathroomCount") or 0
        baths = full_baths + 0.5 * half_baths if (full_baths or half_baths) else None

        neighborhood = node.get("areaName") or ""
        contact_name = node.get("sourceGroupLabel") or None

        has_photos = bool(
            node.get("leadMedia") and node["leadMedia"].get("photo", {}).get("key")
        )

        # Move-in date from availableAt ("2026-08-01" format)
        move_in_date = None
        available_at = node.get("availableAt")
        if available_at:
            try:
                dt = datetime.strptime(available_at[:10], "%Y-%m-%d")
                move_in_date = dt.strftime("%Y-%m-%d")
            except ValueError:
                move_in_date = available_at[:10]

        if not address and not url:
            return None

        ext_id = make_external_id(self.source, listing_id or url or address)
        result = {
            "external_id": ext_id,
            "source": self.source,
            "url": url,
            "address": address,
            "neighborhood": neighborhood,
            "borough": "Manhattan",
            "beds": beds,
            "baths": baths,
            "price": price,
            "broker_fee": None,
            "amenities_raw": [],
            "contact_name": contact_name,
            "contact_email": None,
            "contact_phone": None,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "move_in_date": move_in_date,
            "has_photos": has_photos,
            "laundry_in_unit": False,
            "laundry_in_building": False,
            "dishwasher": False,
            "near_subway": False,
            "gym": False,
            "rooftop": False,
        }
        self._apply_broker_fee_default(result)
        return result

    # ── URL builder ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_search_url(beds: int, max_price: int) -> str:
        return (
            f"https://streeteasy.com/for-rent/nyc/"
            f"price:-{max_price}%7Carea:102,119%7Cbeds:{beds}"
            f"%7Cbaths%3E=1%7Cin_rect:40.694,40.771,-74.054,-73.916"
            f"%7Camenities:washer_dryer,dishwasher?sort_by=se_score"
        )
