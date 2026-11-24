"""Gemini Job 1: translate plain language into a structured Calendar event or Task tick."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, time
from typing import Any, Callable

from src.gemini.client import generate_content

PARSE_MAX_OUTPUT_TOKENS = 320
AUDIO_PARSE_MODEL = "gemini-3.5-flash"


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
class LogProgressAction:
    target_id: str
    target_name: str
    units: float | None = None
    papers: float | None = None
    is_reading: bool = False


@dataclass(frozen=True)
class UnclearAction:
    reason: str


ParsedAction = AddEventAction | TickTaskAction | UpdateTargetDatesAction | LogProgressAction | UnclearAction

ACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING", "nullable": True},
        "action": {
            "type": "STRING",
            "enum": ["add_event", "tick_task", "update_target_dates", "log_progress", "unclear"],
        },
        "calendar": {"type": "STRING", "enum": ["personal", "university"], "nullable": True},
        "summary": {"type": "STRING", "nullable": True},
        "date": {"type": "STRING", "nullable": True},
        "end_date": {"type": "STRING", "nullable": True},
        "all_day": {"type": "BOOLEAN", "nullable": True},
        "start_time": {"type": "STRING", "nullable": True},
        "end_time": {"type": "STRING", "nullable": True},
        "task_id": {"type": "STRING", "nullable": True},
        "target_id": {"type": "STRING", "nullable": True},
        "units": {"type": "NUMBER", "nullable": True},
        "papers": {"type": "NUMBER", "nullable": True},
        "is_reading": {"type": "BOOLEAN", "nullable": True},
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
    "You translate one student utterance (text or audio) into ONE structured action: "
    "'add_event' (add a Google Calendar event such as personal plans, trips, dinners, classes, or seminars), "
    "'tick_task' (mark one exact existing open Google Task from OPEN TASKS completed), "
    "'log_progress' (log a specific completed amount of units or papers, including decimals like 0.25, 0.5, 0.75, "
    "for a target in CONFIGURED TARGETS), "
    "'update_target_dates' (set or change the due/exam date for ONE OR MORE targets in CONFIGURED TARGETS), "
    "or 'unclear' (if ambiguous or none applies). "
    "First write the exact verbatim spoken/written words into 'transcript', then choose fields matching ONLY "
    "what the user actually said. Do zero scheduling math. "
    "For 'log_progress': match target_id strictly to the subject the user named in CONFIGURED TARGETS (never guess "
    "a different subject). If the user says units (e.g. '0.75 units'), set units=0.75, papers=null, is_reading=false. "
    "If the user says papers (e.g. 'half a paper' -> papers=0.5, 'a paper'/'one paper' -> papers=1, "
    "'0.5 paper' -> papers=0.5, 'two papers' -> papers=2), set papers=<decimal>, units=null, is_reading=true "
    "(do NOT convert papers to units yourself). "
    "For 'update_target_dates': whenever the user sets or changes the due date or exam date of one or more targets "
    "listed in CONFIGURED TARGETS, return action='update_target_dates' with one {target_id, date} item per matching "
    "target using exact target_id values and YYYY-MM-DD dates. "
    "For 'add_event': use YYYY-MM-DD dates; set all_day=true when no clock hours are given; for timed events use "
    "24-hour HH:MM start_time and end_time; use calendar='university' for classes/exams and 'personal' otherwise. "
    "For 'tick_task': choose ONLY an exact task_id from OPEN TASKS; if the user states a custom or fractional unit "
    "or paper count, use 'log_progress' instead."
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
        rp = target.get("required_papers")
        extra = f" | required={rp.get('count')} papers" if isinstance(rp, dict) and rp.get("count") else ""
        lines.append(f"- id={tid} | type={ttype} | due={due}{extra} | name={name}")
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

    if kind == "log_progress":
        tid = str(raw.get("target_id") or "").strip()
        by_id = {str(t.get("id")): t for t in (targets or []) if t.get("id")}
        match = by_id.get(tid)
        if not match:
            return UnclearAction(reason=f"No matching configured target found (model suggested {tid!r}).")

        def _parse_pos(val: Any, label: str) -> float | None:
            if val is None or isinstance(val, bool):
                return None
            try:
                num = round(float(val), 4)
            except (TypeError, ValueError):
                return None
            if num == 0:
                return None
            if not math.isfinite(num) or num < 0 or num > 20:
                raise ValueError(f"Logged {label} must be in (0, 20] (got {num}).")
            return num

        try:
            papers = _parse_pos(raw.get("papers"), "papers")
            units = _parse_pos(raw.get("units"), "units")
        except ValueError as exc:
            return UnclearAction(reason=str(exc))

        if papers is None and units is None:
            return UnclearAction(reason="Missing unit or paper amount to log.")
        if papers is not None and not isinstance(match.get("required_papers"), dict):
            return UnclearAction(reason=f"Target {match.get('name')!r} does not track papers.")

        raw_reading = raw.get("is_reading")
        if papers is not None:
            is_reading = True
            units = None
        elif raw_reading is None:
            # Paper-only assignments (units == 0 with required_papers) log directly to their reading stream.
            is_reading = bool(match.get("required_papers")) and match.get("units") == 0
        else:
            is_reading = bool(raw_reading)
        return LogProgressAction(
            target_id=tid,
            target_name=(match.get("name") or tid).strip(),
            units=units,
            papers=papers,
            is_reading=is_reading,
        )

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
    model: str | None = None,
) -> tuple[ParsedAction, str | None]:
    kwargs: dict[str, Any] = {
        "system": _SYSTEM_PROMPT,
        "response_schema": ACTION_SCHEMA,
        "max_output_tokens": PARSE_MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
    }
    if audio_bytes is not None:
        kwargs["audio_bytes"] = audio_bytes
        kwargs["audio_mime_type"] = audio_mime_type
    if model is not None:
        kwargs["model"] = model
    raw_text = call_gemini(prompt, **kwargs)
    try:
        parsed = json.loads(raw_text)
    except ValueError as exc:
        raise RuntimeError(f"Gemini returned invalid JSON for action parsing: {raw_text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Gemini action response must be a JSON object.")
    raw_transcript = parsed.get("transcript")
    transcript = str(raw_transcript).strip() if isinstance(raw_transcript, str) and raw_transcript.strip() else None
    return validate_action_payload(parsed, open_tasks, targets=targets), transcript


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
    action, _ = _request_action(prompt, open_tasks=open_tasks, targets=targets, call_gemini=call_gemini)
    return action


def parse_audio_action_with_transcript(
    audio_bytes: bytes,
    *,
    today: date,
    calendars: dict[str, str],
    open_tasks: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
    audio_mime_type: str = "audio/wav",
    call_gemini: Callable[..., str] = generate_content,
) -> tuple[ParsedAction, str | None]:
    if not audio_bytes:
        raise ValueError("Audio clip is empty.")

    prompt = (
        f"{_context_block(today, calendars, open_tasks, targets)}\n\n"
        "USER INPUT: Transcribe the attached voice clip verbatim into 'transcript', then map it to one structured action."
    )
    return _request_action(
        prompt,
        open_tasks=open_tasks,
        targets=targets,
        call_gemini=call_gemini,
        audio_bytes=audio_bytes,
        audio_mime_type=audio_mime_type,
        model=AUDIO_PARSE_MODEL,
    )


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
    action, _ = parse_audio_action_with_transcript(
        audio_bytes,
        today=today,
        calendars=calendars,
        open_tasks=open_tasks,
        targets=targets,
        audio_mime_type=audio_mime_type,
        call_gemini=call_gemini,
    )
    return action
