"""End-to-end plain-language replanning: Gemini parse -> pure-Python apply/replan/sync/refresh -> Gemini explain."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from src.calendar_client import CONFIG_PATH, create_calendar_event, get_calendar_service, get_events_in_window
from src.gemini.actions import (
    AddEventAction,
    ParsedAction,
    TickTaskAction,
    UnclearAction,
    UpdateTargetDatesAction,
    parse_audio_action,
    parse_user_action,
)
from src.gemini.client import generate_content
from src.gemini.explain import explain_replan
from src.planner import StudyPlan, build_plan, load_config, plan_window
from src.tasks import (
    SyncResult,
    complete_task,
    get_or_create_tasklist,
    get_tasks_service,
    list_tasks,
    read_completed_progress,
    sync_plan_to_tasks,
)
from src.wallpaper import RefreshResult, refresh_screen


@dataclass
class ReplanResult:
    action: ParsedAction
    action_summary: str
    ticked_task: dict[str, str] | None
    created_event: dict[str, Any] | None
    sync_result: SyncResult | None
    refresh_result: RefreshResult | None
    before_plan: StudyPlan
    after_plan: StudyPlan
    explanation: str


def _format_event_summary(action: AddEventAction, created: dict[str, Any]) -> str:
    cal_name = created.get("_calendar_name") or action.calendar
    if action.all_day:
        when = (
            f"{action.start_date.isoformat()} to {action.end_date.isoformat()}"
            if action.end_date > action.start_date
            else action.start_date.isoformat()
        )
        return f"Added all-day event '{action.summary}' on {when} to calendar '{cal_name}'."
    return (
        f"Added event '{action.summary}' on {action.start_date.isoformat()} "
        f"{action.start_time}-{action.end_time} to calendar '{cal_name}'."
    )


def _replan(
    parse_action: Callable[[date, dict[str, str], list[dict[str, Any]], list[dict[str, Any]]], ParsedAction],
    *,
    config_path: Path = CONFIG_PATH,
    list_name: str | None = None,
    today: date | None = None,
    background: str | Path | None = None,
    apply_lock_screen: bool = True,
    calendar_service: Any = None,
    tasks_service: Any = None,
    call_gemini: Callable[..., str] = generate_content,
    refresh_fn: Callable[..., RefreshResult] = refresh_screen,
) -> ReplanResult:
    config = load_config(config_path)
    ref_day = today or date.today()
    start, end = plan_window(config, start_date=ref_day)
    target_list = list_name or config.get("tasks", {}).get("list_name", "Countdown Planner (Test)")

    cal_svc = get_calendar_service(calendar_service)
    task_svc = get_tasks_service(tasks_service)
    tasklist_id = get_or_create_tasklist(task_svc, target_list)

    events_before = get_events_in_window(start, end, config_path=config_path, service=cal_svc)
    tasks_before = list_tasks(task_svc, tasklist_id)
    done_units_before, done_days_before = read_completed_progress(tasks_before)
    before_plan = build_plan(
        config,
        events_before,
        start_date=start,
        completed_units=done_units_before,
        completed_days=done_days_before,
    )

    open_tasks = [t for t in tasks_before if t.get("status") != "completed" and t.get("id")]
    action = parse_action(ref_day, config.get("calendars", {}), open_tasks, config.get("targets", []))

    if isinstance(action, UnclearAction):
        return ReplanResult(
            action=action,
            action_summary=f"No changes made: {action.reason}",
            ticked_task=None,
            created_event=None,
            sync_result=None,
            refresh_result=None,
            before_plan=before_plan,
            after_plan=before_plan,
            explanation=action.reason,
        )

    created_event: dict[str, Any] | None = None
    ticked_task: dict[str, str] | None = None

    if isinstance(action, AddEventAction):
        created_event = create_calendar_event(
            action.summary,
            action.start_date,
            end_date=action.end_date,
            all_day=action.all_day,
            start_time=action.start_time,
            end_time=action.end_time,
            calendar_role=action.calendar,
            config_path=config_path,
            service=cal_svc,
        )
        action_summary = _format_event_summary(action, created_event)
    elif isinstance(action, TickTaskAction):
        complete_task(task_svc, tasklist_id, action.task_id)
        ticked_task = {"id": action.task_id, "title": action.task_title, "due": action.task_due}
        due_part = f" (due {action.task_due})" if action.task_due else ""
        action_summary = f"Ticked task: '{action.task_title}'{due_part}."
    elif isinstance(action, UpdateTargetDatesAction):
        by_id = {u.target_id: u for u in action.updates}
        target_types: dict[str, str] = {}
        for target in config.get("targets", []):
            upd = by_id.get(target.get("id"))
            if upd is not None:
                target["date"] = upd.due_date.isoformat()
                target_types[upd.target_id] = str(target.get("type") or "assignment")
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        # Recompute plan window so deadlines later than the prior anchor (e.g. Dec 18 > Dec 14) are included.
        start, end = plan_window(config, start_date=ref_day)
        existing_in_window = get_events_in_window(start, end, config_path=config_path, service=cal_svc)
        existing_keys = {
            ((ev.get("summary") or "").strip().lower(), ev.get("start", {}).get("date"))
            for ev in existing_in_window
            if ev.get("status") != "cancelled"
        }
        for upd in action.updates:
            is_exam = target_types.get(upd.target_id) == "exam"
            ev_summary = (
                upd.target_name
                if (is_exam and "exam" in upd.target_name.lower())
                else (f"{upd.target_name} Exam" if is_exam else f"{upd.target_name} Due")
            )
            key = (ev_summary.lower(), upd.due_date.isoformat())
            if key not in existing_keys:
                # Assignments use transparent all-day events so the deadline appears on IU without blocking study capacity.
                created_event = create_calendar_event(
                    ev_summary,
                    upd.due_date,
                    all_day=True,
                    calendar_role="university",
                    transparency=None if is_exam else "transparent",
                    config_path=config_path,
                    service=cal_svc,
                )
                existing_keys.add(key)
        parts = [f"{u.target_name} -> {u.due_date.isoformat()}" for u in action.updates]
        action_summary = f"Updated target dates (config + university calendar): {', '.join(parts)}."
    else:  # pragma: no cover - exhaustive guard
        raise ValueError(f"Unsupported action: {action!r}")

    # Re-read truth from Calendar + Tasks and recompute strictly in pure Python.
    events_after = get_events_in_window(start, end, config_path=config_path, service=cal_svc)
    tasks_after = list_tasks(task_svc, tasklist_id)
    done_units_after, done_days_after = read_completed_progress(tasks_after)
    after_plan = build_plan(
        config,
        events_after,
        start_date=start,
        completed_units=done_units_after,
        completed_days=done_days_after,
    )

    sync_res = sync_plan_to_tasks(task_svc, tasklist_id, target_list, after_plan, existing_tasks=tasks_after)
    refresh_res = refresh_fn(
        after_plan,
        config,
        today=ref_day,
        completed_units=done_units_after,
        completed_days=done_days_after,
        background=background,
        apply=apply_lock_screen,
    )
    explanation = explain_replan(action_summary, before_plan, after_plan, today=ref_day, call_gemini=call_gemini)

    return ReplanResult(
        action=action,
        action_summary=action_summary,
        ticked_task=ticked_task,
        created_event=created_event,
        sync_result=sync_res,
        refresh_result=refresh_res,
        before_plan=before_plan,
        after_plan=after_plan,
        explanation=explanation,
    )


def replan_from_text(
    utterance: str,
    *,
    config_path: Path = CONFIG_PATH,
    list_name: str | None = None,
    today: date | None = None,
    background: str | Path | None = None,
    apply_lock_screen: bool = True,
    calendar_service: Any = None,
    tasks_service: Any = None,
    call_gemini: Callable[..., str] = generate_content,
    refresh_fn: Callable[..., RefreshResult] = refresh_screen,
) -> ReplanResult:
    return _replan(
        lambda ref_day, cals, open_tasks, targets: parse_user_action(
            utterance,
            today=ref_day,
            calendars=cals,
            open_tasks=open_tasks,
            targets=targets,
            call_gemini=call_gemini,
        ),
        config_path=config_path,
        list_name=list_name,
        today=today,
        background=background,
        apply_lock_screen=apply_lock_screen,
        calendar_service=calendar_service,
        tasks_service=tasks_service,
        call_gemini=call_gemini,
        refresh_fn=refresh_fn,
    )


def replan_from_audio(
    audio_bytes: bytes,
    *,
    audio_mime_type: str = "audio/wav",
    config_path: Path = CONFIG_PATH,
    list_name: str | None = None,
    today: date | None = None,
    background: str | Path | None = None,
    apply_lock_screen: bool = True,
    calendar_service: Any = None,
    tasks_service: Any = None,
    call_gemini: Callable[..., str] = generate_content,
    refresh_fn: Callable[..., RefreshResult] = refresh_screen,
) -> ReplanResult:
    if not audio_bytes:
        raise ValueError("Audio clip is empty.")
    return _replan(
        lambda ref_day, cals, open_tasks, targets: parse_audio_action(
            audio_bytes,
            today=ref_day,
            calendars=cals,
            open_tasks=open_tasks,
            targets=targets,
            audio_mime_type=audio_mime_type,
            call_gemini=call_gemini,
        ),
        config_path=config_path,
        list_name=list_name,
        today=today,
        background=background,
        apply_lock_screen=apply_lock_screen,
        calendar_service=calendar_service,
        tasks_service=tasks_service,
        call_gemini=call_gemini,
        refresh_fn=refresh_fn,
    )
