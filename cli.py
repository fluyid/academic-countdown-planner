"""CLI entry point for the Academic Countdown & Study Planner."""

from __future__ import annotations

import argparse
import sys

from src.calendar_client import format_event_line, get_upcoming_events


def main() -> int:
    parser = argparse.ArgumentParser(prog="countdown-planner")
    sub = parser.add_subparsers(dest="command", required=True)
    cal = sub.add_parser("check-calendar", help="Print the next N upcoming Calendar events.")
    cal.add_argument("-n", "--count", type=int, default=10)
    args = parser.parse_args()

    try:
        events = get_upcoming_events(max_results=args.count)
    except FileNotFoundError as exc:
        print(f"Setup required: {exc}", file=sys.stderr)
        return 1

    for idx, ev in enumerate(events, 1):
        print(f"{idx:2d}. {format_event_line(ev)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
