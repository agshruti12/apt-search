import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent


class Config:
    # Google
    GOOGLE_CREDENTIALS_PATH: str = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    # Twilio
    TWILIO_ACCOUNT_SID: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER: str = os.getenv("TWILIO_FROM_NUMBER", "")

    # Google Maps (for geocoding + neighborhood lookup)
    GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")

    # User contact
    USER_PHONE: str = os.getenv("USER_PHONE", "")
    USER_EMAIL: str = os.getenv("USER_EMAIL", "")
    USER_NAME: str = os.getenv("USER_NAME", "")

    # Paths
    DB_PATH: str = str(BASE_DIR / "data" / "apartments.db")
    PREFERENCES_PATH: str = str(BASE_DIR / "preferences.json")
    TEMPLATES_PATH: str = str(BASE_DIR / "templates" / "messages.json")


def load_preferences() -> dict[str, Any]:
    path = Config.PREFERENCES_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"preferences.json not found at {path}. "
            "Copy preferences.json.example to preferences.json and fill it in."
        )
    with open(path) as f:
        return json.load(f)


def load_templates() -> list[dict]:
    path = Config.TEMPLATES_PATH
    if not Path(path).exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_templates(templates: list[dict]) -> None:
    path = Config.TEMPLATES_PATH
    with open(path, "w") as f:
        json.dump(templates, f, indent=2)


def budget_for_beds(prefs: dict, beds: int) -> int:
    """Total monthly budget (no broker fee assumed) — used for no-fee sites."""
    by_beds = prefs.get("monthly_budget_by_beds", {})
    return (
        by_beds.get(beds)
        or by_beds.get(str(beds))
        or price_max_for_beds(prefs, beds)
    )


def price_max_for_beds(prefs: dict, beds: int) -> int:
    """Return the per-bedroom price ceiling from preferences."""
    by_beds = prefs.get("price_max_by_beds", {})
    return (
        by_beds.get(beds)
        or by_beds.get(str(beds))
        or prefs.get("price_max", 9999)
    )
