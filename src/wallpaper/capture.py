"""Native screen resolution detection and headless browser screenshot capture."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SCREENSHOT_NAME = "lockscreen.png"
DEFAULT_RESOLUTION = (1920, 1080)
_RES_RE = re.compile(r"(\d+)\s*[xX\u00d7]\s*(\d+)")


def parse_size(raw: str) -> tuple[int, int]:
    match = _RES_RE.fullmatch(raw.strip())
    if not match:
        raise ValueError(f"Invalid size {raw!r}; expected WIDTHxHEIGHT (e.g. 1920x1080).")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError("Screen dimensions must be positive integers.")
    return width, height


def _parse_macos_displays_json(payload: str) -> tuple[int, int] | None:
    data = json.loads(payload)
    for gpu in data.get("SPDisplaysDataType", []):
        for display in gpu.get("spdisplays_ndrvs", []):
            for key in ("_spdisplays_pixels", "_spdisplays_resolution"):
                raw = display.get(key)
                if isinstance(raw, str):
                    match = _RES_RE.search(raw)
                    if match:
                        w, h = int(match.group(1)), int(match.group(2))
                        if w > 0 and h > 0:
                            return w, h
    return None


def detect_screen_resolution(*, platform: str | None = None) -> tuple[int, int]:
    plat = platform or sys.platform
    if plat == "win32":
        try:
            user32 = ctypes.windll.user32
            try:
                # Per-monitor DPI awareness v2 so HiDPI screens return physical pixels.
                user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except (AttributeError, OSError):
                user32.SetProcessDPIAware()
            w, h = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            if w > 0 and h > 0:
                return w, h
        except (AttributeError, OSError):
            return DEFAULT_RESOLUTION
    elif plat == "darwin":
        try:
            proc = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            found = _parse_macos_displays_json(proc.stdout)
            if found:
                return found
        except (OSError, subprocess.SubprocessError, ValueError):
            return DEFAULT_RESOLUTION
    return DEFAULT_RESOLUTION


def _browser_candidates(plat: str) -> tuple[list[str], list[Path]]:
    if plat == "win32":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        paths = [
            Path(pf) / "Google/Chrome/Application/chrome.exe",
            Path(pf86) / "Google/Chrome/Application/chrome.exe",
            Path(pf86) / "Microsoft/Edge/Application/msedge.exe",
            Path(pf) / "Microsoft/Edge/Application/msedge.exe",
            Path(pf) / "BraveSoftware/Brave-Browser/Application/brave.exe",
        ]
        if local:
            paths.insert(2, Path(local) / "Google/Chrome/Application/chrome.exe")
        return ["chrome", "msedge", "brave"], paths
    if plat == "darwin":
        return ["google-chrome", "chromium", "brave-browser"], [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    return ["google-chrome", "chromium", "chromium-browser", "microsoft-edge"], []


def find_browser(*, platform: str | None = None) -> Path:
    override = os.environ.get("COUNTDOWN_BROWSER")
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p
        raise FileNotFoundError(f"COUNTDOWN_BROWSER points to missing binary: {p}")

    plat = platform or sys.platform
    names, paths = _browser_candidates(plat)
    for name in names:
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    for path in paths:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "No Chromium-based browser found (Chrome, Edge, or Brave). "
        "Install one or set COUNTDOWN_BROWSER to the browser executable."
    )


def build_capture_command(
    browser: Path,
    html_path: Path,
    output_path: Path,
    size: tuple[int, int],
    user_data_dir: Path,
) -> list[str]:
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("Screen dimensions must be positive integers.")
    static_url = f"{html_path.resolve().as_uri()}?mode=static"
    return [
        str(browser),
        "--headless",
        "--disable-gpu",
        "--no-first-run",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=2000",
        f"--window-size={width},{height}",
        f"--user-data-dir={user_data_dir}",
        f"--screenshot={output_path.resolve()}",
        static_url,
    ]


def capture_static_screenshot(
    html_path: Path,
    output_path: Path | None = None,
    *,
    size: tuple[int, int] | None = None,
    browser: Path | None = None,
    platform: str | None = None,
) -> Path:
    src = Path(html_path)
    if not src.is_file():
        raise FileNotFoundError(f"Display HTML not found: {src}. Run `cli.py build-display` first.")

    dest = Path(output_path) if output_path else src.parent / DEFAULT_SCREENSHOT_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    dims = size or detect_screen_resolution(platform=platform)
    exe = browser or find_browser(platform=platform)

    with tempfile.TemporaryDirectory(prefix="countdown-shot-") as profile_dir:
        cmd = build_capture_command(exe, src, dest, dims, Path(profile_dir))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)

    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Headless screenshot failed (exit {proc.returncode}): {err[:300]}")
    return dest
