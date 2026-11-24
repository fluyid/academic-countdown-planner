"""Pure StudyPlan -> display view-model builder (zero I/O, zero LLM)."""

from __future__ import annotations

from datetime import date
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.planner.formatter import format_pages, format_paper_ratio, format_papers, format_unit_ratio, format_units
from src.planner.models import Allocation, DayPlan, EPS, StudyPlan, TargetSummary, reading_target_id

MAX_MILESTONES = 6
DEFAULT_CARD_OPACITY = 0.28
MIN_CARD_OPACITY = 0.2
MAX_CARD_OPACITY = 0.95
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BACKGROUND_PATH = REPO_ROOT / "assets" / "background.jpg"


def format_short_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def format_full_date(d: date) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def format_day_heading(d: date) -> str:
    return f"{d.strftime('%a')}, {d.strftime('%b')} {d.day}"


def clamp_card_opacity(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return DEFAULT_CARD_OPACITY
    try:
        num = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CARD_OPACITY
    if not math.isfinite(num):
        return DEFAULT_CARD_OPACITY
    return round(max(MIN_CARD_OPACITY, min(MAX_CARD_OPACITY, num)), 4)


def _resolve_background(background: str | Path | None) -> str | None:
    if not background:
        return None
    raw = str(background).strip()
    if not raw:
        return None
    scheme = urlparse(raw).scheme.lower()
    if scheme in ("http", "https", "file"):
        return raw
    if scheme == "data":
        return raw if raw.lower().startswith("data:image/") else None
    if len(scheme) > 1:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and (REPO_ROOT / path).is_file():
        path = REPO_ROOT / path
    return path.resolve().as_uri()


def _pick_countdown(plan: StudyPlan, config: dict[str, Any], today: date) -> dict[str, Any]:
    window = config.get("window", {})
    anchor_raw = window.get("anchor_date")
    anchor_date = date.fromisoformat(anchor_raw) if anchor_raw else None
    anchor_label = str(window.get("anchor_label") or "Next Target")

    upcoming_dated = sorted(
        (t for t in plan.targets if t.due_date is not None and t.due_date >= today),
        key=lambda t: (t.due_date, t.priority, t.name),
    )
    if upcoming_dated:
        primary = upcoming_dated[0]
        target_date = primary.due_date
        label = anchor_label if anchor_date == target_date and anchor_label else primary.name
    elif anchor_date is not None:
        target_date = anchor_date
        label = anchor_label
    else:
        return {
            "label": anchor_label,
            "target_iso": None,
            "date_label": "Date TBD",
            "days_left": 0,
            "days_unit": "DAYS",
        }

    days_left = max(0, (target_date - today).days)
    return {
        "label": label,
        "target_iso": target_date.isoformat(),
        "date_label": format_full_date(target_date),
        "days_left": days_left,
        "days_unit": "DAY" if days_left == 1 else "DAYS",
    }


def _allocation_item(alloc: Allocation, paper_targets: dict[str, TargetSummary] | None = None) -> dict[str, Any]:
    is_optional = alloc.target_id == "paper-reading"
    name = "Optional Paper Reading" if is_optional else alloc.name
    pt = (paper_targets or {}).get(alloc.target_id)
    if pt is not None and alloc.total is not None:
        p_take = round(alloc.units / pt.units_per_paper, 4)
        p_cum = round(alloc.cumulative / pt.units_per_paper, 4)
        units = f"{format_papers(p_take)} ({format_pages(alloc.pages)})" if alloc.pages is not None else format_papers(p_take)
        progress = format_paper_ratio(p_cum, pt.paper_count)
    else:
        units = f"{format_units(alloc.units)} ({format_pages(alloc.pages)})" if alloc.pages is not None else format_units(alloc.units)
        progress = format_unit_ratio(alloc.cumulative, alloc.total) if alloc.total is not None else "Optional"
    return {
        "name": name,
        "units": units,
        "progress": progress,
        "is_optional": is_optional,
    }


def _next_study_preview(plan: StudyPlan, today: date) -> str | None:
    nxt = next((d for d in plan.days if d.day > today and d.allocations), None)
    if not nxt:
        return None
    first = "Optional Paper Reading" if nxt.allocations[0].target_id == "paper-reading" else nxt.allocations[0].name
    extra = f" +{len(nxt.allocations) - 1}" if len(nxt.allocations) > 1 else ""
    return f"Next: {format_day_heading(nxt.day)} \u00b7 {first}{extra}"


def _build_today(plan: StudyPlan, today: date, completed_days: set[date]) -> dict[str, Any]:
    day_plan: DayPlan | None = next((d for d in plan.days if d.day == today), None)
    next_preview = _next_study_preview(plan, today)
    base = {
        "date_label": format_day_heading(today),
        "next_preview": next_preview,
        "items": [],
        "badge": "",
        "headline": "",
        "detail": "",
    }
    if today in completed_days:
        return {**base, "status": "done", "badge": "Done", "headline": "Done for today", "detail": "Logged in Google Tasks"}
    if day_plan is None:
        return {**base, "status": "open", "badge": "Rest", "headline": "Outside study window"}
    if day_plan.allocations:
        kind = "Free day" if day_plan.day_type == "free" else "Class day"
        badge = f"{kind} \u00b7 {format_units(day_plan.capacity)}"
        detail = day_plan.reason if day_plan.day_type == "class" and day_plan.reason else ""
        paper_targets = {reading_target_id(t.target_id): t for t in plan.targets if t.is_paper_only}
        return {
            **base,
            "status": "study",
            "badge": badge,
            "headline": "",
            "detail": detail,
            "items": [_allocation_item(a, paper_targets) for a in day_plan.allocations],
        }
    if day_plan.day_type == "blocked":
        return {
            **base,
            "status": "blocked",
            "badge": "Blocked",
            "headline": "Blocked day",
            "detail": day_plan.reason or "No study capacity",
        }
    return {**base, "status": "open", "badge": "Open", "headline": "All caught up"}


def _build_milestones(
    targets: list[TargetSummary],
    completed_units: dict[str, float],
    max_milestones: int = MAX_MILESTONES,
) -> list[dict[str, Any]]:
    ordered = sorted(
        enumerate(targets),
        key=lambda pair: (pair[1].due_date is None, pair[1].due_date or date.max, pair[1].priority, pair[0]),
    )
    milestones: list[dict[str, Any]] = []
    for _, t in ordered[:max_milestones]:
        done = round(
            max(0.0, float(completed_units.get(t.target_id, 0.0)))
            + max(0.0, float(completed_units.get(reading_target_id(t.target_id), 0.0))),
            4,
        )
        req = round(max(0.0, float(t.required)), 4)
        pct = min(100, round((done / req) * 100)) if req > EPS else 0
        if t.is_paper_only:
            progress_label = format_paper_ratio(round(done / t.units_per_paper, 4), t.paper_count)
        else:
            progress_label = format_unit_ratio(done, req) if req > EPS else "Open"
        milestones.append(
            {
                "name": t.name,
                "kind": "Exam" if t.target_type == "exam" else "Assignment",
                "date_label": format_short_date(t.due_date) if t.due_date else "Date TBD",
                "progress_label": progress_label,
                "percent": pct,
                "done": req > EPS and done + EPS >= req,
            }
        )
    return milestones


def _status_note(plan: StudyPlan) -> str | None:
    if plan.unscheduled:
        paper_by_name = {t.name: t for t in plan.targets if t.is_paper_only}
        parts = []
        for name, u in plan.unscheduled.items():
            pt = paper_by_name.get(name)
            amt = format_papers(round(u / pt.units_per_paper, 4)) if pt is not None else format_units(u)
            parts.append(f"{name} ({amt})")
        return f"Tight schedule \u00b7 Unscheduled: {', '.join(parts)}"
    if plan.optional_shed:
        return "Tight schedule \u00b7 Optional reading paused"
    return None


def build_display_data(
    plan: StudyPlan,
    config: dict[str, Any],
    *,
    today: date | None = None,
    completed_units: dict[str, float] | None = None,
    completed_days: set[date] | None = None,
    background: str | Path | None = None,
    max_milestones: int = MAX_MILESTONES,
) -> dict[str, Any]:
    ref_day = today or plan.start_date
    done_units = completed_units or {}
    done_days = completed_days or set()
    display_cfg = config.get("display") or {}
    if background is not None:
        bg_source: str | Path | None = background
    else:
        cfg_bg = display_cfg.get("background")
        bg_source = cfg_bg if cfg_bg is not None else (DEFAULT_BACKGROUND_PATH if DEFAULT_BACKGROUND_PATH.is_file() else None)
    return {
        "generated_for": ref_day.isoformat(),
        "countdown": _pick_countdown(plan, config, ref_day),
        "today": _build_today(plan, ref_day, done_days),
        "milestones": _build_milestones(plan.targets, done_units, max_milestones=max_milestones),
        "status_note": _status_note(plan),
        "background_url": _resolve_background(bg_source),
        "card_opacity": clamp_card_opacity(display_cfg.get("card_opacity")),
    }
