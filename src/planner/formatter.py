"""Plain-text rendering for a computed StudyPlan."""

from __future__ import annotations

from src.planner.models import EPS, StudyPlan


def format_plan(plan: StudyPlan) -> str:
    total_cap = round(sum(d.capacity for d in plan.days), 4)
    total_sched = round(sum(a.units for d in plan.days for a in d.allocations), 4)
    counts = {k: sum(1 for d in plan.days if d.day_type == k) for k in ("free", "class", "blocked")}
    lines = [
        f"Study Plan: {plan.start_date} to {plan.end_date} ({len(plan.days)} days)",
        f"Capacity: {total_cap:.2f}u total | {total_sched:.2f}u scheduled | Free: {counts['free']} | Class: {counts['class']} | Blocked: {counts['blocked']}",
        "",
    ]
    for d in plan.days:
        tag = f"{d.day.isoformat()} ({d.day.strftime('%a')}) [{d.day_type:<7} {d.capacity:.2f}u]"
        if d.allocations:
            parts = [
                f"{a.name} ({a.units:.2f}u, {a.cumulative:.2f}/{a.total:.2f})" if a.total is not None else f"{a.name} ({a.units:.2f}u)"
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
        lines.append(f"  [P{t.priority}] {t.name}: {t.scheduled:.2f}/{t.required:.2f}u ({kind} {due})")
    if plan.optional_shed:
        lines.append("  [P3] Optional Paper Reading: 0.00u (dropped -- tight schedule)")
    elif plan.optional_units > EPS:
        lines.append(f"  [P3] Optional Paper Reading: {plan.optional_units:.2f}u")
    if plan.unscheduled:
        shortfall = ", ".join(f"{name} ({units:.2f}u)" for name, units in plan.unscheduled.items())
        lines.append(f"Unscheduled: {shortfall}")
    return "\n".join(lines)
