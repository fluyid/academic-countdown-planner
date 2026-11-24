"""Deterministic unit spreading and priority load-shedding (zero LLM)."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.planner.capacity import classify_days
from src.planner.models import (
    Allocation,
    DEFAULT_MAX_READING_PAGES_PER_DAY,
    DayPlan,
    EPS,
    PAGES_PER_UNIT,
    StudyPlan,
    TargetSummary,
    plan_window,
    reading_target_id,
)

# shortcut: default 2.0 units when assignment units is null; set explicit units in targets.json to override
DEFAULT_ASSIGNMENT_UNITS = 2.0


def _required_paper_spec(target: dict[str, Any], pages_per_unit: float) -> tuple[float, float]:
    spec = target.get("required_papers")
    if not isinstance(spec, dict):
        return 0.0, 0.0
    count = int(spec.get("count") or 0)
    pages = float(spec.get("pages_per_paper") or 0.0)
    if count <= 0 or pages <= EPS:
        return 0.0, 0.0
    return round((count * pages) / pages_per_unit, 4), round(pages / pages_per_unit, 4)


def _append_allocation(day_plan: DayPlan, alloc: Allocation) -> None:
    for existing in day_plan.allocations:
        if existing.target_id == alloc.target_id and existing.is_reading == alloc.is_reading:
            existing.units = round(existing.units + alloc.units, 4)
            existing.cumulative = alloc.cumulative
            if alloc.pages is not None:
                existing.pages = round((existing.pages or 0.0) + alloc.pages, 4)
            return
    day_plan.allocations.append(alloc)


def _reading_label(target_name: str) -> str:
    return f"{target_name} \u2014 Required Paper Reading"


def build_plan(
    config: dict[str, Any],
    events: list[dict[str, Any]],
    start_date: date | None = None,
    completed_units: dict[str, float] | None = None,
    completed_days: set[date] | None = None,
) -> StudyPlan:
    start, end = plan_window(config, start_date)
    days = classify_days(start, end, events, config)
    done_units = completed_units or {}
    done_days = completed_days or set()

    rules = config.get("capacity_rules", {})
    pages_per_unit = float(rules.get("pages_per_unit", PAGES_PER_UNIT))
    if pages_per_unit <= EPS:
        pages_per_unit = PAGES_PER_UNIT
    max_reading_pages = max(0.0, float(rules.get("max_reading_pages_per_day", DEFAULT_MAX_READING_PAGES_PER_DAY)))
    max_reading_units = round(max_reading_pages / pages_per_unit, 4)

    remaining_cap = [0.0 if d.day in done_days else d.capacity for d in days]
    remaining_reading = [0.0 if d.day in done_days or d.capacity <= EPS else max_reading_units for d in days]

    targets = list(enumerate(config.get("targets", [])))
    by_deadline = lambda p: (date.fromisoformat(p[1]["date"]) if p[1].get("date") else date.max, p[0])
    exams = sorted([(i, t) for i, t in targets if t.get("type") == "exam"], key=by_deadline)
    assignments = sorted([(i, t) for i, t in targets if t.get("type") != "exam"], key=by_deadline)

    scheduled_by_id: dict[str, float] = {}
    required_by_id: dict[str, float] = {}
    reading_scheduled_by_id: dict[str, float] = {}
    reading_required_by_id: dict[str, float] = {}
    unscheduled: dict[str, float] = {}

    def record_shortfall(name: str, amount: float) -> None:
        if amount > EPS:
            unscheduled[name] = round(unscheduled.get(name, 0.0) + amount, 4)

    def allocate_stream(
        tid: str,
        name: str,
        prio: int,
        required: float,
        deadline: date | None,
        strict_before: bool,
        *,
        is_reading: bool = False,
        step_units: float | None = None,
    ) -> tuple[float, float]:
        prior = round(max(0.0, float(done_units.get(tid, 0.0))), 4)
        done, needed = prior, round(max(0.0, required - prior), 4)
        for i, day_plan in enumerate(days):
            if needed <= EPS:
                break
            if deadline and (day_plan.day >= deadline if strict_before else day_plan.day > deadline):
                continue
            avail = min(remaining_cap[i], remaining_reading[i]) if is_reading else remaining_cap[i]
            while needed > EPS and avail > EPS:
                take = round(min(needed, avail, step_units) if step_units is not None else min(needed, avail), 4)
                if take <= EPS:
                    break
                done = round(done + take, 4)
                needed = round(needed - take, 4)
                remaining_cap[i] = round(remaining_cap[i] - take, 4)
                if is_reading:
                    remaining_reading[i] = round(remaining_reading[i] - take, 4)
                pages = round(take * pages_per_unit, 4) if is_reading else None
                _append_allocation(day_plan, Allocation(tid, name, prio, take, done, required, pages=pages, is_reading=is_reading))
                avail = min(remaining_cap[i], remaining_reading[i]) if is_reading else remaining_cap[i]
        return done, needed

    # Priority 1 (protected): exams earliest-deadline-first, strictly before exam date.
    for _, t in exams:
        tid, name, prio = t["id"], t["name"], int(t.get("priority", 1))
        req = float(t.get("units") or 0.0)
        deadline = date.fromisoformat(t["date"]) if t.get("date") else None
        done, needed = allocate_stream(tid, name, prio, req, deadline, strict_before=True)
        scheduled_by_id[tid], required_by_id[tid] = done, req
        record_shortfall(name, needed)

    # Priority 2 (never dropped when tight): dated assignments (required reading then work), then undated round-robin.
    for _, t in assignments:
        if not t.get("date"):
            continue
        tid, name, prio = t["id"], t["name"], int(t.get("priority", 2))
        deadline = date.fromisoformat(t["date"])
        read_req, paper_units = _required_paper_spec(t, pages_per_unit)
        work_req = float(t["units"]) if t.get("units") is not None else DEFAULT_ASSIGNMENT_UNITS
        read_done, read_needed = (
            allocate_stream(
                reading_target_id(tid),
                _reading_label(name),
                prio,
                read_req,
                deadline,
                strict_before=False,
                is_reading=True,
                step_units=paper_units,
            )
            if read_req > EPS
            else (0.0, 0.0)
        )
        work_done, work_needed = allocate_stream(tid, name, prio, work_req, deadline, strict_before=False)
        reading_scheduled_by_id[tid], reading_required_by_id[tid] = read_done, read_req
        scheduled_by_id[tid] = round(read_done + work_done, 4)
        required_by_id[tid] = round(read_req + work_req, 4)
        record_shortfall(name, round(read_needed + work_needed, 4))

    undated = [t for _, t in assignments if not t.get("date")]
    if undated:
        read_spec = {t["id"]: _required_paper_spec(t, pages_per_unit) for t in undated}
        read_req_map = {tid: spec[0] for tid, spec in read_spec.items()}
        paper_step_map = {tid: spec[1] for tid, spec in read_spec.items()}
        read_done_map = {
            tid: round(max(0.0, float(done_units.get(reading_target_id(tid), 0.0))), 4) if read_req_map[tid] > EPS else 0.0
            for tid in read_req_map
        }
        read_needed_map = {tid: round(max(0.0, read_req_map[tid] - read_done_map[tid]), 4) for tid in read_req_map}

        # Schedule undated required paper reading first (round-robin by paper, bounded by daily reading cap).
        cursor = 0
        for i, day_plan in enumerate(days):
            while remaining_cap[i] > EPS and remaining_reading[i] > EPS:
                active = [k for k in range(len(undated)) if read_needed_map[undated[(cursor + k) % len(undated)]["id"]] > EPS]
                if not active:
                    break
                pick = (cursor + active[0]) % len(undated)
                t = undated[pick]
                tid = t["id"]
                avail = min(remaining_cap[i], remaining_reading[i])
                take = round(min(read_needed_map[tid], paper_step_map[tid], avail), 4)
                if take <= EPS:
                    break
                read_done_map[tid] = round(read_done_map[tid] + take, 4)
                read_needed_map[tid] = round(read_needed_map[tid] - take, 4)
                remaining_cap[i] = round(remaining_cap[i] - take, 4)
                remaining_reading[i] = round(remaining_reading[i] - take, 4)
                _append_allocation(
                    day_plan,
                    Allocation(
                        reading_target_id(tid),
                        _reading_label(t["name"]),
                        int(t.get("priority", 2)),
                        take,
                        read_done_map[tid],
                        read_req_map[tid],
                        pages=round(take * pages_per_unit, 4),
                        is_reading=True,
                    ),
                )
                cursor = (pick + 1) % len(undated)

        # Then schedule undated assignment work round-robin on remaining capacity.
        work_req_map = {t["id"]: float(t["units"]) if t.get("units") is not None else DEFAULT_ASSIGNMENT_UNITS for t in undated}
        work_done_map = {t["id"]: round(max(0.0, float(done_units.get(t["id"], 0.0))), 4) for t in undated}
        work_needed_map = {tid: round(max(0.0, work_req_map[tid] - work_done_map[tid]), 4) for tid in work_req_map}
        cursor = 0
        for i, day_plan in enumerate(days):
            while remaining_cap[i] > EPS:
                active = [k for k in range(len(undated)) if work_needed_map[undated[(cursor + k) % len(undated)]["id"]] > EPS]
                if not active:
                    break
                pick = (cursor + active[0]) % len(undated)
                t = undated[pick]
                tid = t["id"]
                take = round(min(work_needed_map[tid], remaining_cap[i]), 4)
                work_done_map[tid] = round(work_done_map[tid] + take, 4)
                work_needed_map[tid] = round(work_needed_map[tid] - take, 4)
                remaining_cap[i] = round(remaining_cap[i] - take, 4)
                _append_allocation(
                    day_plan,
                    Allocation(tid, t["name"], int(t.get("priority", 2)), take, work_done_map[tid], work_req_map[tid]),
                )
                cursor = (pick + 1) % len(undated)

        for t in undated:
            tid = t["id"]
            reading_scheduled_by_id[tid], reading_required_by_id[tid] = read_done_map[tid], read_req_map[tid]
            scheduled_by_id[tid] = round(read_done_map[tid] + work_done_map[tid], 4)
            required_by_id[tid] = round(read_req_map[tid] + work_req_map[tid], 4)
            record_shortfall(t["name"], round(read_needed_map[tid] + work_needed_map[tid], 4))

    # Priority 3 (first to drop when tight): optional paper reading on leftover capacity within daily reading cap.
    paper = config.get("optional_work", {}).get("paper_reading")
    optional_units = 0.0
    if paper:
        label = f"Optional Paper Reading ({paper.get('description', 'Read abstract + introduction and keep a note')})"
        prio = int(paper.get("priority", 3))
        for i, day_plan in enumerate(days):
            take = round(min(remaining_cap[i], remaining_reading[i]), 4)
            if take <= EPS:
                continue
            optional_units = round(optional_units + take, 4)
            remaining_cap[i] = round(remaining_cap[i] - take, 4)
            remaining_reading[i] = round(remaining_reading[i] - take, 4)
            _append_allocation(
                day_plan,
                Allocation(
                    "paper-reading",
                    label,
                    prio,
                    take,
                    optional_units,
                    None,
                    pages=round(take * pages_per_unit, 4),
                    is_reading=True,
                ),
            )

    summaries = []
    for _, t in targets:
        spec = t.get("required_papers")
        p_count = int(spec.get("count") or 0) if isinstance(spec, dict) else 0
        _, p_units = _required_paper_spec(t, pages_per_unit)
        summaries.append(
            TargetSummary(
                target_id=t["id"],
                name=t["name"],
                target_type=t.get("type", "assignment"),
                priority=int(t.get("priority", 2)),
                due_date=date.fromisoformat(t["date"]) if t.get("date") else None,
                scheduled=scheduled_by_id.get(t["id"], 0.0),
                required=required_by_id.get(t["id"], 0.0),
                reading_scheduled=reading_scheduled_by_id.get(t["id"], 0.0),
                reading_required=reading_required_by_id.get(t["id"], 0.0),
                paper_count=p_count if p_units > EPS else 0,
                units_per_paper=p_units,
            )
        )
    return StudyPlan(
        start_date=start,
        end_date=end,
        days=days,
        targets=summaries,
        optional_units=optional_units,
        optional_shed=bool(paper) and optional_units <= EPS,
        unscheduled=unscheduled,
    )
