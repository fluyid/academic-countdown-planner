"""Unit tests for Slice 1: OAuth setup & multi-calendar read."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.calendar_client import format_event_line, load_calendar_config, resolve_calendars
from src.google_auth import SCOPES, get_credentials, secrets_dir


class TestSlice1(unittest.TestCase):
    def test_scopes_cover_calendar_and_tasks(self) -> None:
        self.assertTrue({"https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"}.issubset(SCOPES))

    def test_secrets_live_outside_repo(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        self.assertNotIn(repo_root, secrets_dir().parents)
        self.assertNotEqual(repo_root, secrets_dir())

    def test_missing_credentials_raises(self) -> None:
        missing = Path("__nonexistent_dir__") / "credentials.json"
        with self.assertRaises(FileNotFoundError) as ctx:
            get_credentials(credentials_path=missing, token_path=missing.with_name("token.json"))
        self.assertIn(str(missing), str(ctx.exception))

    def test_format_timed_and_all_day_events(self) -> None:
        timed = {"summary": "NLP", "start": {"dateTime": "2026-11-20T10:00:00+01:00"}, "end": {"dateTime": "2026-11-20T12:00:00+01:00"}, "_calendar_name": "IU"}
        all_day = {"summary": "NLP Exam", "start": {"date": "2026-12-14"}, "_calendar_name": "IU"}
        self.assertEqual(format_event_line(timed), "[IU] 2026-11-20T10:00:00+01:00 -> 2026-11-20T12:00:00+01:00 | NLP")
        self.assertEqual(format_event_line(all_day), "[IU] 2026-12-14 | NLP Exam")

    def test_resolve_kai_nath_and_iu(self) -> None:
        class FakeService:
            def calendarList(self): return self
            def list(self): return self
            def execute(self):
                return {"items": [{"id": "kai@gmail.com", "summary": "Kai Nath", "primary": True}, {"id": "iu@group.calendar.google.com", "summary": "IU"}]}

        self.assertEqual(load_calendar_config(), {"personal": "Kai Nath", "university": "IU"})
        self.assertEqual(
            resolve_calendars(FakeService(), {"personal": "Kai Nath", "university": "IU"}),
            [("personal", "Kai Nath", "kai@gmail.com"), ("university", "IU", "iu@group.calendar.google.com")],
        )


if __name__ == "__main__":
    unittest.main()
