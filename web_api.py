"""
Lightweight Flask API serving apartment listings from SQLite.
Run with: python3 web_api.py
Then open the web frontend (cd web && npm run dev).
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request
from flask_cors import CORS

from models.db import Listing, get_session
from config import Config

app = Flask(__name__)
CORS(app)


def _listing_to_dict(l: Listing) -> dict:
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


@app.route("/api/listings")
def get_listings():
    session = get_session(Config.DB_PATH)
    try:
        listings = session.query(Listing).order_by(Listing.pre_tour_score.desc()).all()
        return jsonify([_listing_to_dict(l) for l in listings])
    finally:
        session.close()


@app.route("/api/listings/<int:listing_id>", methods=["PATCH"])
def update_listing(listing_id: int):
    """Allow updating status, notes, post_tour_score from the UI."""
    session = get_session(Config.DB_PATH)
    try:
        listing = session.get(Listing, listing_id)
        if not listing:
            return jsonify({"error": "not found"}), 404
        data = request.get_json() or {}
        allowed = {"status", "notes", "post_tour_score"}
        for key, val in data.items():
            if key in allowed:
                setattr(listing, key, val)
        session.commit()
        return jsonify(_listing_to_dict(listing))
    finally:
        session.close()


if __name__ == "__main__":
    app.run(port=5001, debug=True)
