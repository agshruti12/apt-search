"""Tour scheduling: geocode → cluster → order → propose schedule."""
from __future__ import annotations

from datetime import datetime, timedelta

from models.db import Listing
from services.geocoder import geocode_listings


def _greedy_order(listings: list[Listing]) -> list[Listing]:
    """Greedy nearest-neighbor sort to minimize travel within a cluster."""
    if len(listings) <= 1:
        return listings

    remaining = list(listings)
    ordered = [remaining.pop(0)]
    while remaining:
        last = ordered[-1]
        nearest = min(
            remaining,
            key=lambda l: (
                ((l.lat or 0) - (last.lat or 0)) ** 2
                + ((l.lng or 0) - (last.lng or 0)) ** 2
            ),
        )
        ordered.append(nearest)
        remaining.remove(nearest)
    return ordered


def cluster_listings(listings: list[Listing]) -> dict[str, list[Listing]]:
    """Group listings by neighborhood field, falling back to K-Means on coords."""
    # Try grouping by neighborhood string
    by_neighborhood: dict[str, list[Listing]] = {}
    for l in listings:
        key = l.neighborhood or "Unknown"
        by_neighborhood.setdefault(key, []).append(l)

    # If most have neighborhoods, use that grouping
    named = sum(1 for l in listings if l.neighborhood)
    if named / max(len(listings), 1) >= 0.6:
        return by_neighborhood

    # Fall back to K-Means on (lat, lng)
    coords = [(l.lat or 0, l.lng or 0) for l in listings]
    k = min(3, len(listings))

    try:
        from sklearn.cluster import KMeans
        import numpy as np

        km = KMeans(n_clusters=k, n_init="auto", random_state=0)
        labels = km.fit_predict(np.array(coords))
        clusters: dict[str, list[Listing]] = {}
        for listing, label in zip(listings, labels):
            clusters.setdefault(f"Cluster {label + 1}", []).append(listing)
        return clusters
    except ImportError:
        # No sklearn: return single group
        return {"All": listings}


def build_schedule(
    listings: list[Listing],
    date: str,
    session=None,
) -> list[dict]:
    """
    Geocode listings, cluster, order, and assign time slots.
    Returns a list of slot dicts with keys: listing, time, neighborhood.
    """
    geocode_listings(listings)
    if session:
        session.commit()

    clusters = cluster_listings(listings)
    slots: list[dict] = []

    current_time = datetime.strptime(f"{date} 10:00", "%Y-%m-%d %H:%M")
    SLOT_DURATION = timedelta(minutes=30)
    TRAVEL_TIME = timedelta(minutes=20)

    for neighborhood, group in clusters.items():
        ordered = _greedy_order(group)
        for listing in ordered:
            slots.append({
                "listing": listing,
                "time": current_time.strftime("%I:%M %p").lstrip("0"),
                "neighborhood": neighborhood,
            })
            current_time += SLOT_DURATION + TRAVEL_TIME
        # Add a small gap between clusters
        current_time += timedelta(minutes=10)

    return slots


def print_schedule(slots: list[dict], date: str) -> None:
    print(f"\nTour Schedule — {date}")
    print("─" * 50)
    current_neighborhood = None
    for slot in slots:
        listing = slot["listing"]
        if slot["neighborhood"] != current_neighborhood:
            current_neighborhood = slot["neighborhood"]
            print(f"\n  {current_neighborhood}")
        contact = listing.contact_name or "Unknown"
        print(
            f"    {slot['time']} — {listing.address} "
            f"(${listing.price}/mo) — contact: {contact}"
        )
    print()
