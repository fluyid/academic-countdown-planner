"""Morning and on-change refresh scheduling (Windows Task Scheduler + macOS launchd)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

WINDOWS_TASK_NAME = "CountdownPlannerRefresh"
MACOS_LAUNCHD_LABEL = "com.countdown-planner.refresh"
DEFAULT_MORNING_TIME = "07:00"
DEFAULT_POLL_MINUTES = 30
DEFAULT_REPO_DIR = Path(__file__).resolve().parent.parent.parent


def parse_time_hhmm(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Invalid time {value!r}; expected HH:MM (24-hour, e.g. 07:00).")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Time out of range: {value!r}; expected 00:00..23:59.")
    return hour, minute


def _windows_python_binary(python_exe: Path) -> Path:
    # Prefer pythonw.exe so scheduled refreshes never flash a terminal window.
    if python_exe.name.lower() == "python.exe":
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return python_exe


def render_windows_task_xml(
    python_exe: Path,
    repo_dir: Path,
    *,
    morning_time: str = DEFAULT_MORNING_TIME,
    poll_minutes: int = DEFAULT_POLL_MINUTES,
) -> str:
    hour, minute = parse_time_hhmm(morning_time)
    if poll_minutes < 0:
        raise ValueError("poll_minutes must be >= 0.")

    exe = _windows_python_binary(Path(python_exe).resolve())
    workdir = Path(repo_dir).resolve()
    cli_path = workdir / "cli.py"
    args = f'"{cli_path}" refresh-screen --if-changed'

    repetition = ""
    if poll_minutes > 0:
        repetition = (
            "      <Repetition>\n"
            f"        <Interval>PT{poll_minutes}M</Interval>\n"
            "        <Duration>P1D</Duration>\n"
            "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
            "      </Repetition>\n"
        )

    # XSD 1.2 requires <Repetition> before <StartBoundary> inside <CalendarTrigger>.
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        "  <RegistrationInfo>\n"
        "    <Description>Refresh Academic Countdown display and lock screen (morning + on-change).</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <CalendarTrigger>\n"
        f"{repetition}"
        f"      <StartBoundary>2026-01-01T{hour:02d}:{minute:02d}:00</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "      <ScheduleByDay>\n"
        "        <DaysInterval>1</DaysInterval>\n"
        "      </ScheduleByDay>\n"
        "    </CalendarTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{xml_escape(str(exe))}</Command>\n"
        f"      <Arguments>{xml_escape(args)}</Arguments>\n"
        f"      <WorkingDirectory>{xml_escape(str(workdir))}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def render_macos_launchd_plist(
    python_exe: Path,
    repo_dir: Path,
    *,
    morning_time: str = DEFAULT_MORNING_TIME,
    poll_minutes: int = DEFAULT_POLL_MINUTES,
    config_path: Path | None = None,
    log_path: Path | None = None,
) -> bytes:
    hour, minute = parse_time_hhmm(morning_time)
    if poll_minutes < 0:
        raise ValueError("poll_minutes must be >= 0.")

    workdir = Path(repo_dir).resolve()
    cfg = Path(config_path).resolve() if config_path else workdir / "config" / "targets.json"
    log = Path(log_path).resolve() if log_path else workdir / "output" / "refresh.log"

    plist: dict[str, object] = {
        "Label": MACOS_LAUNCHD_LABEL,
        "ProgramArguments": [
            str(Path(python_exe).resolve()),
            str(workdir / "cli.py"),
            "refresh-screen",
            "--if-changed",
        ],
        "WorkingDirectory": str(workdir),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "WatchPaths": [str(cfg)],
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    if poll_minutes > 0:
        plist["StartInterval"] = poll_minutes * 60
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML)


def macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LAUNCHD_LABEL}.plist"


def render_schedule_definition(
    *,
    python_exe: Path | None = None,
    repo_dir: Path = DEFAULT_REPO_DIR,
    morning_time: str = DEFAULT_MORNING_TIME,
    poll_minutes: int = DEFAULT_POLL_MINUTES,
    platform: str | None = None,
) -> str:
    py = python_exe or Path(sys.executable)
    plat = platform or sys.platform
    if plat == "win32":
        return render_windows_task_xml(py, repo_dir, morning_time=morning_time, poll_minutes=poll_minutes)
    if plat == "darwin":
        return render_macos_launchd_plist(py, repo_dir, morning_time=morning_time, poll_minutes=poll_minutes).decode(
            "utf-8"
        )
    raise NotImplementedError(f"Scheduling is supported on Windows and macOS only (got {plat}).")


def install_schedule(
    *,
    python_exe: Path | None = None,
    repo_dir: Path = DEFAULT_REPO_DIR,
    morning_time: str = DEFAULT_MORNING_TIME,
    poll_minutes: int = DEFAULT_POLL_MINUTES,
    platform: str | None = None,
) -> str:
    py = python_exe or Path(sys.executable)
    plat = platform or sys.platform

    if plat == "win32":
        xml_text = render_windows_task_xml(py, repo_dir, morning_time=morning_time, poll_minutes=poll_minutes)
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False) as tmp:
            tmp.write(xml_text.encode("utf-16"))
            xml_file = Path(tmp.name)
        try:
            proc = subprocess.run(
                ["schtasks.exe", "/Create", "/F", "/TN", WINDOWS_TASK_NAME, "/XML", str(xml_file)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        finally:
            xml_file.unlink(missing_ok=True)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Failed to register Windows Scheduled Task: {err[:300]}")
        return f"Installed Windows Task Scheduler job '{WINDOWS_TASK_NAME}' (daily {morning_time}, poll {poll_minutes}m)."

    if plat == "darwin":
        plist_bytes = render_macos_launchd_plist(py, repo_dir, morning_time=morning_time, poll_minutes=poll_minutes)
        target = macos_plist_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        (Path(repo_dir) / "output").mkdir(parents=True, exist_ok=True)
        target.write_bytes(plist_bytes)
        domain = f"gui/{getattr(os, 'getuid', lambda: 0)()}"
        subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True, text=True, timeout=15)
        proc = subprocess.run(
            ["launchctl", "bootstrap", domain, str(target)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Failed to load macOS launchd agent: {err[:300]}")
        return f"Installed macOS launchd agent '{MACOS_LAUNCHD_LABEL}' at {target}."

    raise NotImplementedError(f"Scheduling is supported on Windows and macOS only (got {plat}).")


def uninstall_schedule(*, platform: str | None = None) -> str:
    plat = platform or sys.platform

    if plat == "win32":
        proc = subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", WINDOWS_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Failed to delete Windows Scheduled Task: {err[:300]}")
        return f"Removed Windows Task Scheduler job '{WINDOWS_TASK_NAME}'."

    if plat == "darwin":
        target = macos_plist_path()
        domain = f"gui/{getattr(os, 'getuid', lambda: 0)()}"
        subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True, text=True, timeout=15)
        target.unlink(missing_ok=True)
        return f"Removed macOS launchd agent '{MACOS_LAUNCHD_LABEL}'."

    raise NotImplementedError(f"Scheduling is supported on Windows and macOS only (got {plat}).")
