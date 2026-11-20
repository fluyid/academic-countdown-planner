"""Pure conversion between DayPlan objects and Google Tasks payloads/progress."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from src.planner.models import Allocation, DayPlan

META_PREFIX = "[countdown-planner] "


def _fmt_units(value: float) -> str:
    return f"{round(value, 4):g}"


def _title_part(alloc: Allocation) -> str:
    label = "Paper Reading" if alloc.target_id == "paper-reading" else alloc.name
    if alloc.total is not None:
        return f"{label} ({_fmt_units(alloc.units)}u, {_fmt_units(alloc.cumulative)}/{_fmt_units(alloc.total)})"
    return f"{label} ({_fmt_units(alloc.units)}u)"


def build_task_body(day_plan: DayPlan) -> dict[str, str]:
    title = " + ".join(_title_part(a) for a in day_plan.allocations)
    due = f"{day_plan.day.isoformat()}T00:00:00.000Z"

    reason = f"; {day_plan.reason}" if day_plan.day_type == "class" and day_plan.reason else ""
    lines = [f"Day: {day_plan.day.isoformat()} ({day_plan.day_type}, {_fmt_units(day_plan.capacity)}u{reason})"]
    units_by_id: dict[str, float] = {}
    for a in day_plan.allocations:
        prog = f" ({_fmt_units(a.cumulative)}/{_fmt_units(a.total)})" if a.total is not None else ""
        lines.append(f"- {a.name}: {_fmt_units(a.units)}u{prog}")
        units_by_id[a.target_id] = round(units_by_id.get(a.target_id, 0.0) + a.units, 4)

    meta = json.dumps({"day": day_plan.day.isoformat(), "units": units_by_id}, sort_keys=True, separators=(",", ":"))
    lines.extend(["", f"{META_PREFIX}{meta}"])
    return {"title": title, "due": due, "notes": "\n".join(lines)}


def parse_task_meta(task: dict[str, Any]) -> tuple[date, dict[str, float]] | None:
    notes = task.get("notes") or ""
    for line in reversed(notes.splitlines()):
        if not line.startswith(META_PREFIX):
            continue
        try:
            payload = json.loads(line[len(META_PREFIX) :])
            day = date.fromisoformat(payload["day"])
            raw_units = payload.get("units")
            if not isinstance(raw_units, dict):
                return None
            units = {str(k): round(float(v), 4) for k, v in raw_units.items() if float(v) > 0}
            return day, units
        except (ValueError, KeyError, TypeError):
            return None
    return None


def read_completed_progress(tasks: list[dict[str, Any]]) -> tuple[dict[str, float], set[date]]:
    completed_units: dict[str, float] = {}
    completed_days: set[date] = set()
    for task in tasks:
        if task.get("status") != "completed":
            continue
        parsed = parse_task_meta(task)
        if not parsed:
            continue
        day, units = parsed
        completed_days.add(day)
        for tid, amount in units.items():
            completed_units[tid] = round(completed_units.get(tid, 0.0) + amount, 4)
    return completed_units, completed_days


def task_matches_body(existing: dict[str, Any], body: dict[str, str]) -> bool:
    return (
        (existing.get("title") or "") == body["title"]
        and (existing.get("due") or "")[:10] == body["due"][:10]
        and (existing.get("notes") or "") == body["notes"]
    )
