"""Geocoding service — Google Maps (primary) with Nominatim fallback."""
from __future__ import annotations

import time
from typing import TypedDict

from config import Config
from models.db import Listing


class GeoResult(TypedDict):
    lat: float
    lng: float
    neighborhood: str | None


def geocode_address(address: str) -> GeoResult | None:
    """
    Geocode an address string. Returns lat, lng, and neighborhood.
    Uses Google Maps API if GOOGLE_MAPS_API_KEY is set, otherwise Nominatim.
    """
    if Config.GOOGLE_MAPS_API_KEY:
        return _geocode_google(address)
    return _geocode_nominatim(address)


def _geocode_google(address: str) -> GeoResult | None:
    import googlemaps
    gmaps = googlemaps.Client(key=Config.GOOGLE_MAPS_API_KEY)
    try:
        results = gmaps.geocode(address)
    except Exception as e:
        print(f"[geocoder] Google Maps error for '{address}': {e}")
        return None

    if not results:
        return None

    result = results[0]
    lat = result["geometry"]["location"]["lat"]
    lng = result["geometry"]["location"]["lng"]

    # Extract neighborhood from address_components in priority order
    neighborhood = None
    for component in result.get("address_components", []):
        types = component["types"]
        if "neighborhood" in types:
            neighborhood = component["long_name"]
            break
        if "sublocality_level_1" in types and neighborhood is None:
            neighborhood = component["long_name"]
        if "sublocality" in types and neighborhood is None:
            neighborhood = component["long_name"]

    return GeoResult(lat=lat, lng=lng, neighborhood=neighborhood)


def _geocode_nominatim(address: str) -> GeoResult | None:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut

    geolocator = Nominatim(user_agent="nyc-apt-agent/1.0")
    query = address if "New York" in address else f"{address}, New York, NY"
    try:
        time.sleep(1.1)  # Nominatim rate limit: 1 req/s
        location = geolocator.geocode(query, timeout=10)
        if location:
            return GeoResult(lat=location.latitude, lng=location.longitude, neighborhood=None)
    except GeocoderTimedOut:
        pass
    return None


def geocode_listing(listing: Listing) -> bool:
    """
    Geocode a listing in-place. Fills lat, lng, and neighborhood (if missing).
    Returns True if coordinates were updated.
    """
    if listing.lat and listing.lng and listing.neighborhood:
        return False  # already complete

    if not listing.address:
        return False

    result = geocode_address(listing.address)
    if not result:
        return False

    listing.lat = result["lat"]
    listing.lng = result["lng"]

    if not listing.neighborhood and result.get("neighborhood"):
        listing.neighborhood = result["neighborhood"]

    return True


def geocode_listings(listings: list[Listing]) -> None:
    """Geocode a list of listings in-place, skipping already-complete ones."""
    to_geocode = [l for l in listings if not (l.lat and l.lng)]
    if not to_geocode:
        return

    method = "Google Maps" if Config.GOOGLE_MAPS_API_KEY else "Nominatim (1 req/s)"
    print(f"[geocoder] Geocoding {len(to_geocode)} listings via {method}…")
    for listing in to_geocode:
        geocode_listing(listing)
