"""Listing scoring and ranking."""
from __future__ import annotations

from config import budget_for_beds
from models.db import Listing

SCORED_AMENITIES = [
    "laundry_in_unit",
    "laundry_in_building",
    "dishwasher",
    "near_subway",
    "gym",
    "rooftop",
]


def effective_monthly_cost(listing: Listing) -> float | None:
    """
    True monthly cost over a 12-month lease including amortized broker fee.
    e.g. $6,000/mo rent + 1 month fee = $6,000 * 13/12 = $6,500/mo effective
    """
    if listing.price is None:
        return None
    fee = listing.broker_fee if listing.broker_fee is not None else 1.0
    return listing.price * (1 + fee / 12)


def _neighborhood_score(neighborhood: str | None, preferred: list[str]) -> float:
    if not neighborhood:
        return 0.0
    n = neighborhood.lower()
    for p in preferred:
        if p.lower() in n or n in p.lower():
            return 1.0
    return 0.0


def _bed_bath_score(value: int | float | None, preferred: list) -> float:
    if value is None:
        return 0.0
    if value in preferred:
        return 1.0
    for p in preferred:
        if abs(value - p) == 1:
            return 0.5
    return 0.0


def score_listing(listing: Listing, prefs: dict) -> float:
    score = 0.0
    weights = prefs.get("ranking_weights", {})
    price_min = prefs.get("price_min", 0)
    budget = budget_for_beds(prefs, listing.beds or 0)

    # Price: score on effective monthly cost vs full budget
    cost = effective_monthly_cost(listing)
    if cost is not None and budget > price_min:
        price_score = 1 - (cost - price_min) / (budget - price_min)
        score += max(0.0, price_score) * weights.get("price", 0)

    # Neighborhood
    score += _neighborhood_score(
        listing.neighborhood, prefs.get("neighborhoods", [])
    ) * weights.get("neighborhood", 0)

    # Beds / baths
    score += _bed_bath_score(listing.beds, prefs.get("beds", [])) * weights.get("beds", 0)
    score += _bed_bath_score(listing.baths, prefs.get("baths", [])) * weights.get("baths", 0)

    # Amenities (binary — only add points if user wants it AND listing has it)
    nice_to_haves = prefs.get("nice_to_haves", {})
    for amenity in SCORED_AMENITIES:
        if nice_to_haves.get(amenity) and getattr(listing, amenity, False):
            score += weights.get(amenity, 0)

    return round(score * 100, 1)


def score_all(listings: list[Listing], prefs: dict) -> None:
    """Compute and assign pre_tour_score for each listing in-place."""
    for listing in listings:
        listing.pre_tour_score = score_listing(listing, prefs)
