"""Deterministic unit spreading and priority load-shedding (zero LLM)."""

from __future__ import annotations

from datetime import date
from typing import Any

from src.planner.capacity import classify_days
from src.planner.models import Allocation, EPS, StudyPlan, TargetSummary, plan_window

# shortcut: default 2.0 units when assignment units is null; set explicit units in targets.json to override
DEFAULT_ASSIGNMENT_UNITS = 2.0


def build_plan(
    config: dict[str, Any],
    events: list[dict[str, Any]],
    start_date: date | None = None,
) -> StudyPlan:
    start, end = plan_window(config, start_date)
    days = classify_days(start, end, events, config)
    remaining_cap = [d.capacity for d in days]

    targets = list(enumerate(config.get("targets", [])))
    by_deadline = lambda p: (date.fromisoformat(p[1]["date"]) if p[1].get("date") else date.max, p[0])
    exams = sorted([(i, t) for i, t in targets if t.get("type") == "exam"], key=by_deadline)
    assignments = sorted([(i, t) for i, t in targets if t.get("type") != "exam"], key=by_deadline)

    scheduled_by_id: dict[str, float] = {}
    required_by_id: dict[str, float] = {}
    unscheduled: dict[str, float] = {}

    def allocate_target(t: dict[str, Any], required: float, strict_before: bool) -> None:
        tid, name, prio = t["id"], t["name"], int(t.get("priority", 1))
        deadline = date.fromisoformat(t["date"]) if t.get("date") else None
        done, needed = 0.0, required
        for i, day_plan in enumerate(days):
            if needed <= EPS:
                break
            if deadline and (day_plan.day >= deadline if strict_before else day_plan.day > deadline):
                continue
            if remaining_cap[i] <= EPS:
                continue
            take = round(min(needed, remaining_cap[i]), 4)
            done = round(done + take, 4)
            needed = round(needed - take, 4)
            remaining_cap[i] = round(remaining_cap[i] - take, 4)
            day_plan.allocations.append(Allocation(tid, name, prio, take, done, required))
        scheduled_by_id[tid], required_by_id[tid] = done, required
        if needed > EPS:
            unscheduled[name] = needed

    # Priority 1 (protected): exams earliest-deadline-first, strictly before exam date.
    for _, t in exams:
        allocate_target(t, float(t.get("units") or 0.0), strict_before=True)

    # Priority 2: dated assignments first (EDF), then undated assignments round-robin.
    for _, t in assignments:
        if t.get("date"):
            req = float(t["units"]) if t.get("units") is not None else DEFAULT_ASSIGNMENT_UNITS
            allocate_target(t, req, strict_before=False)

    undated = [t for _, t in assignments if not t.get("date")]
    if undated:
        req_map = {t["id"]: float(t["units"]) if t.get("units") is not None else DEFAULT_ASSIGNMENT_UNITS for t in undated}
        done_map = {t["id"]: 0.0 for t in undated}
        needed_map = dict(req_map)
        cursor = 0
        for i, day_plan in enumerate(days):
            while remaining_cap[i] > EPS:
                active = [k for k in range(len(undated)) if needed_map[undated[(cursor + k) % len(undated)]["id"]] > EPS]
                if not active:
                    break
                pick = (cursor + active[0]) % len(undated)
                t = undated[pick]
                tid = t["id"]
                take = round(min(needed_map[tid], remaining_cap[i]), 4)
                done_map[tid] = round(done_map[tid] + take, 4)
                needed_map[tid] = round(needed_map[tid] - take, 4)
                remaining_cap[i] = round(remaining_cap[i] - take, 4)
                day_plan.allocations.append(Allocation(tid, t["name"], int(t.get("priority", 2)), take, done_map[tid], req_map[tid]))
                cursor = (pick + 1) % len(undated)
        for t in undated:
            tid = t["id"]
            scheduled_by_id[tid], required_by_id[tid] = done_map[tid], req_map[tid]
            if needed_map[tid] > EPS:
                unscheduled[t["name"]] = needed_map[tid]

    # Priority 3 (first to drop when tight): optional paper reading on leftover capacity.
    paper = config.get("optional_work", {}).get("paper_reading")
    optional_units = 0.0
    if paper:
        label = f"Paper Reading ({paper.get('description', 'Read abstract + introduction and keep a note')})"
        prio = int(paper.get("priority", 3))
        for i, day_plan in enumerate(days):
            if remaining_cap[i] <= EPS:
                continue
            take = round(remaining_cap[i], 4)
            optional_units = round(optional_units + take, 4)
            remaining_cap[i] = 0.0
            day_plan.allocations.append(Allocation("paper-reading", label, prio, take, optional_units, None))

    summaries = [
        TargetSummary(
            target_id=t["id"],
            name=t["name"],
            target_type=t.get("type", "assignment"),
            priority=int(t.get("priority", 2)),
            due_date=date.fromisoformat(t["date"]) if t.get("date") else None,
            scheduled=scheduled_by_id.get(t["id"], 0.0),
            required=required_by_id.get(t["id"], 0.0),
        )
        for _, t in targets
    ]
    return StudyPlan(
        start_date=start,
        end_date=end,
        days=days,
        targets=summaries,
        optional_units=optional_units,
        optional_shed=bool(paper) and optional_units <= EPS,
        unscheduled=unscheduled,
    )
