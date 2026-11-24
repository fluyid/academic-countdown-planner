"""Plain-text rendering and unit label formatting for a computed StudyPlan."""

from __future__ import annotations

from src.planner.models import Allocation, EPS, StudyPlan, reading_target_id


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


def format_papers(value: float) -> str:
    cleaned = round(float(value), 4)
    word = "paper" if abs(cleaned - 1.0) < EPS else "papers"
    return f"{_fmt_num(cleaned)} {word}"


def format_paper_ratio(done: float, total: float) -> str:
    cleaned_total = round(float(total), 4)
    word = "paper" if abs(cleaned_total - 1.0) < EPS else "papers"
    return f"{_fmt_num(done)}/{_fmt_num(cleaned_total)} {word}"


def format_pages(value: float) -> str:
    cleaned = round(float(value), 4)
    word = "page" if abs(cleaned - 1.0) < EPS else "pages"
    return f"{_fmt_num(cleaned)} {word}"


def format_allocation_amount(alloc: Allocation) -> str:
    base = format_units(alloc.units)
    return f"{base}, {format_pages(alloc.pages)}" if alloc.pages is not None else base


def format_plan(plan: StudyPlan) -> str:
    total_cap = round(sum(d.capacity for d in plan.days), 4)
    total_sched = round(sum(a.units for d in plan.days for a in d.allocations), 4)
    counts = {k: sum(1 for d in plan.days if d.day_type == k) for k in ("free", "class", "blocked")}
    paper_by_read_id = {reading_target_id(t.target_id): t for t in plan.targets if t.is_paper_only}
    paper_by_name = {t.name: t for t in plan.targets if t.is_paper_only}
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
            parts = []
            for a in d.allocations:
                pt = paper_by_read_id.get(a.target_id)
                if pt is not None and a.total is not None:
                    p_take = round(a.units / pt.units_per_paper, 4)
                    p_cum = round(a.cumulative / pt.units_per_paper, 4)
                    amt = f"{format_papers(p_take)}, {format_pages(a.pages)}" if a.pages is not None else format_papers(p_take)
                    parts.append(f"{a.name} ({amt}, {format_paper_ratio(p_cum, pt.paper_count)})")
                elif a.total is not None:
                    parts.append(f"{a.name} ({format_allocation_amount(a)}, {format_unit_ratio(a.cumulative, a.total)})")
                else:
                    parts.append(f"{a.name} ({format_allocation_amount(a)})")
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
        if t.is_paper_only:
            p_sched = round(t.reading_scheduled / t.units_per_paper, 4)
            lines.append(f"  [P{t.priority}] {t.name}: {format_paper_ratio(p_sched, t.paper_count)} ({kind} {due})")
            continue
        if t.reading_required > EPS:
            work_sched = round(max(0.0, t.scheduled - t.reading_scheduled), 4)
            work_req = round(max(0.0, t.required - t.reading_required), 4)
            detail = f"reading {format_unit_ratio(t.reading_scheduled, t.reading_required)} + work {format_unit_ratio(work_sched, work_req)}, {kind} {due}"
        else:
            detail = f"{kind} {due}"
        lines.append(f"  [P{t.priority}] {t.name}: {format_unit_ratio(t.scheduled, t.required)} ({detail})")
    if plan.optional_shed:
        lines.append("  [P3] Optional Paper Reading: 0 units (dropped -- tight schedule)")
    elif plan.optional_units > EPS:
        opt_pages = round(sum(a.pages or 0.0 for d in plan.days for a in d.allocations if a.target_id == "paper-reading"), 4)
        page_note = f" ({format_pages(opt_pages)})" if opt_pages > EPS else ""
        lines.append(f"  [P3] Optional Paper Reading: {format_units(plan.optional_units)}{page_note}")
    if plan.unscheduled:
        shortfall_parts = []
        for name, units in plan.unscheduled.items():
            pt = paper_by_name.get(name)
            label = format_papers(round(units / pt.units_per_paper, 4)) if pt is not None else format_units(units)
            shortfall_parts.append(f"{name} ({label})")
        lines.append(f"Unscheduled: {', '.join(shortfall_parts)}")
    return "\n".join(lines)
