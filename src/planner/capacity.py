"""Classify calendar days into free (1.0), class (0.25), or blocked (0.0)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from src.planner.models import DayPlan, EPS


def _merge_hours(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    cur_start, cur_end = ordered[0]
    total = 0.0
    for s, e in ordered[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = s, e
    return round(total + (cur_end - cur_start), 4)


def classify_days(
    start_date: date,
    end_date: date,
    events: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[DayPlan]:
    if start_date > end_date:
        return []

    rules = config.get("capacity_rules", {})
    free_units = float(rules.get("free_day_units", 1.0))
    class_max_hours = float(rules.get("class_day_max_hours", 3.5))
    class_units = float(rules.get("class_day_units", 0.25))
    blocked_units = float(rules.get("blocked_day_units", 0.0))
    keywords = [k.strip() for k in rules.get("blocked_keywords", []) if k.strip()]
    keyword_re = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")s?\b", re.IGNORECASE) if keywords else None

    total_days = (end_date - start_date).days + 1
    intervals_by_day: dict[date, list[tuple[float, float]]] = {start_date + timedelta(days=i): [] for i in range(total_days)}
    blocked_reason: dict[date, str] = {}

    for target in config.get("targets", []):
        if target.get("type") == "exam" and target.get("date"):
            exam_day = date.fromisoformat(target["date"])
            if exam_day in intervals_by_day:
                blocked_reason.setdefault(exam_day, f"exam: {target['name']}")

    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        summary = (ev.get("summary") or "").strip() or "(No title)"
        start_obj, end_obj = ev.get("start", {}), ev.get("end", {})
        kw_match = keyword_re.search(summary) if keyword_re else None
        is_transparent = ev.get("transparency") == "transparent"

        if "date" in start_obj and "dateTime" not in start_obj:
            d_start = date.fromisoformat(start_obj["date"])
            d_end = date.fromisoformat(end_obj["date"]) if end_obj.get("date") else d_start + timedelta(days=1)
            cur, stop = max(d_start, start_date), min(d_end, end_date + timedelta(days=1))
            while cur < stop:
                if kw_match:
                    blocked_reason.setdefault(cur, f"{kw_match.group(0).lower()}: {summary}")
                elif not is_transparent:
                    blocked_reason.setdefault(cur, f"all-day: {summary}")
                cur += timedelta(days=1)
            continue

        if "dateTime" not in start_obj:
            continue
        ev_start = datetime.fromisoformat(start_obj["dateTime"].replace("Z", "+00:00"))
        ev_end = datetime.fromisoformat(end_obj["dateTime"].replace("Z", "+00:00")) if end_obj.get("dateTime") else ev_start
        if ev_start.tzinfo and ev_end.tzinfo:
            ev_end = ev_end.astimezone(ev_start.tzinfo)

        cur_day = max(ev_start.date(), start_date)
        last_day = min(max(ev_end.date(), ev_start.date()), end_date)
        while cur_day <= last_day:
            day_start = datetime.combine(cur_day, time.min, tzinfo=ev_start.tzinfo)
            day_end = datetime.combine(cur_day + timedelta(days=1), time.min, tzinfo=ev_start.tzinfo)
            seg_start, seg_end = max(ev_start, day_start), min(ev_end, day_end)
            if seg_end > seg_start or (ev_end <= ev_start and cur_day == ev_start.date()):
                if kw_match:
                    blocked_reason.setdefault(cur_day, f"{kw_match.group(0).lower()}: {summary}")
                elif not is_transparent and seg_end > seg_start:
                    intervals_by_day[cur_day].append(
                        ((seg_start - day_start).total_seconds() / 3600.0, (seg_end - day_start).total_seconds() / 3600.0)
                    )
            cur_day += timedelta(days=1)

    plans: list[DayPlan] = []
    for day, intervals in intervals_by_day.items():
        hours = _merge_hours(intervals)
        if day in blocked_reason:
            plans.append(DayPlan(day, "blocked", blocked_units, hours, blocked_reason[day]))
        elif hours > class_max_hours + EPS:
            plans.append(DayPlan(day, "blocked", blocked_units, hours, f"{hours:.1f}h scheduled"))
        elif hours > EPS:
            plans.append(DayPlan(day, "class", class_units, hours, f"{hours:.1f}h scheduled"))
        else:
            plans.append(DayPlan(day, "free", free_units, 0.0, "free"))
    return plans
