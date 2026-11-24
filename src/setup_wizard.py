"""Interactive onboarding wizard and environment health check (`cli.py setup`)."""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.calendar_client import CONFIG_PATH
from src.gemini.client import _assert_outside_repo, _parse_dotenv_key
from src.google_auth import secrets_dir
from src.wallpaper.capture import find_browser


@dataclass(frozen=True)
class CheckItem:
    name: str
    ok: bool
    detail: str
    optional: bool = False


def _has_gemini_key(base_dir: Path) -> bool:
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return True
    env_file = base_dir / ".env"
    return bool(_parse_dotenv_key(env_file)) if env_file.is_file() else False


def install_credentials_file(src_path: str | Path, *, base_secrets_dir: Path | None = None) -> Path:
    source = Path(str(src_path).strip().strip('"').strip("'")).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"Credentials file not found: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"File is not valid JSON: {source}") from exc
    if not isinstance(data, dict) or not ("installed" in data or "web" in data):
        raise ValueError("Expected a Google OAuth Desktop App JSON (missing 'installed' key).")

    dest_dir = _assert_outside_repo(base_secrets_dir or secrets_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "credentials.json"
    if source.resolve() != dest.resolve():
        shutil.copy2(source, dest)
    if os.name != "nt":
        os.chmod(dest, 0o600)
    return dest


def save_gemini_api_key(api_key: str, *, base_secrets_dir: Path | None = None) -> Path:
    cleaned = api_key.strip()
    if not cleaned or "\n" in cleaned or "\r" in cleaned:
        raise ValueError("GEMINI_API_KEY must be a single non-empty line.")

    dest_dir = _assert_outside_repo(base_secrets_dir or secrets_dir())
    dest_dir.mkdir(parents=True, exist_ok=True)
    env_path = dest_dir / ".env"

    lines: list[str] = []
    replaced = False
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            key_part = stripped[7:].strip() if stripped.startswith("export ") else stripped
            if "=" in key_part and key_part.split("=", 1)[0].strip() == "GEMINI_API_KEY":
                if not replaced:
                    lines.append(f"GEMINI_API_KEY={cleaned}")
                    replaced = True
            else:
                lines.append(raw)
    if not replaced:
        lines.append(f"GEMINI_API_KEY={cleaned}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(env_path, 0o600)
    return env_path


def update_user_config(
    config_path: Path = CONFIG_PATH,
    *,
    personal_calendar: str | None = None,
    university_calendar: str | None = None,
    tasks_list_name: str | None = None,
) -> dict[str, Any]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    calendars = data.setdefault("calendars", {})
    if personal_calendar is not None and personal_calendar.strip():
        calendars["personal"] = personal_calendar.strip()
    if university_calendar is not None and university_calendar.strip():
        calendars["university"] = university_calendar.strip()
    if tasks_list_name is not None and tasks_list_name.strip():
        data.setdefault("tasks", {})["list_name"] = tasks_list_name.strip()
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def collect_setup_checks(
    *,
    config_path: Path = CONFIG_PATH,
    base_secrets_dir: Path | None = None,
    browser_finder: Callable[[], Path] = find_browser,
) -> list[CheckItem]:
    checks: list[CheckItem] = []

    py_ver = sys.version_info
    checks.append(
        CheckItem(
            "Python",
            py_ver >= (3, 11),
            f"{py_ver.major}.{py_ver.minor}.{py_ver.micro} (requires 3.11+)",
        )
    )

    try:
        sec_dir = _assert_outside_repo(base_secrets_dir or secrets_dir())
        checks.append(CheckItem("Secrets folder", sec_dir.is_dir(), f"{sec_dir} (outside repo)"))
    except ValueError as exc:
        sec_dir = Path(base_secrets_dir or secrets_dir())
        checks.append(CheckItem("Secrets folder", False, str(exc)))

    creds_path = sec_dir / "credentials.json"
    creds_ok = creds_path.is_file()
    checks.append(
        CheckItem(
            "Google OAuth credentials",
            creds_ok,
            str(creds_path) if creds_ok else f"Missing {creds_path}",
        )
    )

    token_path = sec_dir / "token.json"
    checks.append(
        CheckItem(
            "Google OAuth login",
            token_path.is_file(),
            str(token_path) if token_path.is_file() else "Not signed in yet (run `cli.py check-calendar`)",
            optional=True,
        )
    )

    has_key = _has_gemini_key(sec_dir)
    checks.append(
        CheckItem(
            "Gemini API key",
            has_key,
            "Configured (outside repo)" if has_key else f"Not set (optional — add to {sec_dir / '.env'} for say/voice/listen)",
            optional=True,
        )
    )

    try:
        browser = browser_finder()
        checks.append(CheckItem("Chromium browser", True, str(browser)))
    except FileNotFoundError:
        checks.append(CheckItem("Chromium browser", False, "Not found (install Chrome, Edge, or Brave; or set COUNTDOWN_BROWSER)"))

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cals = cfg.get("calendars", {})
        tlist = cfg.get("tasks", {}).get("list_name", "")
        checks.append(
            CheckItem(
                "Config",
                True,
                f"personal={cals.get('personal')!r}, university={cals.get('university')!r}, tasks={tlist!r}",
            )
        )
    except (OSError, ValueError) as exc:
        checks.append(CheckItem("Config", False, f"{config_path}: {exc}"))

    missing_voice = [pkg for pkg in ("sounddevice", "edge_tts", "vosk") if importlib.util.find_spec(pkg) is None]
    checks.append(
        CheckItem(
            "Voice packages",
            not missing_voice,
            "sounddevice, edge_tts, vosk installed"
            if not missing_voice
            else f"Missing: {', '.join(missing_voice)} (re-run pip install -r requirements.txt)",
            optional=True,
        )
    )

    return checks


def format_setup_checks(checks: list[CheckItem]) -> str:
    lines = ["Setup check:"]
    for item in checks:
        tag = "OK" if item.ok else ("OPT" if item.optional else "MISSING")
        lines.append(f"  [{tag:7s}] {item.name}: {item.detail}")
    return "\n".join(lines)


def run_setup_wizard(
    *,
    check_only: bool = False,
    config_path: Path = CONFIG_PATH,
    base_secrets_dir: Path | None = None,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
    print_fn: Callable[[str], None] = print,
    browser_finder: Callable[[], Path] = find_browser,
) -> int:
    if check_only:
        checks = collect_setup_checks(
            config_path=config_path,
            base_secrets_dir=base_secrets_dir,
            browser_finder=browser_finder,
        )
        print_fn(format_setup_checks(checks))
        return 0 if all(c.ok or c.optional for c in checks) else 1

    sec_dir = _assert_outside_repo(base_secrets_dir or secrets_dir())
    sec_dir.mkdir(parents=True, exist_ok=True)
    print_fn("=== Academic Countdown & Study Planner Setup ===")
    print_fn(f"Secrets folder (strictly outside repo): {sec_dir}\n")

    # 1. Google Calendar + Tasks OAuth credentials ($0)
    creds_path = sec_dir / "credentials.json"
    if creds_path.is_file():
        print_fn(f"[1/3] Google OAuth credentials already present: {creds_path}")
    else:
        print_fn("[1/3] Google Calendar & Tasks OAuth ($0 — no billing needed):")
        print_fn("      In Google Cloud Console, enable Calendar API + Tasks API,")
        print_fn("      create an OAuth 2.0 Client ID (Desktop App), and download the JSON.")
        raw_path = input_fn("      Path to downloaded credentials JSON (Enter to skip): ").strip()
        if raw_path:
            dest = install_credentials_file(raw_path, base_secrets_dir=sec_dir)
            print_fn(f"      Saved -> {dest}")
        else:
            print_fn(f"      Skipped. Drop it later at: {creds_path}")

    # 2. Optional Gemini API key (only for say / voice / listen)
    if _has_gemini_key(sec_dir):
        print_fn("\n[2/3] Gemini API key already configured.")
    else:
        print_fn("\n[2/3] Gemini API key (optional — only for `say`, `voice`, and `listen`):")
        print_fn("      Create a key in Google AI Studio (hard-capped locally at 25 calls/day).")
        raw_key = secret_fn("      Paste GEMINI_API_KEY (hidden, Enter to skip): ").strip()
        if raw_key:
            env_file = save_gemini_api_key(raw_key, base_secrets_dir=sec_dir)
            print_fn(f"      Saved -> {env_file}")
        else:
            print_fn(f"      Skipped. Add GEMINI_API_KEY=... later to: {sec_dir / '.env'}")

    # 3. Calendar names & Google Tasks list in config/targets.json
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cur_personal = cfg.get("calendars", {}).get("personal", "primary")
    cur_uni = cfg.get("calendars", {}).get("university", "University")
    cur_list = cfg.get("tasks", {}).get("list_name", "Countdown Planner")
    print_fn(f"\n[3/3] Calendar & Tasks names in {config_path.name} (Enter keeps current):")
    personal = input_fn(f"      Personal Google Calendar name [{cur_personal}]: ").strip()
    university = input_fn(f"      University Google Calendar name [{cur_uni}]: ").strip()
    tlist = input_fn(f"      Google Tasks list name [{cur_list}]: ").strip()
    if personal or university or tlist:
        update_user_config(
            config_path,
            personal_calendar=personal or None,
            university_calendar=university or None,
            tasks_list_name=tlist or None,
        )
        print_fn(f"      Updated {config_path}")

    print_fn("")
    checks = collect_setup_checks(
        config_path=config_path,
        base_secrets_dir=sec_dir,
        browser_finder=browser_finder,
    )
    print_fn(format_setup_checks(checks))

    py_cmd = r".venv\Scripts\python" if sys.platform == "win32" else ".venv/bin/python"
    print_fn("\nNext steps:")
    print_fn(f"  1. Edit your exam/assignment dates in {config_path}")
    print_fn(f"  2. Sign in & check calendars:   {py_cmd} cli.py check-calendar")
    print_fn(f"  3. Sync Tasks & lock screen:    {py_cmd} cli.py sync-tasks --refresh")
    print_fn(f"  4. Live wallpaper + schedule:   {py_cmd} cli.py setup-wallpaper && {py_cmd} cli.py schedule install")
    print_fn(f"  5. Hands-free 'Hey Gemini':     {py_cmd} cli.py listen --speak")
    return 0 if all(c.ok or c.optional for c in checks) else 1
