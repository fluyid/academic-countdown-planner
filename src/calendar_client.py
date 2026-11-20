"""Google Calendar reader across configured calendars ("Kai Nath" + "IU")."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build

from src.google_auth import get_credentials

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "targets.json"


def load_calendar_config(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    return json.loads(config_path.read_text(encoding="utf-8"))["calendars"]


def resolve_calendars(service: Any, role_to_name: dict[str, str]) -> list[tuple[str, str, str]]:
    items = service.calendarList().list().execute().get("items", [])
    by_name = {(i.get("summaryOverride") or i.get("summary") or i["id"]).strip().lower(): (i.get("summaryOverride") or i.get("summary") or i["id"], i["id"]) for i in items}
    primary = next(((i.get("summaryOverride") or i.get("summary") or i["id"], i["id"]) for i in items if i.get("primary")), None)

    resolved, seen = [], set()
    for role, name in role_to_name.items():
        match = by_name.get(name.strip().lower()) or (primary if role == "personal" else None)
        if match and match[1] not in seen:
            resolved.append((role, match[0], match[1]))
            seen.add(match[1])
    return resolved or [("personal", "primary", "primary")]


def _event_sort_key(event: dict[str, Any]) -> str:
    return event.get("start", {}).get("dateTime") or f"{event.get('start', {}).get('date', '9999-12-31')}T00:00:00Z"


def get_calendar_service(service: Any = None) -> Any:
    return service or build("calendar", "v3", credentials=get_credentials())


def get_upcoming_events(max_results: int = 10) -> list[dict[str, Any]]:
    service = get_calendar_service()
    now = datetime.now(timezone.utc).isoformat()
    merged: list[dict[str, Any]] = []
    for role, name, cid in resolve_calendars(service, load_calendar_config()):
        items = service.events().list(calendarId=cid, timeMin=now, maxResults=max_results, singleEvents=True, orderBy="startTime").execute().get("items", [])
        merged.extend({**ev, "_calendar_role": role, "_calendar_name": name} for ev in items)
    merged.sort(key=_event_sort_key)
    return merged[:max_results]


def get_events_in_window(
    start_date: date,
    end_date: date,
    config_path: Path = CONFIG_PATH,
    service: Any = None,
) -> list[dict[str, Any]]:
    service = get_calendar_service(service)
    tz = datetime.now().astimezone().tzinfo
    time_min = datetime.combine(start_date, time.min, tzinfo=tz).isoformat()
    time_max = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz).isoformat()
    merged: list[dict[str, Any]] = []
    for role, name, cid in resolve_calendars(service, load_calendar_config(config_path)):
        page_token: str | None = None
        while True:
            resp = service.events().list(
                calendarId=cid,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            ).execute()
            merged.extend({**ev, "_calendar_role": role, "_calendar_name": name} for ev in resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    merged.sort(key=_event_sort_key)
    return merged


def create_calendar_event(
    summary: str,
    start_date: date,
    *,
    end_date: date | None = None,
    all_day: bool = True,
    start_time: str | None = None,
    end_time: str | None = None,
    calendar_role: str = "personal",
    config_path: Path = CONFIG_PATH,
    service: Any = None,
) -> dict[str, Any]:
    svc = get_calendar_service(service)
    resolved = resolve_calendars(svc, load_calendar_config(config_path))
    role, cal_name, cid = next((c for c in resolved if c[0] == calendar_role), resolved[0])

    if all_day:
        inclusive_end = end_date or start_date
        if inclusive_end < start_date:
            raise ValueError(f"end_date ({inclusive_end}) cannot be before start_date ({start_date}).")
        # Google Calendar all-day end.date is exclusive.
        body: dict[str, Any] = {
            "summary": summary.strip(),
            "start": {"date": start_date.isoformat()},
            "end": {"date": (inclusive_end + timedelta(days=1)).isoformat()},
        }
    else:
        if not start_time or not end_time:
            raise ValueError("Timed calendar events require both start_time and end_time (HH:MM).")
        tz = datetime.now().astimezone().tzinfo
        t_start = time.fromisoformat(start_time)
        t_end = time.fromisoformat(end_time)
        if t_end <= t_start:
            raise ValueError(f"end_time ({end_time}) must be after start_time ({start_time}).")
        body = {
            "summary": summary.strip(),
            "start": {"dateTime": datetime.combine(start_date, t_start, tzinfo=tz).isoformat()},
            "end": {"dateTime": datetime.combine(start_date, t_end, tzinfo=tz).isoformat()},
        }

    created = svc.events().insert(calendarId=cid, body=body).execute()
    return {**created, "_calendar_role": role, "_calendar_name": cal_name}


def format_event_line(event: dict[str, Any]) -> str:
    start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "TBD")
    end = event.get("end", {}).get("dateTime") or ""
    prefix = f"[{event['_calendar_name']}] " if event.get("_calendar_name") else ""
    times = f"{start} -> {end}" if end and "T" in start else start
    return f"{prefix}{times} | {event.get('summary', '(No title)')}"
