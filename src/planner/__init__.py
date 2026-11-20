"""Pure Python deterministic study planner package."""

from src.planner.capacity import classify_days
from src.planner.formatter import format_plan, format_unit_ratio, format_units
from src.planner.models import Allocation, DayPlan, StudyPlan, TargetSummary, load_config, plan_window
from src.planner.scheduler import build_plan

__all__ = [
    "Allocation",
    "DayPlan",
    "StudyPlan",
    "TargetSummary",
    "build_plan",
    "classify_days",
    "format_plan",
    "format_unit_ratio",
    "format_units",
    "load_config",
    "plan_window",
]
