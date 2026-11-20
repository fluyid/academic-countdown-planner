"""Gemini Job 2: explain the pure-Python recomputed plan in plain language (zero arithmetic)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from src.gemini.client import generate_content
from src.planner.models import DayPlan, EPS, StudyPlan

EXPLAIN_MAX_OUTPUT_TOKENS = 220

_SYSTEM_PROMPT = (
    "You explain an updated study plan to a student in 2 to 4 concise, encouraging sentences. "
    "Use ONLY the pre-computed facts provided below. Never do arithmetic, never change numbers, "
    "and never invent dates or tasks. If a task was ticked, state its exact title."
)


def _day_label(day_plan: DayPlan) -> str:
    if day_plan.allocations:
        return " + ".join(
            f"{a.name} ({round(a.units, 4):g}u, {round(a.cumulative, 4):g}/{round(a.total, 4):g})"
            if a.total is not None
            else f"{a.name} ({round(a.units, 4):g}u)"
            for a in day_plan.allocations
        )
    if day_plan.day_type == "blocked":
        return f"blocked ({day_plan.reason})"
    return "open"


def summarize_plan_changes(
    before: StudyPlan,
    after: StudyPlan,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    ref_day = today or after.start_date
    before_by_day = {d.day: d for d in before.days}
    changed_days: list[dict[str, str]] = []
    newly_blocked: list[dict[str, str]] = []

    for day_plan in after.days:
        prev = before_by_day.get(day_plan.day)
        after_lbl = _day_label(day_plan)
        before_lbl = _day_label(prev) if prev else "(outside previous window)"
        if day_plan.day_type == "blocked" and (not prev or prev.day_type != "blocked"):
            newly_blocked.append({"date": day_plan.day.isoformat(), "reason": day_plan.reason})
        if after_lbl != before_lbl:
            changed_days.append({"date": day_plan.day.isoformat(), "before": before_lbl, "after": after_lbl})

    today_plan = next((d for d in after.days if d.day == ref_day), None)
    next_study = next((d for d in after.days if d.day >= ref_day and d.allocations), None)

    return {
        "today": ref_day.isoformat(),
        "today_plan": _day_label(today_plan) if today_plan else "outside window",
        "next_study_day": (
            {"date": next_study.day.isoformat(), "plan": _day_label(next_study)} if next_study else None
        ),
        "newly_blocked_days": newly_blocked,
        "changed_days_count": len(changed_days),
        "changed_days_sample": changed_days[:6],
        "targets": [
            {
                "name": t.name,
                "scheduled": round(t.scheduled, 4),
                "required": round(t.required, 4),
                "due": t.due_date.isoformat() if t.due_date else "TBD",
            }
            for t in after.targets
        ],
        "optional_paper_reading": (
            "dropped (tight schedule)"
            if after.optional_shed
            else f"{round(after.optional_units, 4):g}u scheduled"
            if after.optional_units > EPS
            else "none"
        ),
        "unscheduled_shortfall": {k: round(v, 4) for k, v in after.unscheduled.items()},
    }


def explain_replan(
    action_summary: str,
    before: StudyPlan,
    after: StudyPlan,
    *,
    today: date | None = None,
    call_gemini: Callable[..., str] = generate_content,
) -> str:
    facts = {
        "action_applied": action_summary,
        "recomputed_plan": summarize_plan_changes(before, after, today=today),
    }
    prompt = "PRE-COMPUTED FACTS:\n" + json.dumps(facts, indent=2)
    return call_gemini(
        prompt,
        system=_SYSTEM_PROMPT,
        max_output_tokens=EXPLAIN_MAX_OUTPUT_TOKENS,
        temperature=0.2,
    )
