"""Unit tests for Slice 2: pure Python study planner & date-range calendar fetch."""

from __future__ import annotations

import copy
import unittest
from datetime import date

from src.calendar_client import get_events_in_window
from src.planner import build_plan, classify_days, format_plan, load_config


def _timed(summary: str, start: str, end: str, **extra) -> dict:
    return {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def _all_day(summary: str, start: str, end: str | None = None, **extra) -> dict:
    ev: dict = {"summary": summary, "start": {"date": start}, **extra}
    if end:
        ev["end"] = {"date": end}
    return ev


class TestSlice2Planner(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def _assert_capacity_invariants(self, plan) -> None:
        for d in plan.days:
            used = round(sum(a.units for a in d.allocations), 4)
            self.assertLessEqual(used, d.capacity + 1e-9, f"{d.day} over-allocated: {used} > {d.capacity}")
            if d.capacity == 0.0:
                self.assertEqual(d.allocations, [], f"Blocked day {d.day} received work")
            for a in d.allocations:
                self.assertGreater(a.units, 0.0)

    def test_day_classification_rules_and_edge_cases(self) -> None:
        events = [
            # Exact 3.5h boundary -> class (0.25)
            _timed("NLP Lecture", "2026-11-21T09:00:00+01:00", "2026-11-21T12:30:00+01:00"),
            # Overlapping events totaling 3.0h union (not 4.0h sum) -> class (0.25)
            _timed("Lab A", "2026-11-22T10:00:00+01:00", "2026-11-22T12:00:00+01:00"),
            _timed("Lab B", "2026-11-22T11:00:00+01:00", "2026-11-22T13:00:00+01:00"),
            # > 3.5h -> blocked (0.0)
            _timed("Long Seminar", "2026-11-23T09:00:00+01:00", "2026-11-23T13:00:00+01:00"),
            # Keyword "trip" blocks even if short or transparent
            _timed("Weekend trip", "2026-11-24T18:00:00+01:00", "2026-11-24T19:00:00+01:00", transparency="transparent"),
            # Keyword "date" must NOT false-match "Update"
            _timed("Update notes", "2026-11-25T10:00:00+01:00", "2026-11-25T11:00:00+01:00"),
            # Transparent all-day (e.g. week label) does not block; opaque all-day does
            _all_day("Semester Week 6", "2026-11-26", "2026-11-27", transparency="transparent"),
            _all_day("Hackathon", "2026-11-27", "2026-11-28"),
            # Keyword "date" blocks
            _timed("Dinner date", "2026-11-28T19:00:00+01:00", "2026-11-28T21:00:00+01:00"),
        ]
        days = {d.day: d for d in classify_days(date(2026, 11, 20), date(2026, 11, 28), events, self.config)}
        self.assertEqual((days[date(2026, 11, 20)].day_type, days[date(2026, 11, 20)].capacity), ("free", 1.0))
        self.assertEqual((days[date(2026, 11, 21)].day_type, days[date(2026, 11, 21)].capacity), ("class", 0.25))
        self.assertEqual((days[date(2026, 11, 22)].day_type, days[date(2026, 11, 22)].capacity), ("class", 0.25))
        self.assertEqual((days[date(2026, 11, 23)].day_type, days[date(2026, 11, 23)].capacity), ("blocked", 0.0))
        self.assertEqual((days[date(2026, 11, 24)].day_type, days[date(2026, 11, 24)].capacity), ("blocked", 0.0))
        self.assertEqual((days[date(2026, 11, 25)].day_type, days[date(2026, 11, 25)].capacity), ("class", 0.25))
        self.assertEqual((days[date(2026, 11, 26)].day_type, days[date(2026, 11, 26)].capacity), ("free", 1.0))
        self.assertEqual((days[date(2026, 11, 27)].day_type, days[date(2026, 11, 27)].capacity), ("blocked", 0.0))
        self.assertEqual((days[date(2026, 11, 28)].day_type, days[date(2026, 11, 28)].capacity), ("blocked", 0.0))

    def test_comfortable_schedule_fills_exams_assignments_and_optional_paper(self) -> None:
        plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        self._assert_capacity_invariants(plan)
        by_id = {t.target_id: t for t in plan.targets}
        # 24 free days before Dec 14 (Dec 14 itself is blocked as NLP exam day) -> 15 exams + 4 assignments + 5 paper
        self.assertEqual(by_id["nlp-exam"].scheduled, 5.0)
        self.assertEqual(by_id["robotics-exam"].scheduled, 5.0)
        self.assertEqual(by_id["linear-algebra-exam"].scheduled, 5.0)
        self.assertEqual(by_id["ethics-essay"].scheduled, 2.0)
        self.assertEqual(by_id["cv-assignment"].scheduled, 2.0)
        self.assertEqual(plan.optional_units, 5.0)
        self.assertFalse(plan.optional_shed)
        self.assertEqual(plan.unscheduled, {})
        # NLP units must all land strictly before Dec 14, and Dec 14 must be blocked
        dec14 = next(d for d in plan.days if d.day == date(2026, 12, 14))
        self.assertEqual(dec14.day_type, "blocked")
        for d in plan.days:
            if any(a.target_id == "nlp-exam" for a in d.allocations):
                self.assertLess(d.day, date(2026, 12, 14))

    def test_load_shedding_drops_optional_paper_first_and_protects_exams(self) -> None:
        # Block 7 days out of 24 free days -> 17.0u total capacity: 15u exams protected, 2u split on assignments, 0u paper
        events = [_all_day("Trip", f"2026-11-{day:02d}") for day in range(20, 27)]
        plan = build_plan(self.config, events, start_date=date(2026, 11, 20))
        self._assert_capacity_invariants(plan)
        by_id = {t.target_id: t for t in plan.targets}
        self.assertEqual(by_id["nlp-exam"].scheduled, 5.0)
        self.assertEqual(by_id["robotics-exam"].scheduled, 5.0)
        self.assertEqual(by_id["linear-algebra-exam"].scheduled, 5.0)
        # Remaining 2.0u split evenly across both assignments (round-robin)
        self.assertEqual(by_id["ethics-essay"].scheduled, 1.0)
        self.assertEqual(by_id["cv-assignment"].scheduled, 1.0)
        self.assertEqual(plan.optional_units, 0.0)
        self.assertTrue(plan.optional_shed)

    def test_deficit_never_quietly_drops_exam_units(self) -> None:
        # Only 10 free days before Dec 14 -> 10.0u capacity for 15.0u exams
        plan = build_plan(self.config, [], start_date=date(2026, 12, 4))
        self._assert_capacity_invariants(plan)
        self.assertEqual(plan.unscheduled["Linear Algebra"], 5.0)
        self.assertTrue(plan.optional_shed)
        text = format_plan(plan)
        self.assertIn("Unscheduled:", text)
        self.assertIn("Linear Algebra (5.00u)", text)
        self.assertIn("dropped -- tight schedule", text)

    def test_class_days_accumulate_quarter_units_and_respect_earlier_exam_deadline(self) -> None:
        cfg = copy.deepcopy(self.config)
        # Put Robotics earlier than NLP so EDF schedules Robotics before Dec 2
        cfg["targets"][1]["date"] = "2026-12-02"
        events = [
            _timed("Class", "2026-11-20T10:00:00+01:00", "2026-11-20T12:00:00+01:00"),
            _timed("Class", "2026-11-21T10:00:00+01:00", "2026-11-21T12:00:00+01:00"),
        ]
        plan = build_plan(cfg, events, start_date=date(2026, 11, 20))
        self._assert_capacity_invariants(plan)
        for d in plan.days:
            if any(a.target_id == "robotics-exam" for a in d.allocations):
                self.assertLess(d.day, date(2026, 12, 2))
        self.assertEqual(plan.days[0].allocations[0].units, 0.25)

    def test_get_events_in_window_merges_and_paginates(self) -> None:
        class FakeService:
            def calendarList(self): return self
            def events(self): return self
            def list(self, **kwargs):
                self._kwargs = kwargs
                return self
            def execute(self):
                if "calendarId" not in self._kwargs:
                    return {"items": [{"id": "kai", "summary": "Kai Nath", "primary": True}, {"id": "iu", "summary": "IU"}]}
                if self._kwargs["calendarId"] == "kai" and not self._kwargs.get("pageToken"):
                    return {"items": [_all_day("Trip", "2026-11-25")], "nextPageToken": "p2"}
                if self._kwargs["calendarId"] == "kai" and self._kwargs.get("pageToken") == "p2":
                    return {"items": [_timed("Dinner date", "2026-11-22T19:00:00+01:00", "2026-11-22T21:00:00+01:00")]}
                return {"items": [_timed("NLP", "2026-11-21T10:00:00+01:00", "2026-11-21T12:00:00+01:00")]}

        events = get_events_in_window(date(2026, 11, 20), date(2026, 12, 14), service=FakeService())
        self.assertEqual([e["summary"] for e in events], ["NLP", "Dinner date", "Trip"])
        self.assertEqual([e["_calendar_name"] for e in events], ["IU", "Kai Nath", "Kai Nath"])


if __name__ == "__main__":
    unittest.main()
