"""Single-login Google OAuth for Calendar + Tasks stored outside the repo."""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]


def secrets_dir() -> Path:
    override = os.environ.get("COUNTDOWN_SECRETS_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "countdown-planner"


def get_credentials(credentials_path: Path | None = None, token_path: Path | None = None) -> Credentials:
    base = secrets_dir()
    credentials_path = credentials_path or base / "credentials.json"
    token_path = token_path or base / "token.json"

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES) if token_path.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(f"Missing Desktop App OAuth JSON at {credentials_path}")
            creds = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES).run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
