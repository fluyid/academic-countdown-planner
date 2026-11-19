"""CLI entry point for the Academic Countdown & Study Planner."""

from __future__ import annotations

import argparse
import sys

from src.calendar_client import format_event_line, get_events_in_window, get_upcoming_events
from src.planner import build_plan, format_plan, load_config, plan_window


def main() -> int:
    parser = argparse.ArgumentParser(prog="countdown-planner")
    sub = parser.add_subparsers(dest="command", required=True)
    cal = sub.add_parser("check-calendar", help="Print the next N upcoming Calendar events.")
    cal.add_argument("-n", "--count", type=int, default=10)
    sub.add_parser("plan", help="Print the pure-Python study plan through the exam window.")
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
    except FileNotFoundError as exc:
        print(f"Setup required: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
