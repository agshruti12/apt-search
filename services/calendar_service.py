"""Google Calendar integration — creates events only on confirmed tours."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import Config
from models.db import Listing

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = "token_calendar.json"


def _get_service():
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

    return build("calendar", "v3", credentials=creds)


def create_tour_event(listing: Listing, date: str, time: str) -> str:
    """
    Create a Google Calendar event for a confirmed tour.
    Returns the calendar event ID.
    """
    service = _get_service()

    # Parse date + time
    start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %I:%M %p")
    end_dt = start_dt + timedelta(minutes=30)

    description_parts = [
        f"Address: {listing.address}",
        f"Price: ${listing.price}/mo" if listing.price else "",
        f"Contact: {listing.contact_name}" if listing.contact_name else "",
        f"Phone: {listing.contact_phone}" if listing.contact_phone else "",
        f"Email: {listing.contact_email}" if listing.contact_email else "",
        f"URL: {listing.url}" if listing.url else "",
    ]
    description = "\n".join(p for p in description_parts if p)

    event = {
        "summary": f"Apt Tour: {listing.address}",
        "description": description,
        "location": listing.address,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/New_York",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/New_York",
        },
    }

    created = service.events().insert(
        calendarId=Config.GOOGLE_CALENDAR_ID, body=event
    ).execute()
    return created["id"]
