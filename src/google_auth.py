"""Single-login Google OAuth for Calendar + Tasks stored outside the repo."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]

_ORIG_GETADDRINFO = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    infos = _ORIG_GETADDRINFO(*args, **kwargs)
    return sorted(infos, key=lambda item: 0 if item[0] == socket.AF_INET else 1)


def prefer_ipv4() -> None:
    # httplib2 aborts on IPv6 connect timeout without trying IPv4; sort AF_INET first while keeping IPv6 fallback.
    if socket.getaddrinfo is not _ipv4_first_getaddrinfo:
        socket.getaddrinfo = _ipv4_first_getaddrinfo


def secrets_dir() -> Path:
    override = os.environ.get("COUNTDOWN_SECRETS_DIR")
    return Path(override).expanduser() if override else Path.home() / ".config" / "countdown-planner"


def get_credentials(credentials_path: Path | None = None, token_path: Path | None = None) -> Credentials:
    prefer_ipv4()
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
