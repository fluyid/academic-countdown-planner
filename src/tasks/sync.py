"""Idempotent StudyPlan -> Google Tasks sync that preserves completed progress."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.planner.models import StudyPlan
from src.tasks.client import create_task, delete_task, list_tasks, update_task
from src.tasks.codec import build_task_body, parse_task_meta, task_matches_body


@dataclass
class SyncResult:
    list_name: str
    tasklist_id: str
    created: int
    updated: int
    deleted: int
    unchanged: int
    completed_preserved: int
    total_study_days: int


def sync_plan_to_tasks(
    service: Any,
    tasklist_id: str,
    list_name: str,
    plan: StudyPlan,
    existing_tasks: list[dict[str, Any]] | None = None,
) -> SyncResult:
    tasks = list_tasks(service, tasklist_id) if existing_tasks is None else existing_tasks

    completed_days: set[date] = set()
    completed_preserved = 0
    open_by_day: dict[date, list[dict[str, Any]]] = {}

    for task in tasks:
        parsed = parse_task_meta(task)
        if not parsed:
            continue
        day, _ = parsed
        if task.get("status") == "completed":
            completed_days.add(day)
            completed_preserved += 1
        else:
            open_by_day.setdefault(day, []).append(task)

    desired = [
        (d.day, build_task_body(d))
        for d in plan.days
        if d.allocations and d.day not in completed_days
    ]

    created = updated = unchanged = deleted = 0
    to_create: list[dict[str, str]] = []
    stale_ids: list[str] = []

    for day, body in desired:
        matches = open_by_day.pop(day, [])
        if matches:
            primary = matches[0]
            stale_ids.extend(dup["id"] for dup in matches[1:] if dup.get("id"))
            if task_matches_body(primary, body):
                unchanged += 1
            else:
                update_task(service, tasklist_id, primary["id"], body)
                updated += 1
        else:
            to_create.append(body)

    # Insert in reverse chronological order so Google Tasks places the earliest study day at the top.
    for body in reversed(to_create):
        create_task(service, tasklist_id, body)
        created += 1

    for leftovers in open_by_day.values():
        stale_ids.extend(t["id"] for t in leftovers if t.get("id"))
    for tid in stale_ids:
        delete_task(service, tasklist_id, tid)
        deleted += 1

    return SyncResult(
        list_name=list_name,
        tasklist_id=tasklist_id,
        created=created,
        updated=updated,
        deleted=deleted,
        unchanged=unchanged,
        completed_preserved=completed_preserved,
        total_study_days=len(desired),
    )


def format_sync_summary(result: SyncResult) -> str:
    return (
        f"Synced to Google Tasks list '{result.list_name}': "
        f"{result.total_study_days} open study days "
        f"(+{result.created} created, ~{result.updated} updated, "
        f"-{result.deleted} deleted, ={result.unchanged} unchanged, "
        f"{result.completed_preserved} completed preserved)"
    )
