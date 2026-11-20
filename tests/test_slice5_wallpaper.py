"""Unit tests for Slice 5: Lock Screen & Wallpaper."""

from __future__ import annotations

import json
import plistlib
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

from src.planner import build_plan, load_config
from src.wallpaper import (
    DEFAULT_SCREENSHOT_NAME,
    MACOS_LAUNCHD_LABEL,
    WINDOWS_TASK_NAME,
    apply_lock_screen,
    build_capture_command,
    build_windows_lockscreen_script,
    capture_static_screenshot,
    detect_screen_resolution,
    install_schedule,
    parse_size,
    parse_time_hhmm,
    refresh_screen,
    render_macos_launchd_plist,
    render_windows_task_xml,
    setup_live_wallpaper,
    uninstall_schedule,
)


class TestSlice5Wallpaper(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_capture_command_and_size_parsing(self) -> None:
        self.assertEqual(parse_size("2560x1600"), (2560, 1600))
        with self.assertRaises(ValueError):
            parse_size("1920")
        with self.assertRaises(ValueError):
            parse_size("0x1080")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html_path = root / "index.html"
            html_path.write_text("<html></html>", encoding="utf-8")
            out_path = root / DEFAULT_SCREENSHOT_NAME

            cmd = build_capture_command(Path("chrome"), html_path, out_path, (2560, 1440), root / "prof")
            self.assertIn("--window-size=2560,1440", cmd)
            self.assertIn(f"--screenshot={out_path.resolve()}", cmd)
            self.assertTrue(cmd[-1].endswith("/index.html?mode=static"))

            def fake_run(argv, **_kwargs):
                shot = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
                Path(shot).write_bytes(b"\x89PNG\r\n\x1a\nfake")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch("src.wallpaper.capture.subprocess.run", side_effect=fake_run):
                written = capture_static_screenshot(html_path, size=(1920, 1080), browser=Path("chrome"))
            self.assertEqual(written, out_path)
            self.assertTrue(written.is_file())

    def test_detect_macos_resolution_from_system_profiler(self) -> None:
        payload = json.dumps(
            {
                "SPDisplaysDataType": [
                    {"spdisplays_ndrvs": [{"_spdisplays_pixels": "3024 x 1964", "_spdisplays_resolution": "1512 x 982"}]}
                ]
            }
        )
        completed = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
        with mock.patch("src.wallpaper.capture.subprocess.run", return_value=completed):
            self.assertEqual(detect_screen_resolution(platform="darwin"), (3024, 1964))

    def test_apply_lock_screen_windows_and_macos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "lockscreen.png"
            img.write_bytes(b"\x89PNG\r\n\x1a\nimg")

            # Windows uses WinRT via LOCKSCREEN_IMAGE_PATH env var (no path interpolation in script).
            self.assertIn("$env:LOCKSCREEN_IMAGE_PATH", build_windows_lockscreen_script())
            win_calls: list[tuple[list[str], dict[str, str]]] = []

            def fake_win(argv, **kwargs):
                win_calls.append((argv, kwargs.get("env", {})))
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch("src.wallpaper.apply.subprocess.run", side_effect=fake_win):
                applied = apply_lock_screen(img, platform="win32")
            self.assertEqual(applied, img.resolve())
            self.assertEqual(win_calls[0][0][0], "powershell.exe")
            self.assertEqual(win_calls[0][1]["LOCKSCREEN_IMAGE_PATH"], str(img.resolve()))

            # macOS alternates cache-busting _a / _b slots so System Events reloads updated PNGs.
            mac_targets: list[str] = []

            def fake_mac(argv, **_kwargs):
                mac_targets.append(argv[-1])
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch("src.wallpaper.apply.subprocess.run", side_effect=fake_mac):
                first = apply_lock_screen(img, platform="darwin")
                second = apply_lock_screen(img, platform="darwin")
            self.assertEqual(first.name, "lockscreen_a.png")
            self.assertEqual(second.name, "lockscreen_b.png")
            self.assertEqual(mac_targets, [str(first), str(second)])

    def test_setup_live_wallpaper_lively_and_plash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "index.html"
            html_path.write_text("<html></html>", encoding="utf-8")

            # Windows fallback instructions when Lively is absent.
            with mock.patch("src.wallpaper.apply._find_lively_cli", return_value=None):
                res = setup_live_wallpaper(html_path, platform="win32")
            self.assertFalse(res.applied)
            self.assertEqual(res.app, "Lively Wallpaper")
            self.assertIn(str(html_path.resolve()), res.detail)

            # Windows direct apply when Lively CLI exists.
            with (
                mock.patch("src.wallpaper.apply._find_lively_cli", return_value=Path("livelycu.exe")),
                mock.patch("src.wallpaper.apply.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_mock,
            ):
                res = setup_live_wallpaper(html_path, platform="win32")
            self.assertTrue(res.applied)
            self.assertEqual(run_mock.call_args[0][0], ["livelycu.exe", "setwp", "--file", str(html_path.resolve())])

            # macOS Plash URL scheme when Plash.app exists.
            with (
                mock.patch("src.wallpaper.apply._find_plash_app", return_value=Path("/Applications/Plash.app")),
                mock.patch("src.wallpaper.apply.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_mock,
            ):
                res = setup_live_wallpaper(html_path, platform="darwin")
            self.assertTrue(res.applied)
            self.assertEqual(res.app, "Plash")
            self.assertTrue(run_mock.call_args[0][0][1].startswith("plash:add?url="))

    def test_schedule_windows_xml_and_macos_plist(self) -> None:
        self.assertEqual(parse_time_hhmm("07:30"), (7, 30))
        with self.assertRaises(ValueError):
            parse_time_hhmm("25:00")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            py = repo / ".venv" / "Scripts" / "python.exe"
            xml_text = render_windows_task_xml(py, repo, morning_time="07:15", poll_minutes=30)
            root = ET.fromstring(xml_text)
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            cal = root.find(".//t:CalendarTrigger", ns)
            self.assertIsNotNone(cal)
            children = [c.tag.rsplit("}", 1)[-1] for c in list(cal)]
            self.assertEqual(children[:2], ["Repetition", "StartBoundary"])
            self.assertIn("T07:15:00", root.findtext(".//t:StartBoundary", namespaces=ns))
            self.assertEqual(root.findtext(".//t:Interval", namespaces=ns), "PT30M")
            self.assertEqual(root.findtext(".//t:StartWhenAvailable", namespaces=ns), "true")
            self.assertIn("refresh-screen --if-changed", root.findtext(".//t:Arguments", namespaces=ns))

            plist_bytes = render_macos_launchd_plist(py, repo, morning_time="07:15", poll_minutes=30)
            data = plistlib.loads(plist_bytes)
            self.assertEqual(data["Label"], MACOS_LAUNCHD_LABEL)
            self.assertEqual(data["StartCalendarInterval"], {"Hour": 7, "Minute": 15})
            self.assertEqual(data["StartInterval"], 1800)
            self.assertEqual(data["WatchPaths"], [str((repo / "config" / "targets.json").resolve())])
            self.assertEqual(data["ProgramArguments"][-2:], ["refresh-screen", "--if-changed"])

            # Verify install/uninstall call schtasks on Windows and launchctl on macOS.
            with mock.patch("src.wallpaper.schedule.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_mock:
                msg = install_schedule(python_exe=py, repo_dir=repo, platform="win32")
                self.assertIn(WINDOWS_TASK_NAME, msg)
                self.assertEqual(run_mock.call_args[0][0][:4], ["schtasks.exe", "/Create", "/F", "/TN"])
                uninstall_schedule(platform="win32")
                self.assertEqual(run_mock.call_args[0][0], ["schtasks.exe", "/Delete", "/F", "/TN", WINDOWS_TASK_NAME])

    def test_refresh_screen_if_changed_skips_when_identical(self) -> None:
        plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "output"
            shots: list[Path] = []
            applies: list[Path] = []

            def fake_capture(html_path, output_path=None, **_kwargs):
                dest = Path(output_path)
                dest.write_bytes(b"\x89PNG\r\n\x1a\nshot")
                shots.append(dest)
                return dest

            def fake_apply(image_path, **_kwargs):
                applies.append(Path(image_path))
                return Path(image_path)

            with (
                mock.patch("src.wallpaper.refresh.capture_static_screenshot", side_effect=fake_capture),
                mock.patch("src.wallpaper.refresh.apply_lock_screen", side_effect=fake_apply),
            ):
                first = refresh_screen(plan, self.config, output_dir=out_dir, size=(1920, 1080), if_changed=True)
                second = refresh_screen(plan, self.config, output_dir=out_dir, size=(1920, 1080), if_changed=True)
                third = refresh_screen(
                    plan,
                    self.config,
                    output_dir=out_dir,
                    completed_units={"nlp-exam": 1.0},
                    size=(1920, 1080),
                    if_changed=True,
                )

            self.assertTrue(first.changed)
            self.assertTrue(first.applied)
            self.assertFalse(second.changed)
            self.assertFalse(second.applied)
            self.assertTrue(third.changed)
            self.assertEqual(len(shots), 2)
            self.assertEqual(len(applies), 2)


if __name__ == "__main__":
    unittest.main()
