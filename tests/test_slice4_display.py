"""Unit tests for Slice 4: Ephemeral HTML Display Page."""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from unittest import mock

from src.display import (
    DEFAULT_BACKGROUND_PATH,
    MAX_MILESTONES,
    build_display_data,
    render_display_html,
    write_display,
)
from src.planner import build_plan, load_config


class TestSlice4Display(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_concrete_dates_and_no_t_minus_labels(self) -> None:
        plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        data = build_display_data(plan, self.config, completed_units={"nlp-exam": 2.0})
        html_doc = render_display_html(data)

        self.assertEqual(data["countdown"]["label"], "Intro to NLP Exam")
        self.assertEqual(data["countdown"]["date_label"], "Dec 14, 2026")
        self.assertEqual(data["countdown"]["days_left"], 24)
        self.assertEqual(data["milestones"][0]["date_label"], "Dec 14")
        self.assertEqual(data["milestones"][0]["progress_label"], "2/5u")
        self.assertEqual(data["milestones"][0]["percent"], 40)

        self.assertNotIn("T-minus", html_doc)
        self.assertIsNone(re.search(r"\bT-\d+", html_doc))
        self.assertIn("Dec 14, 2026", html_doc)
        self.assertIn("--safe-top:", html_doc)
        self.assertIn("--safe-bottom:", html_doc)
        self.assertIn('params.get("mode") === "static"', html_doc)

    def test_milestones_chronological_and_capped_at_max(self) -> None:
        cfg = copy.deepcopy(self.config)
        cfg["targets"] = [
            {"id": "late", "type": "exam", "name": "Late Exam", "date": "2026-12-20", "units": 2, "priority": 1},
            {"id": "tbd-1", "type": "assignment", "name": "TBD One", "date": None, "units": 1, "priority": 2},
            {"id": "early", "type": "exam", "name": "Early Exam", "date": "2026-12-02", "units": 2, "priority": 1},
            {"id": "mid", "type": "assignment", "name": "Mid Essay", "date": "2026-12-10", "units": 1, "priority": 2},
            {"id": "tbd-2", "type": "assignment", "name": "TBD Two", "date": None, "units": 1, "priority": 2},
            {"id": "mid2", "type": "exam", "name": "Mid Exam", "date": "2026-12-12", "units": 2, "priority": 1},
            {"id": "extra", "type": "assignment", "name": "Overflow Seventh", "date": None, "units": 1, "priority": 2},
            {"id": "extra2", "type": "assignment", "name": "Overflow Eighth", "date": None, "units": 1, "priority": 2},
        ]
        plan = build_plan(cfg, [], start_date=date(2026, 11, 20))
        data = build_display_data(plan, cfg)

        self.assertEqual(len(data["milestones"]), MAX_MILESTONES)
        self.assertEqual(
            [(m["name"], m["date_label"]) for m in data["milestones"]],
            [
                ("Early Exam", "Dec 2"),
                ("Mid Essay", "Dec 10"),
                ("Mid Exam", "Dec 12"),
                ("Late Exam", "Dec 20"),
                ("TBD One", "Date TBD"),
                ("TBD Two", "Date TBD"),
            ],
        )
        # Primary countdown automatically tracks earliest upcoming deadline.
        self.assertEqual(data["countdown"]["label"], "Early Exam")
        self.assertEqual(data["countdown"]["date_label"], "Dec 2, 2026")

    def test_today_states_study_done_and_blocked(self) -> None:
        # Study day
        study_plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        study_data = build_display_data(study_plan, self.config)
        self.assertEqual(study_data["today"]["status"], "study")
        self.assertEqual(study_data["today"]["items"][0]["name"], "Introduction to NLP")

        # Completed today -> done state with next-day preview
        done_plan = build_plan(
            self.config,
            [],
            start_date=date(2026, 11, 20),
            completed_units={"nlp-exam": 1.0},
            completed_days={date(2026, 11, 20)},
        )
        done_data = build_display_data(
            done_plan,
            self.config,
            completed_units={"nlp-exam": 1.0},
            completed_days={date(2026, 11, 20)},
        )
        self.assertEqual(done_data["today"]["status"], "done")
        self.assertEqual(done_data["today"]["headline"], "Done for today")
        self.assertIn("Nov 21", done_data["today"]["next_preview"])

        # Blocked day -> blocked state with reason
        trip = [{"summary": "Family Trip", "start": {"date": "2026-11-20"}}]
        blocked_plan = build_plan(self.config, trip, start_date=date(2026, 11, 20))
        blocked_data = build_display_data(blocked_plan, self.config)
        self.assertEqual(blocked_data["today"]["status"], "blocked")
        self.assertIn("Family Trip", blocked_data["today"]["detail"])

    def test_escaping_and_background_validation(self) -> None:
        cfg = copy.deepcopy(self.config)
        cfg["window"]["anchor_label"] = "NLP </script><img src=x onerror=alert(1)>"
        plan = build_plan(cfg, [], start_date=date(2026, 11, 20))
        data = build_display_data(plan, cfg, background="javascript:alert(1)")
        html_doc = render_display_html(data)

        self.assertIsNone(data["background_url"])
        self.assertNotIn("<img src=x", html_doc)
        self.assertIn("&lt;/script&gt;", html_doc)
        self.assertNotIn("javascript:", html_doc)

    def test_default_background_and_overrides(self) -> None:
        plan = build_plan(self.config, [], start_date=date(2026, 11, 20))

        # Default uses bundled cyberpunk background + local font faces when present.
        default_data = build_display_data(plan, self.config)
        self.assertTrue(DEFAULT_BACKGROUND_PATH.is_file())
        self.assertEqual(default_data["background_url"], DEFAULT_BACKGROUND_PATH.resolve().as_uri())
        rendered = render_display_html(default_data)
        self.assertIn("data:image/jpeg;base64,", rendered)
        for family in ("Cyberway Riders", "Orbitron", "Rajdhani"):
            self.assertIn(f'font-family: "{family}"', rendered)

        # Explicit URL or path overrides default.
        custom_data = build_display_data(plan, self.config, background="https://example.com/custom.png")
        self.assertEqual(custom_data["background_url"], "https://example.com/custom.png")

        # Empty string explicitly disables image (falls back to CSS gradient).
        none_data = build_display_data(plan, self.config, background="")
        self.assertIsNone(none_data["background_url"])

        # Missing default asset gracefully falls back to None.
        missing = Path(DEFAULT_BACKGROUND_PATH.parent / "__nonexistent_bg__.jpg")
        with mock.patch("src.display.model.DEFAULT_BACKGROUND_PATH", missing):
            fallback_data = build_display_data(plan, self.config)
        self.assertIsNone(fallback_data["background_url"])

    def test_write_display_outputs_ephemeral_html_and_json(self) -> None:
        plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "output"
            res = write_display(plan, self.config, output_dir=out_dir)
            self.assertTrue(res.html_path.exists())
            self.assertTrue(res.data_path.exists())
            self.assertEqual(res.html_path.name, "index.html")
            saved_json = json.loads(res.data_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_json["countdown"]["target_iso"], "2026-12-14")
            self.assertIn("<!DOCTYPE html>", res.html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
