"""Plain-text rendering and unit label formatting for a computed StudyPlan."""

from __future__ import annotations

from src.planner.models import EPS, StudyPlan


def _fmt_num(value: float) -> str:
    cleaned = round(float(value), 4)
    if cleaned == 0:
        cleaned = 0.0
    return f"{cleaned:g}"


def format_units(value: float) -> str:
    cleaned = round(float(value), 4)
    word = "unit" if abs(cleaned - 1.0) < EPS else "units"
    return f"{_fmt_num(cleaned)} {word}"


def format_unit_ratio(done: float, total: float) -> str:
    cleaned_total = round(float(total), 4)
    word = "unit" if abs(cleaned_total - 1.0) < EPS else "units"
    return f"{_fmt_num(done)}/{_fmt_num(cleaned_total)} {word}"


def format_plan(plan: StudyPlan) -> str:
    total_cap = round(sum(d.capacity for d in plan.days), 4)
    total_sched = round(sum(a.units for d in plan.days for a in d.allocations), 4)
    counts = {k: sum(1 for d in plan.days if d.day_type == k) for k in ("free", "class", "blocked")}
    lines = [
        f"Study Plan: {plan.start_date} to {plan.end_date} ({len(plan.days)} days)",
        (
            f"Capacity: {format_units(total_cap)} total | {format_units(total_sched)} scheduled | "
            f"Free: {counts['free']} | Class: {counts['class']} | Blocked: {counts['blocked']}"
        ),
        "",
    ]
    for d in plan.days:
        tag = f"{d.day.isoformat()} ({d.day.strftime('%a')}) [{d.day_type:<7} {format_units(d.capacity)}]"
        if d.allocations:
            parts = [
                (
                    f"{a.name} ({format_units(a.units)}, {format_unit_ratio(a.cumulative, a.total)})"
                    if a.total is not None
                    else f"{a.name} ({format_units(a.units)})"
                )
                for a in d.allocations
            ]
            note = f"  [{d.reason}]" if d.day_type == "class" else ""
            lines.append(f"{tag} {' + '.join(parts)}{note}")
        elif d.day_type == "blocked":
            lines.append(f"{tag} -- ({d.reason})")
        else:
            lines.append(f"{tag} -- (open)")

    lines.extend(["", "Summary:"])
    for t in plan.targets:
        due = t.due_date.isoformat() if t.due_date else "TBD"
        kind = "exam" if t.target_type == "exam" else "due"
        lines.append(f"  [P{t.priority}] {t.name}: {format_unit_ratio(t.scheduled, t.required)} ({kind} {due})")
    if plan.optional_shed:
        lines.append("  [P3] Optional Paper Reading: 0 units (dropped -- tight schedule)")
    elif plan.optional_units > EPS:
        lines.append(f"  [P3] Optional Paper Reading: {format_units(plan.optional_units)}")
    if plan.unscheduled:
        shortfall = ", ".join(f"{name} ({format_units(units)})" for name, units in plan.unscheduled.items())
        lines.append(f"Unscheduled: {shortfall}")
    return "\n".join(lines)
