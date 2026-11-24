"""OS lock-screen application (Windows + macOS) and live desktop wallpaper setup."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class LiveWallpaperResult:
    applied: bool
    app: str
    url: str
    detail: str


def build_windows_lockscreen_script() -> str:
    # Reads path from LOCKSCREEN_IMAGE_PATH env var so arbitrary file paths are never interpolated.
    return (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
        "$methods = [System.WindowsRuntimeSystemExtensions].GetMethods(); "
        "$asTask = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 "
        "-and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
        "$asAction = ($methods | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 "
        "-and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]; "
        "[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null; "
        "[Windows.System.UserProfile.LockScreen,Windows.System.UserProfile,ContentType=WindowsRuntime] | Out-Null; "
        "$op = [Windows.Storage.StorageFile]::GetFileFromPathAsync($env:LOCKSCREEN_IMAGE_PATH); "
        "$file = $asTask.MakeGenericMethod([Windows.Storage.StorageFile]).Invoke($null, @($op)).GetAwaiter().GetResult(); "
        "$act = [Windows.System.UserProfile.LockScreen]::SetImageFileAsync($file); "
        "$asAction.Invoke($null, @($act)).GetAwaiter().GetResult()"
    )


def _next_macos_cache_path(image_path: Path) -> Path:
    # macOS ignores wallpaper updates when the file path is unchanged; alternate between two slots.
    slot_a = image_path.with_name(f"{image_path.stem}_a{image_path.suffix}")
    slot_b = image_path.with_name(f"{image_path.stem}_b{image_path.suffix}")
    marker = image_path.parent / ".macos_slot"
    prev = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    target, next_tag = (slot_b, "b") if prev == "a" else (slot_a, "a")
    shutil.copy2(image_path, target)
    marker.write_text(next_tag, encoding="utf-8")
    return target


def apply_lock_screen(image_path: Path, *, platform: str | None = None) -> Path:
    resolved = Path(image_path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Lock-screen image not found: {resolved}")

    plat = platform or sys.platform
    if plat == "win32":
        env = {**os.environ, "LOCKSCREEN_IMAGE_PATH": str(resolved)}
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", build_windows_lockscreen_script()],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Windows lock-screen update failed: {err[:300]}")
        return resolved

    if plat == "darwin":
        target = _next_macos_cache_path(resolved)
        script = (
            'on run argv\n'
            '  tell application "System Events" to tell every desktop to '
            'set picture to POSIX file (item 1 of argv)\n'
            'end run'
        )
        proc = subprocess.run(
            ["osascript", "-e", script, "--", str(target)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"macOS lock-screen/wallpaper update failed: {err[:300]}")
        return target

    raise NotImplementedError(f"Lock-screen application is supported on Windows and macOS only (got {plat}).")


_HUD_HTML_MARKER = 'aria-label="Academic Study Countdown"'


def _find_lively_cli() -> Path | None:
    for name in ("livelycu", "livelycu.exe", "Lively.exe"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local) / "Microsoft/WindowsApps/livelycu.exe" if local else None,
        Path(local) / "Programs/Lively Wallpaper/livelycu.exe" if local else None,
        Path(local) / "Programs/Lively Wallpaper/Lively.exe" if local else None,
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Lively Wallpaper/Lively.exe",
    ]
    return next((p for p in candidates if p and p.is_file()), None)


def _sync_lively_library_copies(html_path: Path, *, local_appdata: Path | None = None) -> int:
    """Update any copied Countdown HTML files inside Lively Wallpaper's local Library folders."""
    base = local_appdata or (Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None)
    if not base or not base.is_dir() or not html_path.is_file():
        return 0

    roots = [base / "Lively Wallpaper" / "Library" / "wallpapers"]
    packages_dir = base / "Packages"
    if packages_dir.is_dir():
        roots.extend(
            pkg / "LocalCache" / "Local" / "Lively Wallpaper" / "Library" / "wallpapers"
            for pkg in packages_dir.glob("*LivelyWallpaper*")
        )

    plan_json = html_path.with_name("plan.json")
    updated = 0
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            target_html = entry / "index.html"
            if not target_html.is_file():
                continue
            try:
                head = target_html.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _HUD_HTML_MARKER not in head:
                continue
            shutil.copy2(html_path, target_html)
            if plan_json.is_file():
                shutil.copy2(plan_json, entry / "plan.json")
            updated += 1
    return updated


def reload_live_wallpaper(
    html_path: Path,
    *,
    platform: str | None = None,
    local_appdata: Path | None = None,
) -> bool:
    """Best-effort live desktop wallpaper refresh (Lively Wallpaper on Windows, Plash on macOS)."""
    resolved = Path(html_path).resolve()
    plat = platform or sys.platform

    if plat == "win32":
        synced = _sync_lively_library_copies(resolved, local_appdata=local_appdata)
        exe = _find_lively_cli()
        if exe:
            try:
                proc = subprocess.run(
                    [str(exe), "setwp", "--file", "reload"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        return synced > 0

    if plat == "darwin":
        if _find_plash_app():
            try:
                proc = subprocess.run(["open", "plash:reload"], capture_output=True, text=True, timeout=10)
                return proc.returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
    return False


def _find_plash_app() -> Path | None:
    candidates = [Path("/Applications/Plash.app"), Path.home() / "Applications/Plash.app"]
    return next((p for p in candidates if p.exists()), None)


def setup_live_wallpaper(html_path: Path, *, platform: str | None = None) -> LiveWallpaperResult:
    resolved = Path(html_path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Display HTML not found: {resolved}. Run `cli.py build-display` first.")

    url = resolved.as_uri()
    plat = platform or sys.platform

    if plat == "win32":
        exe = _find_lively_cli()
        if exe:
            proc = subprocess.run(
                [str(exe), "setwp", "--file", str(resolved)],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if proc.returncode == 0:
                return LiveWallpaperResult(
                    applied=True,
                    app="Lively Wallpaper",
                    url=url,
                    detail=f"Applied via {exe.name} -> {resolved}",
                )
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Lively Wallpaper failed (exit {proc.returncode}): {err[:300]}")
        return LiveWallpaperResult(
            applied=False,
            app="Lively Wallpaper",
            url=url,
            detail=(
                "Lively Wallpaper CLI not detected. Open Lively Wallpaper (or Wallpaper Engine), "
                f"click Add Wallpaper, and select: {resolved}"
            ),
        )

    if plat == "darwin":
        plash_url = f"plash:add?url={quote(url, safe='')}&title=Study%20Countdown"
        if _find_plash_app():
            proc = subprocess.run(["open", plash_url], capture_output=True, text=True, timeout=15)
            if proc.returncode == 0:
                return LiveWallpaperResult(
                    applied=True,
                    app="Plash",
                    url=url,
                    detail=f"Added live page to Plash -> {url}",
                )
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"Plash setup failed (exit {proc.returncode}): {err[:300]}")
        return LiveWallpaperResult(
            applied=False,
            app="Plash",
            url=url,
            detail=f"Plash.app not found in /Applications. Install Plash and add URL: {url}",
        )

    raise NotImplementedError(f"Live wallpaper setup is supported on Windows and macOS only (got {plat}).")
