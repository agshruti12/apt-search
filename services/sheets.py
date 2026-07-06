"""Google Sheets sync service."""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import os

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import Config, load_templates
from models.db import Listing, get_session

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "ID", "Source", "URL", "Address", "Neighborhood", "Beds", "Baths",
    "Price", "Broker Fee (months)", "Broker Fee Source",
    "Laundry", "Building Amenities", "Nearest Subway",
    "Has Flex Room", "Has Photos",
    "Move-in Date", "Listed Date", "Status", "Contact Notes",
    "Contact Name", "Contact Email", "Contact Phone",
    "Pre-Tour Score", "Post-Tour Score", "Notes",
]

# Columns the user edits in the sheet; synced back to DB on pull
EDITABLE_COLS = {
    "Status": "status",
    "Contact Notes": "contact_notes",
    "Notes": "notes",
    "Pre-Tour Score": "pre_tour_score",
    "Post-Tour Score": "post_tour_score",
}

STATUS_COLORS = {
    "liked":      {"red": 0.565, "green": 0.933, "blue": 0.565},
    "passed":     {"red": 0.933, "green": 0.565, "blue": 0.565},
    "contacted":  {"red": 1.0,   "green": 0.949, "blue": 0.8},
    "touring":    {"red": 0.678, "green": 0.847, "blue": 0.902},
}


TOKEN_PATH = "token_sheets.json"


def _get_client() -> gspread.Client:
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                Config.GOOGLE_CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return gspread.authorize(creds)


def _laundry_label(listing: Listing) -> str:
    if listing.laundry_in_unit:
        return "In Unit"
    if listing.laundry_in_building:
        return "In Building"
    return "None"


def _listing_to_row(listing: Listing) -> list:
    return [
        listing.id,
        listing.source or "",
        listing.url or "",
        listing.address or "",
        listing.neighborhood or "",
        listing.beds if listing.beds is not None else "",
        listing.baths if listing.baths is not None else "",
        listing.price if listing.price is not None else "",
        listing.broker_fee if listing.broker_fee is not None else "",
        listing.broker_fee_source or "",
        _laundry_label(listing),
        listing.building_amenities or "",
        listing.nearest_subway or "",
        "Yes" if listing.has_flex else ("No" if listing.has_flex is False else "Unknown"),
        "Yes" if listing.has_photos else ("No" if listing.has_photos is False else "Unknown"),
        listing.move_in_date or "",
        listing.listed_date or "",
        listing.status or "new",
        listing.contact_notes or "",
        listing.contact_name or "",
        listing.contact_email or "",
        listing.contact_phone or "",
        listing.pre_tour_score if listing.pre_tour_score is not None else "",
        listing.post_tour_score if listing.post_tour_score is not None else "",
        listing.notes or "",
    ]


def sync_to_sheet(session: Session | None = None) -> None:
    """Push all listings from SQLite to Google Sheet. Upserts by listing ID."""
    own_session = session is None
    if own_session:
        session = get_session(Config.DB_PATH)

    try:
        client = _get_client()
        sheet = client.open_by_key(Config.GOOGLE_SHEET_ID)

        # ── Listings tab ──────────────────────────────────────────────────────
        try:
            ws = sheet.worksheet("Apartment Listings")
        except gspread.exceptions.WorksheetNotFound:
            ws = sheet.add_worksheet("Apartment Listings", rows=1000, cols=30)
            ws.update("A1", [HEADERS])
            _apply_conditional_formatting(sheet, ws)

        # Build id → row-number index from existing sheet data
        existing = ws.get_all_values()
        id_to_row: dict[int, int] = {}
        if len(existing) > 1:
            for i, row in enumerate(existing[1:], start=2):
                if row and row[0].isdigit():
                    id_to_row[int(row[0])] = i

        listings = session.query(Listing).all()
        updates: list[tuple[int, list]] = []
        appends: list[list] = []
        # Delete rows for passed listings (in reverse order to preserve row numbers)
        rows_to_delete: list[int] = []

        for listing in listings:
            if listing.status == "passed":
                if listing.id in id_to_row:
                    rows_to_delete.append(id_to_row[listing.id])
                continue
            row_data = _listing_to_row(listing)
            if listing.id in id_to_row:
                updates.append((id_to_row[listing.id], row_data))
            else:
                appends.append(row_data)

        # Delete passed rows in reverse order so row numbers stay valid
        for row_num in sorted(rows_to_delete, reverse=True):
            ws.delete_rows(row_num)

        # Batch update existing rows
        if updates:
            batch = []
            for row_num, row_data in updates:
                range_notation = f"A{row_num}"
                batch.append({"range": range_notation, "values": [row_data]})
            ws.batch_update(batch)

        # Append new rows
        if appends:
            ws.append_rows(appends, value_input_option="USER_ENTERED")

        print(f"[sheets] Synced {len(updates)} updated + {len(appends)} new + {len(rows_to_delete)} removed listings.")

        # ── Templates tab ─────────────────────────────────────────────────────
        _sync_templates_tab(sheet)

    finally:
        if own_session:
            session.close()


def pull_from_sheet(session: Session | None = None) -> None:
    """Read editable columns (Status, Notes, Scores) from sheet back into SQLite."""
    own_session = session is None
    if own_session:
        session = get_session(Config.DB_PATH)

    try:
        client = _get_client()
        sheet = client.open_by_key(Config.GOOGLE_SHEET_ID)

        try:
            ws = sheet.worksheet("Apartment Listings")
        except gspread.exceptions.WorksheetNotFound:
            print("[sheets] No sheet found to pull from.")
            return

        rows = ws.get_all_records()
        updated = 0
        for row in rows:
            listing_id = row.get("ID")
            if not listing_id:
                continue
            listing = session.get(Listing, int(listing_id))
            if listing is None:
                continue

            changed = False
            for col_name, attr in EDITABLE_COLS.items():
                val = row.get(col_name, "")
                if val == "":
                    val = None
                if attr in ("pre_tour_score", "post_tour_score") and val is not None:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        val = None
                if getattr(listing, attr) != val:
                    setattr(listing, attr, val)
                    changed = True

            if changed:
                listing.updated_at = datetime.utcnow().isoformat()
                updated += 1

        session.commit()
        print(f"[sheets] Pulled updates for {updated} listings from sheet.")

    finally:
        if own_session:
            session.close()


def _sync_templates_tab(sheet: gspread.Spreadsheet) -> None:
    templates = load_templates()
    try:
        ws = sheet.worksheet("Templates")
    except gspread.exceptions.WorksheetNotFound:
        ws = sheet.add_worksheet("Templates", rows=100, cols=6)

    headers = ["Key", "Label", "Channel", "Subject", "Body"]
    rows = [headers] + [
        [t.get("key", ""), t.get("label", ""), t.get("channel", ""),
         t.get("subject", "") or "", t.get("body", "")]
        for t in templates
    ]
    ws.clear()
    ws.update("A1", rows)


def _apply_conditional_formatting(
    sheet: gspread.Spreadsheet, ws: gspread.Worksheet
) -> None:
    """Set background color rules for the Status column (Q = index 16)."""
    sheet_id = ws.id
    status_col_index = 17  # 0-based, column R (Status)

    requests = []
    for status, color in STATUS_COLORS.items():
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(HEADERS),
                    }],
                    "booleanRule": {
                        "condition": {
                            "type": "TEXT_EQ",
                            "values": [{"userEnteredValue": status}],
                        },
                        "format": {"backgroundColor": color},
                    },
                },
                "index": 0,
            }
        })

    sheet.batch_update({"requests": requests})
