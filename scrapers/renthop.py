"""
Scraper for RentHop.
Uses RentHop's native search URL format with area codes and feature filters.
Makes one request per bedroom count (not per neighborhood) using area-code grouping.
"""
from __future__ import annotations

import json
import re
import time
import random
from datetime import datetime
from urllib.parse import urlencode, quote_plus

from bs4 import BeautifulSoup

from scrapers.base import (
    BaseScraper, normalize_amenities, make_external_id, parse_price,
    extract_building_amenities, _PRICE_MIN, _PRICE_MAX,
)
from config import budget_for_beds

# RentHop area codes for Manhattan (from the user's search URLs)
# 1rgo = all Downtown Manhattan; individual codes = Midtown minus Chelsea
_AREAS_DOWNTOWN = "1rgo"
_AREAS_MIDTOWN_NO_CHELSEA = "3qyj,3qyn,3qyr,5fro,3qyt,5frp,3qzx,5frn,3r0s,2jog,3r2b"
_AREAS_ALL = f"{_AREAS_DOWNTOWN},{_AREAS_MIDTOWN_NO_CHELSEA}"

# Preference key → RentHop features[] value
_FEATURE_MAP = {
    "laundry_in_unit": "Laundry In Unit",
    "laundry_in_building": "Laundry In Building",
    "dishwasher": "Dishwasher",
    "gym": "Fitness Center",
    "no_flex_rooms": "No Flex Rooms",
}


def _build_url(beds: int, max_price: int, preferences: dict, page: int = 1) -> str:
    """Build the RentHop search URL using their exact parameter format."""
    nice = preferences.get("nice_to_haves", {})
    baths = preferences.get("baths", [2])
    min_bath = min(baths) if baths else 2

    # Build params manually to match RentHop's array encoding (features%5B%5D=...)
    parts = [
        f"bathrooms={min_bath}",
    ]

    # Add feature filters from nice_to_haves
    for pref_key, renthop_val in _FEATURE_MAP.items():
        if nice.get(pref_key):
            parts.append(f"features%5B%5D={quote_plus(renthop_val)}")

    # No Fee filter
    if preferences.get("no_broker_fee_only", False):
        parts.append("features%5B%5D=No+Fee")

    parts += [
        "q=",
        f"areas={_AREAS_ALL}",
        f"min_price={preferences.get('price_min', 0)}",
        f"max_price={max_price}",
        f"bedrooms%5B%5D={beds}",
        "sort=hopscore",
        f"page={page}",
    ]

    return "https://www.renthop.com/search?" + "&".join(parts)


class RentHopScraper(BaseScraper):
    source = "renthop"

    def scrape(self, preferences: dict) -> list[dict]:
        beds_list: list[int] = preferences.get("beds", [3, 4])
        all_listings: list[dict] = []

        for beds in beds_list:
            # Use full monthly budget since we filter for no-fee
            max_price = budget_for_beds(preferences, beds)
            page = 1
            consecutive_empty = 0

            while page <= 10:  # cap at 10 pages (~200 listings) per bed count
                url = _build_url(beds, max_price, preferences, page)
                print(f"[renthop] {beds}BR page {page}: {url}")
                try:
                    html = self._get_page_html(url, wait_for="[class*='listing'], article, .search-result")
                    listings = self._parse(html, beds, preferences.get("neighborhoods", []))
                    print(f"[renthop] Found {len(listings)} listings on page {page}")

                    if not listings:
                        consecutive_empty += 1
                        if consecutive_empty >= 2:
                            break
                    else:
                        consecutive_empty = 0
                        all_listings.extend(listings)

                    page += 1
                    time.sleep(random.uniform(2, 4))
                except Exception as e:
                    print(f"[renthop] Error on page {page}: {e}")
                    break

        deduped = self._dedupe_by_address(all_listings)
        saved = self.save_listings(deduped, preferences)
        print(f"[renthop] {saved} new listings saved.")
        return deduped

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse(self, html: str, beds: int, neighborhoods: list[str]) -> list[dict]:
        """Try __NEXT_DATA__ JSON first, then HTML cards."""
        listings = self._try_next_data(html, beds, neighborhoods)
        if listings:
            return listings
        return self._try_html_cards(html, beds, neighborhoods)

    # ── Detail page fetch ─────────────────────────────────────────────────────

    def _fetch_detail(self, url: str) -> dict:
        """
        Fetch a RentHop listing detail page and return a dict with:
          amenities_raw, laundry_in_unit, laundry_in_building, dishwasher, gym,
          rooftop, nearest_subway, building_amenities, contact_name, contact_email
        Returns an empty dict on failure.
        """
        try:
            html = self._get_page_html(url, wait_for="#nearby-transit")
        except Exception as e:
            print(f"[renthop] detail fetch failed for {url}: {e}")
            return {}

        soup = BeautifulSoup(html, "lxml")
        result: dict = {}

        # ── Amenities ─────────────────────────────────────────────────────────
        # All amenities are in <div class="col-6 mt-2"> inside a row.no-gutters
        amenity_divs = soup.find_all(
            "div", class_=lambda c: c and "col-6" in c and "mt-2" in c
        )
        amenity_strings = [d.get_text(strip=True) for d in amenity_divs if d.get_text(strip=True)]
        # Remove "No Fee" etc. — keep only actual amenity labels
        amenity_strings = [a for a in amenity_strings if a not in ("No Fee",)]

        flags = normalize_amenities(amenity_strings)
        result.update(flags)
        result["amenities_raw"] = amenity_strings
        result["building_amenities"] = extract_building_amenities(amenity_strings)

        # ── Nearest subway ────────────────────────────────────────────────────
        transit_div = soup.find(id="nearby-transit")
        if transit_div:
            result["nearest_subway"] = self._parse_nearest_transit(transit_div)

        # ── Posted date + Move-in date ────────────────────────────────────────
        # Text pattern: "Posted last hour,\n  Immediate Move-In"
        #           or: "Posted 1 day ago,\n  Jul 24 Move-In"
        #           or: "Posted 3 days ago,\n  Aug 1 Move-In"
        for div in soup.find_all("div", class_=re.compile(r"font-size-9")):
            text = div.get_text(" ", strip=True)
            posted_m = re.search(
                r"Posted\s+(last\s+hour|last\s+day|(\d+)\s+(hour|day|week|month)s?\s+ago)",
                text, re.I
            )
            if not posted_m:
                continue

            # Resolve posted date
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            raw_age = posted_m.group(0).lower()
            if "hour" in raw_age or "last hour" in raw_age:
                listed = now
            elif "last day" in raw_age or re.search(r"^posted 1 day", raw_age):
                listed = now - timedelta(days=1)
            else:
                n_m = re.search(r"(\d+)\s+(day|week|month)", raw_age)
                if n_m:
                    n, unit = int(n_m.group(1)), n_m.group(2)
                    delta = {"day": 1, "week": 7, "month": 30}[unit] * n
                    listed = now - timedelta(days=delta)
                else:
                    listed = now
            result["listed_date"] = listed.strftime("%Y-%m-%d")

            # Move-in date from same div
            move_m = re.search(r"(Immediate|[\w]+ \d{1,2})\s+Move-In", text, re.I)
            if move_m:
                raw = move_m.group(1).strip()
                if raw.lower() == "immediate":
                    result["move_in_date"] = "Immediate"
                else:
                    year = now.year
                    try:
                        dt = datetime.strptime(f"{raw} {year}", "%b %d %Y")
                        if dt.date() < now.date():
                            dt = dt.replace(year=year + 1)
                        result["move_in_date"] = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        result["move_in_date"] = raw
            break

        # ── Contact ───────────────────────────────────────────────────────────
        contact_block = soup.find(id="contact-details-block")
        if contact_block:
            name_tag = contact_block.find("div", class_="agent-name")
            if name_tag:
                result["contact_name"] = name_tag.get_text(strip=True)
            # Email is rarely shown publicly; grab if present
            email_tag = contact_block.find("a", href=re.compile(r"mailto:"))
            if email_tag:
                result["contact_email"] = email_tag["href"].replace("mailto:", "").strip()

        return result

    @staticmethod
    def _parse_nearest_transit(transit_div) -> str | None:
        """
        Return a human-readable string like 'Wall St (2/3) — 3 min walk'
        from the first transit entry in the #nearby-transit div.
        """
        entries = transit_div.find_all("div", class_="d-block mt-3")
        if not entries:
            return None
        entry = entries[0]
        # Lines: text of each .transit-nyc span
        lines = [t.get_text(strip=True) for t in entry.find_all(class_="transit-nyc")]
        lines_str = "/".join(lines) if lines else ""
        # Station name: first <span class="b">
        station_tag = entry.find("span", class_="b")
        station = station_tag.get_text(strip=True) if station_tag else ""
        # Distance: prefer "X min walk" from the parenthetical "(630 ft - 3 min walk)"
        dist_m = re.search(r"(\d+)\s*min walk", entry.get_text())
        dist = f"{dist_m.group(1)} min walk" if dist_m else ""

        parts = []
        if station:
            parts.append(station)
        if lines_str:
            parts.append(f"({lines_str})")
        if dist:
            parts.append(f"— {dist}")
        return " ".join(parts) if parts else None

    # ── Strategy 1: __NEXT_DATA__ ────────────────────────────────────────────

    def _try_next_data(self, html: str, beds: int, neighborhoods: list[str]) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag:
            return []
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            return []

        raw = self._find_listings_in_obj(data)
        return [r for r in (self._from_item(item, beds, neighborhoods) for item in raw) if r]

    def _find_listings_in_obj(self, node, depth: int = 0) -> list:
        if depth > 8:
            return []
        if isinstance(node, list) and node and isinstance(node[0], dict):
            first = node[0]
            if any(k in first for k in ("id", "address", "price", "bedrooms", "hop_score", "listing_id")):
                return node
        if isinstance(node, dict):
            for key in ("listings", "results", "data", "homes", "units", "search_results", "items"):
                if key in node:
                    result = self._find_listings_in_obj(node[key], depth + 1)
                    if result:
                        return result
            for v in node.values():
                result = self._find_listings_in_obj(v, depth + 1)
                if result:
                    return result
        return []

    def _from_item(self, item: dict, beds: int, neighborhoods: list[str]) -> dict | None:
        address = (
            item.get("address") or item.get("street_address") or
            item.get("full_address") or
            (item.get("location") or {}).get("address", "")
        )
        city = item.get("city", "New York")
        state = item.get("state", "NY")
        if city and address and city not in address:
            address = f"{address}, {city}, {state}".strip(", ")

        listing_id = str(item.get("id") or item.get("listing_id") or "")
        url = item.get("url") or item.get("listing_url") or ""
        if not url and listing_id:
            url = f"https://www.renthop.com/listings/{listing_id}"
        if url and not url.startswith("http"):
            url = "https://www.renthop.com" + url

        price = None
        p = item.get("price") or item.get("rent") or item.get("min_price") or item.get("low_price")
        if p is not None:
            if isinstance(p, (int, float)) and _PRICE_MIN < p < _PRICE_MAX:
                price = int(p)
            else:
                price = parse_price(str(p))

        baths = None
        b = item.get("baths") or item.get("bathrooms") or item.get("bath_count")
        if b is not None:
            try:
                baths = float(b)
            except (ValueError, TypeError):
                pass

        item_beds = item.get("bedrooms") or item.get("beds") or item.get("bed_count")
        if item_beds is not None:
            try:
                beds = int(item_beds)
            except (ValueError, TypeError):
                pass

        amenities_raw = item.get("amenities") or item.get("features") or []
        amenity_strings = [
            a if isinstance(a, str) else a.get("name", "") or a.get("label", "")
            for a in amenities_raw
        ]
        description = (item.get("description") or "").lower()
        amenity_strings.append(description)
        amenity_flags = normalize_amenities(amenity_strings)
        amenity_strings_clean = [s for s in amenity_strings if s != description]

        broker_fee = None
        all_text = description + " " + " ".join(amenity_strings_clean)
        if "no fee" in all_text or "no broker" in all_text:
            broker_fee = 0.0

        nbhd = (
            item.get("neighborhood") or item.get("neighborhood_name") or
            (item.get("location") or {}).get("neighborhood", "")
        )

        contact_name = item.get("contact_name") or item.get("agent_name") or item.get("landlord_name")
        contact_phone = item.get("phone") or item.get("contact_phone")
        contact_email = item.get("email") or item.get("contact_email")

        # Photos: check if photos/images array is present and non-empty
        photos = item.get("photos") or item.get("images") or item.get("media") or []
        has_photos = len(photos) > 0 if photos else None

        if not address and not url:
            return None

        ext_id = make_external_id(self.source, listing_id or url or address or "")
        result = {
            "external_id": ext_id,
            "source": self.source,
            "url": url or None,
            "address": address or None,
            "neighborhood": nbhd or None,
            "borough": "Manhattan",
            "beds": beds,
            "baths": baths,
            "price": price,
            "broker_fee": broker_fee,
            "amenities_raw": amenity_strings_clean,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "has_photos": has_photos,
            **amenity_flags,
        }
        self._apply_broker_fee_default(result)
        return result

    # ── Strategy 2: HTML card parsing ────────────────────────────────────────
    # RentHop renders server-side HTML (no __NEXT_DATA__).
    # Cards are: <div class="search-listing" id="listing-{id}" listing_id="{id}">

    def _try_html_cards(self, html: str, beds: int, neighborhoods: list[str]) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.find_all("div", class_="search-listing")
        listings = []
        for card in cards:
            try:
                listing = self._from_card(card, beds)
                if not listing:
                    continue
                # Enrich with detail-page data (amenities, transit, contact)
                if listing.get("url"):
                    detail = self._fetch_detail(listing["url"])
                    listing.update({k: v for k, v in detail.items() if v is not None})
                listings.append(listing)
                time.sleep(random.uniform(1, 2))
            except Exception as e:
                print(f"[renthop] card parse error: {e}")
                continue
        return listings

    def _from_card(self, card, beds: int) -> dict | None:
        # IDs and coordinates are on the card div itself
        listing_id = card.get("listing_id") or ""
        lat = card.get("latitude")
        lng = card.get("longitude")

        # URL: first <a> inside .search-photo (the photo link)
        photo_div = card.find("div", class_="search-photo")
        link = photo_div.find("a", href=True) if photo_div else None
        if not link:
            link = card.find("a", href=True)
        url = link["href"] if link else None
        if url and not url.startswith("http"):
            url = "https://www.renthop.com" + url

        # Address: <a class="font-size-12 b"> inside .search-info-title
        address = None
        title_div = card.find("div", class_="search-info-title")
        if title_div:
            addr_tag = title_div.find("a", class_=re.compile(r"font-size-12"))
            if addr_tag:
                address = addr_tag.get_text(strip=True)

        # Neighborhood: <div id="listing-{id}-neighborhoods">
        nbhd = None
        if listing_id:
            nbhd_div = card.find("div", id=f"listing-{listing_id}-neighborhoods")
            if nbhd_div:
                # Text is "Financial District, Downtown Manhattan, Manhattan" — take first part
                parts = nbhd_div.get_text(strip=True).split(",")
                nbhd = parts[0].strip() if parts else None

        # Price: <div id="listing-{id}-price"> contains "$7,500"
        price = None
        if listing_id:
            price_div = card.find("div", id=f"listing-{listing_id}-price")
            if price_div:
                price = parse_price(price_div.get_text())
        if price is None:
            price = parse_price(card.get_text())

        # Beds/baths: text "3 Bed" and "2 Bath" in .search-info
        info_div = card.find("div", class_="search-info")
        info_text = info_div.get_text(" ", strip=True) if info_div else ""
        bed_m = re.search(r"(\d+)\s*Bed", info_text, re.I)
        bath_m = re.search(r"([\d.]+)\s*Bath", info_text, re.I)
        if bed_m:
            try:
                beds = int(bed_m.group(1))
            except ValueError:
                pass
        baths = None
        if bath_m:
            try:
                baths = float(bath_m.group(1))
            except ValueError:
                pass

        # Broker fee: look for "No Fee" badge
        broker_fee = None
        broker_fee_source = None
        card_text = card.get_text(" ").lower()
        if "no fee" in card_text or "no broker" in card_text:
            broker_fee = 0.0
            broker_fee_source = "listed"

        # Photos: check for img.search-thumb
        has_photos = bool(card.find("img", class_="search-thumb"))

        # Contact: "By {name}" link
        contact_name = None
        by_tag = card.find("a", class_="font-blue", href=re.compile(r"/managers/"))
        if by_tag:
            contact_name = by_tag.get_text(strip=True)

        if not url and not address:
            return None

        ext_id = make_external_id(self.source, listing_id or url or address or "")
        result = {
            "external_id": ext_id,
            "source": self.source,
            "url": url,
            "address": address,
            "neighborhood": nbhd,
            "borough": "Manhattan",
            "beds": beds,
            "baths": baths,
            "price": price,
            "broker_fee": broker_fee,
            "broker_fee_source": broker_fee_source,
            "amenities_raw": [],
            "contact_name": contact_name,
            "contact_email": None,
            "contact_phone": None,
            "listed_date": datetime.utcnow().strftime("%Y-%m-%d"),
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
