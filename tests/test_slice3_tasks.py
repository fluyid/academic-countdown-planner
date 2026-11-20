"""Unit tests for Slice 3: Google Tasks sync & completed-progress truth."""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from src.planner import Allocation, DayPlan, build_plan, load_config
from src.tasks import (
    build_task_body,
    format_sync_summary,
    get_or_create_tasklist,
    list_tasks,
    parse_task_meta,
    read_completed_progress,
    sync_plan_to_tasks,
)


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeTasksService:
    """In-memory Google Tasks v1 mock recording API calls."""

    def __init__(self, lists: list[dict[str, str]] | None = None, tasks_by_list: dict[str, list[dict[str, Any]]] | None = None):
        self.lists = list(lists or [])
        self.tasks_by_list: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in (tasks_by_list or {}).items()}
        self.calls: list[tuple[str, Any]] = []
        self._seq = 100

    def tasklists(self):
        service = self

        class _Lists:
            def list(self, **kwargs):
                service.calls.append(("tasklists.list", kwargs))
                return _Call(lambda: {"items": list(service.lists)})

            def insert(self, body):
                service.calls.append(("tasklists.insert", body))

                def _do():
                    service._seq += 1
                    item = {"id": f"list-{service._seq}", "title": body["title"]}
                    service.lists.append(item)
                    service.tasks_by_list[item["id"]] = []
                    return item

                return _Call(_do)

        return _Lists()

    def tasks(self):
        service = self

        class _Tasks:
            def list(self, **kwargs):
                service.calls.append(("tasks.list", kwargs))
                items = list(service.tasks_by_list.get(kwargs["tasklist"], []))
                return _Call(lambda: {"items": items})

            def insert(self, tasklist, body):
                service.calls.append(("tasks.insert", body))

                def _do():
                    service._seq += 1
                    task = {"id": f"task-{service._seq}", "status": "needsAction", **body}
                    # Google Tasks inserts at the top of the list.
                    service.tasks_by_list.setdefault(tasklist, []).insert(0, task)
                    return task

                return _Call(_do)

            def patch(self, tasklist, task, body):
                service.calls.append(("tasks.patch", (task, body)))

                def _do():
                    for item in service.tasks_by_list.get(tasklist, []):
                        if item["id"] == task:
                            item.update(body)
                            return item
                    raise KeyError(task)

                return _Call(_do)

            def delete(self, tasklist, task):
                service.calls.append(("tasks.delete", task))

                def _do():
                    service.tasks_by_list[tasklist] = [t for t in service.tasks_by_list.get(tasklist, []) if t["id"] != task]
                    return {}

                return _Call(_do)

        return _Tasks()


class TestSlice3Tasks(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_codec_round_trip_and_completed_progress(self) -> None:
        day_plan = DayPlan(
            day=date(2026, 11, 20),
            day_type="free",
            capacity=1.0,
            scheduled_hours=0.0,
            reason="",
            allocations=[Allocation("nlp-exam", "Introduction to NLP", 1, 1.0, 1.0, 5.0)],
        )
        body = build_task_body(day_plan)
        self.assertEqual(body["title"], "Introduction to NLP (1u, 1/5)")
        self.assertEqual(body["due"], "2026-11-20T00:00:00.000Z")
        self.assertEqual(parse_task_meta(body), (date(2026, 11, 20), {"nlp-exam": 1.0}))

        tasks = [
            {"id": "t1", "status": "completed", **body},
            {"id": "t2", "status": "needsAction", **body},
            {"id": "t3", "status": "completed", "title": "Buy coffee", "notes": "personal note"},
            {"id": "t4", "status": "completed", "title": "Broken", "notes": "[countdown-planner] {bad json"},
        ]
        units, days = read_completed_progress(tasks)
        self.assertEqual(units, {"nlp-exam": 1.0})
        self.assertEqual(days, {date(2026, 11, 20)})

    def test_planner_respects_completed_units_and_days(self) -> None:
        plan = build_plan(
            self.config,
            [],
            start_date=date(2026, 11, 20),
            completed_units={"nlp-exam": 1.0},
            completed_days={date(2026, 11, 20)},
        )
        day0 = plan.days[0]
        self.assertEqual(day0.day, date(2026, 11, 20))
        self.assertEqual(day0.allocations, [])
        # Nov 21 resumes NLP at 2/5 (not reset to 1/5, and total scheduled+completed stays 5.0).
        day1 = plan.days[1]
        self.assertEqual(day1.allocations[0].target_id, "nlp-exam")
        self.assertEqual(day1.allocations[0].cumulative, 2.0)
        nlp_allocs = [a.units for d in plan.days for a in d.allocations if a.target_id == "nlp-exam"]
        self.assertEqual(sum(nlp_allocs), 4.0)

    def test_sync_creates_chronologically_and_is_idempotent(self) -> None:
        svc = FakeTasksService()
        list_id = get_or_create_tasklist(svc, "Countdown Planner (Test)")
        self.assertEqual(get_or_create_tasklist(svc, "countdown planner (test)"), list_id)

        plan = build_plan(self.config, [], start_date=date(2026, 12, 10))
        res1 = sync_plan_to_tasks(svc, list_id, "Countdown Planner (Test)", plan)
        # Dec 10, 11, 12, 13 are 4 study days (Dec 14 is blocked exam day).
        self.assertEqual((res1.created, res1.updated, res1.deleted, res1.unchanged), (4, 0, 0, 0))
        stored = svc.tasks_by_list[list_id]
        self.assertEqual([t["due"][:10] for t in stored], ["2026-12-10", "2026-12-11", "2026-12-12", "2026-12-13"])

        # Second sync performs zero write calls.
        svc.calls.clear()
        res2 = sync_plan_to_tasks(svc, list_id, "Countdown Planner (Test)", plan)
        self.assertEqual((res2.created, res2.updated, res2.deleted, res2.unchanged), (0, 0, 0, 4))
        write_ops = [op for op, _ in svc.calls if op in ("tasks.insert", "tasks.patch", "tasks.delete")]
        self.assertEqual(write_ops, [])
        self.assertIn("=4 unchanged", format_sync_summary(res2))

    def test_sync_preserves_completed_and_unmanaged_and_prunes_blocked_days(self) -> None:
        svc = FakeTasksService()
        list_id = get_or_create_tasklist(svc, "Countdown Planner (Test)")
        # Unrelated personal task must never be touched.
        svc.tasks_by_list[list_id].append({"id": "manual-1", "status": "needsAction", "title": "Call mom"})

        initial_plan = build_plan(self.config, [], start_date=date(2026, 12, 10))
        sync_plan_to_tasks(svc, list_id, "Countdown Planner (Test)", initial_plan)

        # User completes Dec 10 in Google Tasks.
        dec10_task = next(t for t in svc.tasks_by_list[list_id] if (t.get("due") or "").startswith("2026-12-10"))
        dec10_task["status"] = "completed"

        # Now Dec 11 becomes blocked by a trip in Google Calendar.
        existing = list_tasks(svc, list_id)
        completed_units, completed_days = read_completed_progress(existing)
        self.assertEqual(completed_units, {"nlp-exam": 1.0})
        self.assertEqual(completed_days, {date(2026, 12, 10)})

        trip = [{"summary": "Trip", "start": {"date": "2026-12-11"}}]
        replanned = build_plan(
            self.config,
            trip,
            start_date=date(2026, 12, 10),
            completed_units=completed_units,
            completed_days=completed_days,
        )
        res = sync_plan_to_tasks(svc, list_id, "Countdown Planner (Test)", replanned, existing_tasks=existing)

        self.assertEqual(res.completed_preserved, 1)
        self.assertEqual(res.deleted, 1)  # Dec 11 open task pruned because day is now blocked
        self.assertEqual(res.updated, 2)  # Dec 12 & Dec 13 shifted from (3/5, 4/5) to (2/5, 3/5)
        remaining = svc.tasks_by_list[list_id]
        # Completed Dec 10 task and manual task both still exist intact.
        self.assertTrue(any(t["id"] == dec10_task["id"] and t["status"] == "completed" for t in remaining))
        self.assertTrue(any(t["id"] == "manual-1" for t in remaining))
        self.assertFalse(any((t.get("due") or "").startswith("2026-12-11") for t in remaining))

    def test_list_tasks_requests_hidden_completed_and_skips_deleted(self) -> None:
        svc = FakeTasksService(
            lists=[{"id": "L1", "title": "Countdown Planner (Test)"}],
            tasks_by_list={
                "L1": [
                    {"id": "1", "title": "Active", "status": "needsAction"},
                    {"id": "2", "title": "Done", "status": "completed", "hidden": True},
                    {"id": "3", "title": "Trash", "deleted": True},
                ]
            },
        )
        items = list_tasks(svc, "L1")
        self.assertEqual([t["id"] for t in items], ["1", "2"])
        list_call = next(kwargs for op, kwargs in svc.calls if op == "tasks.list")
        self.assertTrue(list_call["showCompleted"])
        self.assertTrue(list_call["showHidden"])


if __name__ == "__main__":
    unittest.main()
