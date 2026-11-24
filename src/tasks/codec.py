"""Pure conversion between DayPlan objects and Google Tasks payloads/progress."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from src.planner.formatter import format_allocation_amount, format_pages, format_unit_ratio, format_units
from src.planner.models import Allocation, DayPlan, reading_target_id

META_PREFIX = "[countdown-planner] "


def _title_part(alloc: Allocation) -> str:
    label = "Optional Paper Reading" if alloc.target_id == "paper-reading" else alloc.name
    amount = format_allocation_amount(alloc)
    if alloc.total is not None:
        return f"{label} ({amount}, {format_unit_ratio(alloc.cumulative, alloc.total)})"
    return f"{label} ({amount})"


def build_task_body(day_plan: DayPlan) -> dict[str, str]:
    title = " + ".join(_title_part(a) for a in day_plan.allocations)
    due = f"{day_plan.day.isoformat()}T00:00:00.000Z"

    reason = f"; {day_plan.reason}" if day_plan.day_type == "class" and day_plan.reason else ""
    lines = [f"Day: {day_plan.day.isoformat()} ({day_plan.day_type}, {format_units(day_plan.capacity)}{reason})"]
    units_by_id: dict[str, float] = {}
    for a in day_plan.allocations:
        prog = f" ({format_unit_ratio(a.cumulative, a.total)})" if a.total is not None else ""
        lines.append(f"- {a.name}: {format_allocation_amount(a)}{prog}")
        units_by_id[a.target_id] = round(units_by_id.get(a.target_id, 0.0) + a.units, 4)

    meta = json.dumps({"day": day_plan.day.isoformat(), "units": units_by_id}, sort_keys=True, separators=(",", ":"))
    lines.extend(["", f"{META_PREFIX}{meta}"])
    return {"title": title, "due": due, "notes": "\n".join(lines)}


def build_progress_log_body(
    day: date,
    target_id: str,
    target_name: str,
    units: float,
    *,
    is_reading: bool = False,
    pages_per_unit: float = 40.0,
) -> dict[str, str]:
    clean_units = round(float(units), 4)
    if clean_units <= 0:
        raise ValueError("Logged units must be > 0.")
    meta_tid = reading_target_id(target_id) if is_reading else target_id
    label = f"{target_name} \u2014 Required Paper Reading" if is_reading else target_name
    amount = (
        f"{format_units(clean_units)} ({format_pages(round(clean_units * pages_per_unit, 4))})"
        if is_reading
        else format_units(clean_units)
    )
    title = f"Logged: {label} ({amount})"
    due = f"{day.isoformat()}T00:00:00.000Z"
    meta = json.dumps(
        {"consume_day": False, "day": day.isoformat(), "units": {meta_tid: clean_units}},
        sort_keys=True,
        separators=(",", ":"),
    )
    notes = f"Logged completed progress on {day.isoformat()}: {label} ({amount})\n\n{META_PREFIX}{meta}"
    return {"title": title, "due": due, "notes": notes}


def _parse_meta_payload(task: dict[str, Any]) -> dict[str, Any] | None:
    notes = task.get("notes") or ""
    for line in reversed(notes.splitlines()):
        if not line.startswith(META_PREFIX):
            continue
        try:
            payload = json.loads(line[len(META_PREFIX) :])
            if isinstance(payload, dict):
                return payload
        except ValueError:
            return None
    return None


def parse_task_meta(task: dict[str, Any]) -> tuple[date, dict[str, float]] | None:
    payload = _parse_meta_payload(task)
    if not payload:
        return None
    try:
        day = date.fromisoformat(payload["day"])
        raw_units = payload.get("units")
        if not isinstance(raw_units, dict):
            return None
        units = {str(k): round(float(v), 4) for k, v in raw_units.items() if float(v) > 0}
        return day, units
    except (ValueError, KeyError, TypeError):
        return None


def task_consumes_day(task: dict[str, Any]) -> bool:
    payload = _parse_meta_payload(task)
    if not payload:
        return True
    return payload.get("consume_day", True) is not False


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
        if task_consumes_day(task):
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
