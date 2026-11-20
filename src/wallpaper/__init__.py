"""Lock-screen screenshot capture, OS wallpaper application, and refresh scheduling."""

from src.wallpaper.apply import LiveWallpaperResult, apply_lock_screen, build_windows_lockscreen_script, setup_live_wallpaper
from src.wallpaper.capture import (
    DEFAULT_RESOLUTION,
    DEFAULT_SCREENSHOT_NAME,
    build_capture_command,
    capture_static_screenshot,
    detect_screen_resolution,
    find_browser,
    parse_size,
)
from src.wallpaper.refresh import HASH_FILENAME, RefreshResult, compute_display_fingerprint, refresh_screen
from src.wallpaper.schedule import (
    DEFAULT_MORNING_TIME,
    DEFAULT_POLL_MINUTES,
    MACOS_LAUNCHD_LABEL,
    WINDOWS_TASK_NAME,
    install_schedule,
    macos_plist_path,
    parse_time_hhmm,
    render_macos_launchd_plist,
    render_schedule_definition,
    render_windows_task_xml,
    uninstall_schedule,
)

__all__ = [
    "DEFAULT_MORNING_TIME",
    "DEFAULT_POLL_MINUTES",
    "DEFAULT_RESOLUTION",
    "DEFAULT_SCREENSHOT_NAME",
    "HASH_FILENAME",
    "LiveWallpaperResult",
    "MACOS_LAUNCHD_LABEL",
    "RefreshResult",
    "WINDOWS_TASK_NAME",
    "apply_lock_screen",
    "build_capture_command",
    "build_windows_lockscreen_script",
    "capture_static_screenshot",
    "compute_display_fingerprint",
    "detect_screen_resolution",
    "find_browser",
    "install_schedule",
    "macos_plist_path",
    "parse_size",
    "parse_time_hhmm",
    "refresh_screen",
    "render_macos_launchd_plist",
    "render_schedule_definition",
    "render_windows_task_xml",
    "setup_live_wallpaper",
    "uninstall_schedule",
]
