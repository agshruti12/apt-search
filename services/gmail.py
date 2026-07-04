"""Gmail outreach service."""
from __future__ import annotations

import base64
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import Config

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TOKEN_PATH = "token_gmail.json"


def _get_service():
    creds = None
    import os
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

    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email and return the Gmail message ID."""
    service = _get_service()
    message = MIMEText(body)
    message["to"] = to
    message["from"] = Config.USER_EMAIL
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    sent = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return sent["id"]
