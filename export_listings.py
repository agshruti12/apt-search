"""
Export all listings from SQLite → web/public/listings.json
Run after a scrape to refresh the static site data.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from models.db import Listing, get_session
from config import Config

OUT_PATH = os.path.join(os.path.dirname(__file__), "web", "public", "listings.json")


def listing_to_dict(l: Listing) -> dict:
    return {
        "id": l.id,
        "source": l.source or "",
        "url": l.url or "",
        "address": l.address or "",
        "neighborhood": l.neighborhood or "",
        "beds": l.beds,
        "baths": l.baths,
        "price": l.price,
        "broker_fee": l.broker_fee,
        "broker_fee_source": l.broker_fee_source or "",
        "laundry_in_unit": bool(l.laundry_in_unit),
        "laundry_in_building": bool(l.laundry_in_building),
        "dishwasher": bool(l.dishwasher),
        "gym": bool(l.gym),
        "rooftop": bool(l.rooftop),
        "building_amenities": l.building_amenities or "",
        "nearest_subway": l.nearest_subway or "",
        "has_flex": l.has_flex,
        "has_photos": l.has_photos,
        "move_in_date": l.move_in_date or "",
        "listed_date": l.listed_date or "",
        "status": l.status or "new",
        "contact_name": l.contact_name or "",
        "contact_email": l.contact_email or "",
        "contact_phone": l.contact_phone or "",
        "pre_tour_score": l.pre_tour_score,
        "post_tour_score": l.post_tour_score,
        "notes": l.notes or "",
    }


def main():
    session = get_session(Config.DB_PATH)
    try:
        listings = session.query(Listing).order_by(Listing.pre_tour_score.desc()).all()
        data = [listing_to_dict(l) for l in listings]
    finally:
        session.close()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(data, f, default=str)

    print(f"[export] {len(data)} listings → {OUT_PATH}")


if __name__ == "__main__":
    main()
