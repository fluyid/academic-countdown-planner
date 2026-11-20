"""Unit tests for Slice 6: Gemini Replan & zero-accidental-billing guardrails."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from src.gemini import (
    DEFAULT_MODEL,
    AddEventAction,
    TickTaskAction,
    UnclearAction,
    generate_content,
    load_api_key,
    parse_user_action,
    replan_from_text,
)
from src.gemini.client import REPO_ROOT
from src.planner import build_plan, load_config
from src.tasks import get_or_create_tasklist, sync_plan_to_tasks
from src.wallpaper import RefreshResult, render_schedule_definition
from tests.test_slice3_tasks import FakeTasksService


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendarService:
    """In-memory Google Calendar v3 mock supporting list + insert."""

    def __init__(self, events: list[dict[str, Any]] | None = None):
        self.events_by_cal: dict[str, list[dict[str, Any]]] = {"cal-personal": list(events or []), "cal-uni": []}
        self.inserted: list[tuple[str, dict[str, Any]]] = []

    def calendarList(self):
        class _CalList:
            def list(self):
                return _Call(
                    lambda: {
                        "items": [
                            {"id": "cal-personal", "summary": "Kai Nath", "primary": True},
                            {"id": "cal-uni", "summary": "IU"},
                        ]
                    }
                )

        return _CalList()

    def events(self):
        svc = self

        class _Events:
            def list(self, calendarId, **kwargs):
                return _Call(lambda: {"items": list(svc.events_by_cal.get(calendarId, []))})

            def insert(self, calendarId, body):
                def _do():
                    created = {"id": f"ev-{len(svc.inserted) + 1}", **body}
                    svc.events_by_cal.setdefault(calendarId, []).append(created)
                    svc.inserted.append((calendarId, body))
                    return created

                return _Call(_do)

        return _Events()


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


class TestSlice6Gemini(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_key_loading_and_billing_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env_file = base / ".env"
            usage_file = base / "gemini_usage.json"

            # Reject repo-internal paths for both secrets and usage state.
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
                with self.assertRaises(ValueError):
                    load_api_key(env_path=REPO_ROOT / ".env")
                with self.assertRaises(FileNotFoundError):
                    load_api_key(env_path=env_file)

                env_file.write_text("# secret\nexport GEMINI_API_KEY='test-secret-key'\n", encoding="utf-8")
                self.assertEqual(load_api_key(env_path=env_file), "test-secret-key")

            # Reject non-Flash models and oversized token caps before making any HTTP call.
            calls: list[dict[str, Any]] = []

            def ok_post(url, headers, json, timeout):
                calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
                return _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

            with self.assertRaises(ValueError):
                generate_content("hi", model="gemini-3.5-pro", api_key="k", usage_path=usage_file, post_fn=ok_post)
            with self.assertRaises(ValueError):
                generate_content("hi", max_output_tokens=1024, api_key="k", usage_path=usage_file, post_fn=ok_post)
            self.assertEqual(calls, [])

            # Enforce hard daily cap (2 calls/day) + verify Flash default + key in header (not URL).
            out1 = generate_content("one", api_key="k1", usage_path=usage_file, daily_cap=2, today=date(2026, 11, 20), post_fn=ok_post)
            out2 = generate_content("two", api_key="k1", usage_path=usage_file, daily_cap=2, today=date(2026, 11, 20), post_fn=ok_post)
            self.assertEqual((out1, out2), ("ok", "ok"))
            self.assertIn(f"/{DEFAULT_MODEL}:generateContent", calls[0]["url"])
            self.assertNotIn("k1", calls[0]["url"])
            self.assertEqual(calls[0]["headers"]["x-goog-api-key"], "k1")
            self.assertEqual(calls[0]["json"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "MINIMAL"})

            with self.assertRaisesRegex(RuntimeError, "Daily Gemini call cap reached"):
                generate_content("three", api_key="k1", usage_path=usage_file, daily_cap=2, today=date(2026, 11, 20), post_fn=ok_post)
            self.assertEqual(len(calls), 2)

            # Cap resets on a new calendar day.
            generate_content("next-day", api_key="k1", usage_path=usage_file, daily_cap=2, today=date(2026, 11, 21), post_fn=ok_post)
            self.assertEqual(len(calls), 3)

            # Fail fast with zero retries on HTTP error or network timeout; never leak key.
            fail_count = 0

            def fail_post(url, headers, json, timeout):
                nonlocal fail_count
                fail_count += 1
                raise requests.Timeout("timed out")

            with self.assertRaisesRegex(RuntimeError, "no retries") as ctx:
                generate_content("boom", api_key="super-secret", usage_path=usage_file, daily_cap=10, today=date(2026, 11, 22), post_fn=fail_post)
            self.assertEqual(fail_count, 1)
            self.assertNotIn("super-secret", str(ctx.exception))

    def test_add_event_reshuffles_plan_syncs_and_refreshes(self) -> None:
        cal_svc = FakeCalendarService()
        task_svc = FakeTasksService()
        list_id = get_or_create_tasklist(task_svc, "Countdown Planner (Test)")
        seed_plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        sync_plan_to_tasks(task_svc, list_id, "Countdown Planner (Test)", seed_plan)

        gemini_prompts: list[str] = []

        def fake_gemini(prompt: str, **kwargs) -> str:
            gemini_prompts.append(prompt)
            if len(gemini_prompts) == 1:
                return json.dumps({
                    "action": "add_event",
                    "calendar": "personal",
                    "summary": "Plans",
                    "date": "2026-11-28",
                    "all_day": True,
                })
            return "Blocked Nov 28 for Plans and shifted your remaining study units forward with zero units dropped."

        refreshed: list[dict[str, Any]] = []

        def fake_refresh(plan, config, **kwargs) -> RefreshResult:
            refreshed.append({"plan": plan, **kwargs})
            return RefreshResult(Path("output/index.html"), Path("output/lockscreen.png"), Path("lock.png"), (1920, 1080), True, True)

        res = replan_from_text(
            "I have plans on the 28th",
            today=date(2026, 11, 20),
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=fake_gemini,
            refresh_fn=fake_refresh,
        )

        self.assertIsInstance(res.action, AddEventAction)
        self.assertEqual(len(cal_svc.inserted), 1)
        cid, body = cal_svc.inserted[0]
        self.assertEqual(cid, "cal-personal")
        self.assertEqual(body["start"], {"date": "2026-11-28"})
        self.assertEqual(body["end"], {"date": "2026-11-29"})

        nov28 = next(d for d in res.after_plan.days if d.day == date(2026, 11, 28))
        self.assertEqual(nov28.day_type, "blocked")
        self.assertEqual(nov28.allocations, [])
        self.assertFalse(any((t.get("due") or "").startswith("2026-11-28") for t in task_svc.tasks_by_list[list_id]))
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(len(gemini_prompts), 2)
        self.assertIn("2026-11-28", gemini_prompts[1])
        self.assertIn("Nov 28", res.explanation)

        # Timed event on university calendar classifies as class day (<=3.5h -> 0.25u) and validates HH:MM.
        timed_calls = iter([
            json.dumps({
                "action": "add_event",
                "calendar": "university",
                "summary": "NLP Seminar",
                "date": "2026-11-29",
                "all_day": False,
                "start_time": "10:00",
                "end_time": "12:00",
            }),
            "Added 2h NLP Seminar on Nov 29 (IU).",
        ])
        res2 = replan_from_text(
            "NLP seminar Nov 29 10am to 12pm",
            today=date(2026, 11, 20),
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=lambda *a, **kw: next(timed_calls),
            refresh_fn=fake_refresh,
        )
        self.assertEqual(cal_svc.inserted[1][0], "cal-uni")
        self.assertIn("T10:00:00", cal_svc.inserted[1][1]["start"]["dateTime"])
        nov29 = next(d for d in res2.after_plan.days if d.day == date(2026, 11, 29))
        self.assertEqual(nov29.day_type, "class")
        self.assertEqual(nov29.capacity, 0.25)

    def test_tick_task_echoes_exact_task_and_preserves_progress(self) -> None:
        cal_svc = FakeCalendarService()
        task_svc = FakeTasksService()
        list_id = get_or_create_tasklist(task_svc, "Countdown Planner (Test)")
        seed_plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        sync_plan_to_tasks(task_svc, list_id, "Countdown Planner (Test)", seed_plan)

        today_task = next(t for t in task_svc.tasks_by_list[list_id] if (t.get("due") or "").startswith("2026-11-20"))
        responses = iter([
            json.dumps({"action": "tick_task", "task_id": today_task["id"]}),
            f"Marked '{today_task['title']}' done; tomorrow continues NLP at 2/5.",
        ])

        def fake_refresh(plan, config, **kwargs) -> RefreshResult:
            return RefreshResult(Path("output/index.html"), Path("output/lockscreen.png"), None, (1920, 1080), True, False)

        res = replan_from_text(
            "I finished today's NLP study task",
            today=date(2026, 11, 20),
            apply_lock_screen=False,
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=lambda *a, **kw: next(responses),
            refresh_fn=fake_refresh,
        )

        self.assertIsInstance(res.action, TickTaskAction)
        self.assertIsNotNone(res.ticked_task)
        self.assertEqual(res.ticked_task["id"], today_task["id"])
        self.assertEqual(res.ticked_task["title"], today_task["title"])
        self.assertIn(today_task["title"], res.action_summary)
        self.assertEqual(today_task["status"], "completed")
        self.assertEqual(res.sync_result.completed_preserved, 1)
        # Pure Python planner starts tomorrow at 2/5 (no double-scheduling Nov 20).
        day0, day1 = res.after_plan.days[0], res.after_plan.days[1]
        self.assertEqual(day0.allocations, [])
        self.assertEqual(day1.allocations[0].cumulative, 2.0)

        # Hallucinated task_id must never tick anything.
        bad = parse_user_action(
            "done",
            today=date(2026, 11, 20),
            calendars={"personal": "Kai Nath", "university": "IU"},
            open_tasks=[{"id": "t-real", "title": "Real task", "due": "2026-11-21T00:00:00.000Z"}],
            call_gemini=lambda *a, **kw: json.dumps({"action": "tick_task", "task_id": "t-invented"}),
        )
        self.assertIsInstance(bad, UnclearAction)

    def test_unclear_action_makes_no_writes_and_schedule_never_uses_gemini(self) -> None:
        cal_svc = FakeCalendarService()
        task_svc = FakeTasksService()
        gemini_calls = 0

        def fake_gemini(*a, **kw) -> str:
            nonlocal gemini_calls
            gemini_calls += 1
            return json.dumps({"action": "unclear", "reason": "No date or task mentioned."})

        res = replan_from_text(
            "hello there",
            today=date(2026, 11, 20),
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=fake_gemini,
            refresh_fn=lambda *a, **kw: self.fail("refresh_screen must not run on unclear action"),
        )

        self.assertIsInstance(res.action, UnclearAction)
        self.assertEqual(gemini_calls, 1)
        self.assertEqual(cal_svc.inserted, [])
        self.assertIsNone(res.sync_result)

        # Scheduled refreshes invoke refresh-screen --if-changed, never Gemini (`say`).
        for plat in ("win32", "darwin"):
            sched_def = render_schedule_definition(platform=plat)
            self.assertIn("refresh-screen", sched_def)
            self.assertNotIn(" say", sched_def)
            self.assertNotIn("gemini", sched_def.lower())


if __name__ == "__main__":
    unittest.main()
