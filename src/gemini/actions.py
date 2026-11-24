"""Gemini Job 1: translate plain language into a structured Calendar event or Task tick."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Callable

from src.gemini.client import generate_content

PARSE_MAX_OUTPUT_TOKENS = 256


@dataclass(frozen=True)
class AddEventAction:
    summary: str
    start_date: date
    end_date: date
    all_day: bool = True
    start_time: str | None = None
    end_time: str | None = None
    calendar: str = "personal"


@dataclass(frozen=True)
class TickTaskAction:
    task_id: str
    task_title: str
    task_due: str


@dataclass(frozen=True)
class TargetDateUpdate:
    target_id: str
    target_name: str
    due_date: date


@dataclass(frozen=True)
class UpdateTargetDatesAction:
    updates: tuple[TargetDateUpdate, ...]


@dataclass(frozen=True)
class UnclearAction:
    reason: str


ParsedAction = AddEventAction | TickTaskAction | UpdateTargetDatesAction | UnclearAction

ACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "action": {
            "type": "STRING",
            "enum": ["add_event", "tick_task", "update_target_dates", "unclear"],
        },
        "calendar": {"type": "STRING", "enum": ["personal", "university"], "nullable": True},
        "summary": {"type": "STRING", "nullable": True},
        "date": {"type": "STRING", "nullable": True},
        "end_date": {"type": "STRING", "nullable": True},
        "all_day": {"type": "BOOLEAN", "nullable": True},
        "start_time": {"type": "STRING", "nullable": True},
        "end_time": {"type": "STRING", "nullable": True},
        "task_id": {"type": "STRING", "nullable": True},
        "target_updates": {
            "type": "ARRAY",
            "nullable": True,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "target_id": {"type": "STRING"},
                    "date": {"type": "STRING"},
                },
                "required": ["target_id", "date"],
            },
        },
        "reason": {"type": "STRING", "nullable": True},
    },
    "required": ["action"],
}

_SYSTEM_PROMPT = (
    "You translate one student utterance into ONE structured action: "
    "'add_event' (add a Google Calendar event such as personal plans, trips, dinners, classes, or seminars), "
    "'tick_task' (mark an existing open Google Task completed), "
    "'update_target_dates' (set or change the due/exam date for ONE OR MORE targets in CONFIGURED TARGETS), "
    "or 'unclear' (if ambiguous or none applies). "
    "Do zero scheduling math. "
    "For 'update_target_dates': whenever the user sets or changes the due date or exam date of any exam or assignment "
    "listed in CONFIGURED TARGETS (even multiple in one sentence, e.g. 'my Ethics Essay and Computer Vision assignment "
    "are due Dec 17'), return action='update_target_dates' with one {target_id, date} item per matching target "
    "using ONLY exact target_id values from CONFIGURED TARGETS and YYYY-MM-DD dates; if none matches, return 'unclear'. "
    "For 'add_event': use YYYY-MM-DD dates; set all_day=true whenever no specific clock hours are given "
    "(e.g. 'I have plans on the 28th' -> all_day=true, summary='Plans', calendar='personal'); "
    "for timed events use 24-hour HH:MM start_time and end_time; use calendar='university' for classes/exams "
    "and 'personal' otherwise. "
    "For 'tick_task': choose ONLY an exact task_id from the provided OPEN TASKS list; if none matches, return 'unclear'."
)


def _format_open_tasks(open_tasks: list[dict[str, Any]]) -> str:
    if not open_tasks:
        return "(none)"
    lines = []
    for task in open_tasks:
        tid = task.get("id", "")
        due = (task.get("due") or "")[:10] or "no-due"
        title = (task.get("title") or "").strip()
        lines.append(f"- id={tid} | due={due} | title={title}")
    return "\n".join(lines)


def _format_targets(targets: list[dict[str, Any]] | None) -> str:
    if not targets:
        return "(none)"
    lines = []
    for target in targets:
        tid = target.get("id", "")
        ttype = target.get("type", "target")
        due = target.get("date") or "TBD"
        name = (target.get("name") or "").strip()
        lines.append(f"- id={tid} | type={ttype} | due={due} | name={name}")
    return "\n".join(lines)


def _validate_hhmm(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value.strip()) != 5:
        raise ValueError(f"Invalid {field_name} from Gemini: {value!r}; expected HH:MM.")
    cleaned = value.strip()
    time.fromisoformat(cleaned)
    return cleaned


def validate_action_payload(
    raw: dict[str, Any],
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
) -> ParsedAction:
    kind = raw.get("action")
    if kind == "unclear":
        reason = (raw.get("reason") or "Could not map input to a Calendar event, target date, or open Task.").strip()
        return UnclearAction(reason=reason)

    if kind == "add_event":
        summary = (raw.get("summary") or "").strip()
        if not summary:
            raise ValueError("Gemini add_event action is missing a summary.")
        raw_date = raw.get("date")
        if not isinstance(raw_date, str):
            raise ValueError("Gemini add_event action is missing a YYYY-MM-DD date.")
        start_d = date.fromisoformat(raw_date.strip())
        raw_end = raw.get("end_date")
        end_d = date.fromisoformat(raw_end.strip()) if isinstance(raw_end, str) and raw_end.strip() else start_d
        if end_d < start_d:
            raise ValueError(f"Gemini add_event end_date ({end_d}) is before date ({start_d}).")

        calendar = raw.get("calendar") or "personal"
        if calendar not in ("personal", "university"):
            calendar = "personal"

        all_day = True if raw.get("all_day") is None else bool(raw["all_day"])
        if all_day:
            return AddEventAction(summary=summary, start_date=start_d, end_date=end_d, all_day=True, calendar=calendar)

        start_t = _validate_hhmm(raw.get("start_time"), "start_time")
        end_t = _validate_hhmm(raw.get("end_time"), "end_time")
        if time.fromisoformat(end_t) <= time.fromisoformat(start_t):
            raise ValueError(f"Gemini add_event end_time ({end_t}) must be after start_time ({start_t}).")
        return AddEventAction(
            summary=summary,
            start_date=start_d,
            end_date=start_d,
            all_day=False,
            start_time=start_t,
            end_time=end_t,
            calendar=calendar,
        )

    if kind == "tick_task":
        task_id = (raw.get("task_id") or "").strip()
        match = next((t for t in open_tasks if t.get("id") == task_id), None)
        if not match:
            return UnclearAction(reason=f"No matching open Google Task found (model suggested {task_id!r}).")
        return TickTaskAction(
            task_id=task_id,
            task_title=(match.get("title") or "").strip(),
            task_due=(match.get("due") or "")[:10],
        )

    if kind == "update_target_dates":
        raw_updates = raw.get("target_updates")
        if not isinstance(raw_updates, list) or not raw_updates:
            return UnclearAction(reason="No target date updates provided.")
        by_id = {str(t.get("id")): t for t in (targets or []) if t.get("id")}
        deduped: dict[str, TargetDateUpdate] = {}
        for item in raw_updates:
            if not isinstance(item, dict):
                raise ValueError("Invalid target_updates item from Gemini.")
            tid = str(item.get("target_id") or "").strip()
            match = by_id.get(tid)
            if not match:
                return UnclearAction(reason=f"No matching configured target found (model suggested {tid!r}).")
            raw_date = item.get("date")
            if not isinstance(raw_date, str) or not raw_date.strip():
                raise ValueError(f"Gemini update_target_dates item for {tid!r} is missing a YYYY-MM-DD date.")
            due = date.fromisoformat(raw_date.strip())
            deduped[tid] = TargetDateUpdate(
                target_id=tid,
                target_name=(match.get("name") or tid).strip(),
                due_date=due,
            )
        return UpdateTargetDatesAction(updates=tuple(deduped.values()))

    raise ValueError(f"Unknown action from Gemini: {kind!r}")


def _context_block(
    today: date,
    calendars: dict[str, str],
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
) -> str:
    return (
        f"TODAY: {today.isoformat()} ({today.strftime('%A')})\n"
        f"CALENDARS: personal={calendars.get('personal', 'Kai Nath')}, university={calendars.get('university', 'IU')}\n"
        f"CONFIGURED TARGETS:\n{_format_targets(targets)}\n"
        f"OPEN TASKS:\n{_format_open_tasks(open_tasks)}"
    )


def _request_action(
    prompt: str,
    *,
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None,
    call_gemini: Callable[..., str],
    audio_bytes: bytes | None = None,
    audio_mime_type: str = "audio/wav",
) -> ParsedAction:
    kwargs: dict[str, Any] = {
        "system": _SYSTEM_PROMPT,
        "response_schema": ACTION_SCHEMA,
        "max_output_tokens": PARSE_MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
    }
    if audio_bytes is not None:
        kwargs["audio_bytes"] = audio_bytes
        kwargs["audio_mime_type"] = audio_mime_type
    raw_text = call_gemini(prompt, **kwargs)
    try:
        parsed = json.loads(raw_text)
    except ValueError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON for action parsing: {raw_text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini action response must be a JSON object.")
    return validate_action_payload(parsed, open_tasks, targets=targets)


def parse_user_action(
    utterance: str,
    *,
    today: date,
    calendars: dict[str, str],
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
    call_gemini: Callable[..., str] = generate_content,
) -> ParsedAction:
    cleaned = utterance.strip()
    if not cleaned:
        raise ValueError("Please provide a plain-language instruction.")

    prompt = f"{_context_block(today, calendars, open_tasks, targets)}\n\nUSER INPUT: {cleaned}"
    return _request_action(prompt, open_tasks=open_tasks, targets=targets, call_gemini=call_gemini)


def parse_audio_action(
    audio_bytes: bytes,
    *,
    today: date,
    calendars: dict[str, str],
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
    audio_mime_type: str = "audio/wav",
    call_gemini: Callable[..., str] = generate_content,
) -> ParsedAction:
    if not audio_bytes:
        raise ValueError("Audio clip is empty.")

    prompt = (
        f"{_context_block(today, calendars, open_tasks, targets)}\n\n"
        "USER INPUT: Listen to the attached voice clip and map it to one structured action."
    )
    return _request_action(
        prompt,
        open_tasks=open_tasks,
        targets=targets,
        call_gemini=call_gemini,
        audio_bytes=audio_bytes,
        audio_mime_type=audio_mime_type,
    )
