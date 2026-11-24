"""Data structures and config/window helpers for the study planner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "targets.json"
EPS = 1e-9
PAGES_PER_UNIT = 40.0
DEFAULT_MAX_READING_PAGES_PER_DAY = 40.0


def reading_target_id(target_id: str) -> str:
    return f"{target_id}:reading"


@dataclass
class Allocation:
    target_id: str
    name: str
    priority: int
    units: float
    cumulative: float
    total: float | None
    pages: float | None = None
    is_reading: bool = False


@dataclass
class DayPlan:
    day: date
    day_type: str
    capacity: float
    scheduled_hours: float
    reason: str
    allocations: list[Allocation] = field(default_factory=list)


@dataclass
class TargetSummary:
    target_id: str
    name: str
    target_type: str
    priority: int
    due_date: date | None
    scheduled: float
    required: float
    reading_scheduled: float = 0.0
    reading_required: float = 0.0


@dataclass
class StudyPlan:
    start_date: date
    end_date: date
    days: list[DayPlan]
    targets: list[TargetSummary]
    optional_units: float
    optional_shed: bool
    unscheduled: dict[str, float]


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(config_path.read_text(encoding="utf-8"))


def plan_window(config: dict[str, Any], start_date: date | None = None) -> tuple[date, date]:
    start = start_date or date.today()
    dates = [
        date.fromisoformat(d)
        for d in [config.get("window", {}).get("anchor_date"), *(t.get("date") for t in config.get("targets", []))]
        if d
    ]
    return start, max(dates) if dates else start
