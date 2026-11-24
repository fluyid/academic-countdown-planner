"""End-to-end display rebuild -> static screenshot -> OS lock-screen refresh."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.display import DEFAULT_OUTPUT_DIR, write_display
from src.planner.models import StudyPlan
from src.wallpaper.apply import apply_lock_screen, reload_live_wallpaper
from src.wallpaper.capture import DEFAULT_SCREENSHOT_NAME, capture_static_screenshot, detect_screen_resolution

HASH_FILENAME = ".display_hash"


@dataclass(frozen=True)
class RefreshResult:
    html_path: Path
    screenshot_path: Path
    applied_path: Path | None
    resolution: tuple[int, int]
    changed: bool
    applied: bool


def compute_display_fingerprint(data: dict[str, Any], resolution: tuple[int, int]) -> str:
    canonical = json.dumps({"data": data, "resolution": list(resolution)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def refresh_screen(
    plan: StudyPlan,
    config: dict[str, Any],
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    today: date | None = None,
    completed_units: dict[str, float] | None = None,
    completed_days: set[date] | None = None,
    background: str | Path | None = None,
    size: tuple[int, int] | None = None,
    if_changed: bool = False,
    apply: bool = True,
    browser: Path | None = None,
    platform: str | None = None,
) -> RefreshResult:
    display_out = write_display(
        plan,
        config,
        output_dir=output_dir,
        today=today,
        completed_units=completed_units,
        completed_days=completed_days,
        background=background,
    )
    resolution = size or detect_screen_resolution(platform=platform)
    screenshot_path = output_dir / DEFAULT_SCREENSHOT_NAME
    hash_path = output_dir / HASH_FILENAME
    fingerprint = compute_display_fingerprint(display_out.data, resolution)

    if if_changed and screenshot_path.is_file() and hash_path.is_file():
        if hash_path.read_text(encoding="utf-8").strip() == fingerprint:
            return RefreshResult(
                html_path=display_out.html_path,
                screenshot_path=screenshot_path,
                applied_path=None,
                resolution=resolution,
                changed=False,
                applied=False,
            )

    capture_static_screenshot(
        display_out.html_path,
        screenshot_path,
        size=resolution,
        browser=browser,
        platform=platform,
    )
    hash_path.write_text(fingerprint + "\n", encoding="utf-8")

    applied_path = apply_lock_screen(screenshot_path, platform=platform) if apply else None
    if apply:
        reload_live_wallpaper(display_out.html_path, platform=platform)
    return RefreshResult(
        html_path=display_out.html_path,
        screenshot_path=screenshot_path,
        applied_path=applied_path,
        resolution=resolution,
        changed=True,
        applied=apply,
    )
