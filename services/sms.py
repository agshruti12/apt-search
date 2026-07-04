"""Twilio SMS outreach service."""
from __future__ import annotations

from twilio.rest import Client

from config import Config


def send_sms(to: str, body: str) -> str:
    """Send an SMS and return the Twilio SID."""
    client = Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        body=body,
        from_=Config.TWILIO_FROM_NUMBER,
        to=to,
    )
    return message.sid
