"""CLI entry point for the Academic Countdown & Study Planner."""

from __future__ import annotations

import argparse
import sys

from src.calendar_client import format_event_line, get_events_in_window, get_upcoming_events
from src.display import DEFAULT_OUTPUT_DIR, write_display
from src.planner import build_plan, format_plan, load_config, plan_window
from src.tasks import (
    format_sync_summary,
    get_or_create_tasklist,
    get_tasks_service,
    list_tasks,
    read_completed_progress,
    sync_plan_to_tasks,
)
from src.wallpaper import (
    DEFAULT_MORNING_TIME,
    DEFAULT_POLL_MINUTES,
    install_schedule,
    parse_size,
    refresh_screen,
    render_schedule_definition,
    setup_live_wallpaper,
    uninstall_schedule,
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
    sync.add_argument("--refresh", action="store_true", help="Also rebuild display, capture lock screen, and apply it.")
    disp = sub.add_parser("build-display", help="Render the ephemeral HTML display page into output/.")
    disp.add_argument("--list", dest="list_name", help="Override Google Tasks list name from config/targets.json.")
    disp.add_argument("--background", help="Optional background image path or URL (defaults to placeholder backdrop).")
    ref = sub.add_parser("refresh-screen", help="Rebuild display, capture static screenshot, and apply OS lock screen.")
    ref.add_argument("--list", dest="list_name", help="Override Google Tasks list name from config/targets.json.")
    ref.add_argument("--background", help="Optional background image path or URL.")
    ref.add_argument("--size", help="Override screenshot resolution as WIDTHxHEIGHT (defaults to native screen).")
    ref.add_argument("--if-changed", action="store_true", help="Skip screenshot/apply if the display state is unchanged.")
    ref.add_argument("--skip-apply", action="store_true", help="Capture output/lockscreen.png without changing OS lock screen.")
    sub.add_parser("setup-wallpaper", help="Point Lively Wallpaper (Windows) or Plash (macOS) to output/index.html.")
    sched = sub.add_parser("schedule", help="Install, uninstall, or inspect morning + on-change refresh scheduling.")
    sched.add_argument("action", choices=("install", "uninstall", "show"))
    sched.add_argument("--time", default=DEFAULT_MORNING_TIME, help="Morning refresh time in 24h HH:MM (default: 07:00).")
    sched.add_argument(
        "--poll-minutes",
        type=int,
        default=DEFAULT_POLL_MINUTES,
        help="On-change check interval in minutes (0 to disable, default: 30).",
    )
    say = sub.add_parser("say", help="Replan from plain language via Gemini (add Calendar event or tick a Task).")
    say.add_argument("text", nargs="+", help='Plain-language input, e.g. "I have plans on the 28th".')
    say.add_argument("--list", dest="list_name", help="Override Google Tasks list name from config/targets.json.")
    say.add_argument("--background", help="Optional background image path or URL.")
    say.add_argument("--skip-apply", action="store_true", help="Capture screenshot without changing OS lock screen.")
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
            service, tasklist_id, existing, completed_units, completed_days, plan = _load_live_state(config, list_name)
            result = sync_plan_to_tasks(service, tasklist_id, list_name, plan, existing_tasks=existing)
            print(format_sync_summary(result))
            print()
            print(format_plan(plan))
            if args.refresh:
                ref_res = refresh_screen(
                    plan, config, completed_units=completed_units, completed_days=completed_days
                )
                print(f"\nLock screen updated: {ref_res.screenshot_path} ({ref_res.resolution[0]}x{ref_res.resolution[1]})")
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
        elif args.command == "refresh-screen":
            config = load_config()
            list_name = args.list_name or config.get("tasks", {}).get("list_name", "Countdown Planner (Test)")
            _, _, _, completed_units, completed_days, plan = _load_live_state(config, list_name)
            size = parse_size(args.size) if args.size else None
            res = refresh_screen(
                plan,
                config,
                completed_units=completed_units,
                completed_days=completed_days,
                background=args.background,
                size=size,
                if_changed=args.if_changed,
                apply=not args.skip_apply,
            )
            w, h = res.resolution
            if not res.changed:
                print(f"No display changes; kept {res.screenshot_path} ({w}x{h}).")
            else:
                print(f"Screenshot:  {res.screenshot_path} ({w}x{h})")
                print(f"Lock screen: {'applied -> ' + str(res.applied_path) if res.applied else 'skipped (--skip-apply)'}")
        elif args.command == "setup-wallpaper":
            res = setup_live_wallpaper(DEFAULT_OUTPUT_DIR / "index.html")
            print(f"{res.app}: {res.detail}")
            print(f"Live URL: {res.url}")
        elif args.command == "schedule":
            if args.action == "show":
                print(render_schedule_definition(morning_time=args.time, poll_minutes=args.poll_minutes))
            elif args.action == "install":
                print(install_schedule(morning_time=args.time, poll_minutes=args.poll_minutes))
            elif args.action == "uninstall":
                print(uninstall_schedule())
        elif args.command == "say":
            from src.gemini import replan_from_text

            res = replan_from_text(
                " ".join(args.text),
                list_name=args.list_name,
                background=args.background,
                apply_lock_screen=not args.skip_apply,
            )
            print(res.action_summary)
            if res.ticked_task:
                due = f", due {res.ticked_task['due']}" if res.ticked_task.get("due") else ""
                print(f"Confirmed task ticked: {res.ticked_task['title']} (id={res.ticked_task['id']}{due})")
            if res.sync_result is not None:
                print()
                print(res.explanation)
                print()
                print(format_sync_summary(res.sync_result))
                if res.refresh_result is not None:
                    w, h = res.refresh_result.resolution
                    state = (
                        f"applied -> {res.refresh_result.applied_path}"
                        if res.refresh_result.applied
                        else "skipped (--skip-apply)"
                    )
                    print(f"Lock screen: {state} ({w}x{h})")
    except FileNotFoundError as exc:
        print(f"Setup required: {exc}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, NotImplementedError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
