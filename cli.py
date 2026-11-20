"""CLI entry point for the Academic Countdown & Study Planner."""

from __future__ import annotations

import argparse
import sys

from src.calendar_client import format_event_line, get_events_in_window, get_upcoming_events
from src.display import write_display
from src.planner import build_plan, format_plan, load_config, plan_window
from src.tasks import (
    format_sync_summary,
    get_or_create_tasklist,
    get_tasks_service,
    list_tasks,
    read_completed_progress,
    sync_plan_to_tasks,
)


def _load_live_state(config: dict, list_name: str):
    start, end = plan_window(config)
    events = get_events_in_window(start, end)
    service = get_tasks_service()
    tasklist_id = get_or_create_tasklist(service, list_name)
    existing = list_tasks(service, tasklist_id)
    completed_units, completed_days = read_completed_progress(existing)
    plan = build_plan(
        config,
        events,
        start_date=start,
        completed_units=completed_units,
        completed_days=completed_days,
    )
    return service, tasklist_id, existing, completed_units, completed_days, plan


def main() -> int:
    parser = argparse.ArgumentParser(prog="countdown-planner")
    sub = parser.add_subparsers(dest="command", required=True)
    cal = sub.add_parser("check-calendar", help="Print the next N upcoming Calendar events.")
    cal.add_argument("-n", "--count", type=int, default=10)
    sub.add_parser("plan", help="Print the pure-Python study plan through the exam window.")
    sync = sub.add_parser("sync-tasks", help="Sync the computed study plan to Google Tasks (one task per study day).")
    sync.add_argument("--list", dest="list_name", help="Override Google Tasks list name from config/targets.json.")
    disp = sub.add_parser("build-display", help="Render the ephemeral HTML display page into output/.")
    disp.add_argument("--list", dest="list_name", help="Override Google Tasks list name from config/targets.json.")
    disp.add_argument("--background", help="Optional background image path or URL (defaults to placeholder backdrop).")
    args = parser.parse_args()

    try:
        if args.command == "check-calendar":
            events = get_upcoming_events(max_results=args.count)
            for idx, ev in enumerate(events, 1):
                print(f"{idx:2d}. {format_event_line(ev)}")
        elif args.command == "plan":
            config = load_config()
            start, end = plan_window(config)
            events = get_events_in_window(start, end)
            print(format_plan(build_plan(config, events, start_date=start)))
        elif args.command == "sync-tasks":
            config = load_config()
            list_name = args.list_name or config.get("tasks", {}).get("list_name", "Countdown Planner (Test)")
            service, tasklist_id, existing, _, _, plan = _load_live_state(config, list_name)
            result = sync_plan_to_tasks(service, tasklist_id, list_name, plan, existing_tasks=existing)
            print(format_sync_summary(result))
            print()
            print(format_plan(plan))
        elif args.command == "build-display":
            config = load_config()
            list_name = args.list_name or config.get("tasks", {}).get("list_name", "Countdown Planner (Test)")
            _, _, _, completed_units, completed_days, plan = _load_live_state(config, list_name)
            out = write_display(
                plan,
                config,
                completed_units=completed_units,
                completed_days=completed_days,
                background=args.background,
            )
            print(f"Display built: {out.html_path}")
            print(f"Live view:     {out.html_path.resolve().as_uri()}")
            print(f"Static view:   {out.html_path.resolve().as_uri()}?mode=static")
    except FileNotFoundError as exc:
        print(f"Setup required: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
