"""Unit tests for V2 Slice 3: Gemini Voice Replan (100% mocked, zero live mic or API calls)."""

from __future__ import annotations

import base64
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from datetime import date
from pathlib import Path
from typing import Any

import requests

from src.gemini import (
    DEFAULT_MODEL,
    MAX_AUDIO_BYTES,
    AddEventAction,
    TickTaskAction,
    UnclearAction,
    UpdateTargetDatesAction,
    generate_content,
    parse_audio_action,
    replan_from_audio,
)
from src.planner import build_plan, load_config
from src.tasks import get_or_create_tasklist, sync_plan_to_tasks
from src.voice import (
    DEFAULT_EDGE_VOICE,
    HARD_MAX_SECONDS,
    SAMPLE_RATE,
    TTS_AUDIO_ENV_VAR,
    TTS_ENV_VAR,
    build_audio_play_command,
    build_tts_command,
    clamp_max_seconds,
    load_wav_file,
    pcm_to_wav_bytes,
    record_voice_clip,
    speak_text,
)
from src.wallpaper import RefreshResult, render_schedule_definition
from tests.test_slice3_tasks import FakeTasksService
from tests.test_slice6_gemini import FakeCalendarService, _FakeResp


def _pcm_chunk(amplitude: int, frames: int = 1600) -> bytes:
    """Create a 100 ms (at 16 kHz) mono 16-bit PCM chunk at constant amplitude."""
    return struct.pack(f"<{frames}h", *([amplitude] * frames))


class _FakeRawInputStream:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)
        self.reads = 0
        self.closed = False

    def __enter__(self) -> _FakeRawInputStream:
        return self

    def __exit__(self, *exc_info) -> None:
        self.closed = True

    def read(self, frames: int) -> tuple[bytes, bool]:
        if self.reads >= len(self._chunks):
            return b"", False
        chunk = self._chunks[self.reads]
        self.reads += 1
        return chunk, False


class TestV2Slice3Voice(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.sample_wav = pcm_to_wav_bytes(_pcm_chunk(2000, frames=1600))

    def test_generate_content_audio_inline_data_and_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            usage_file = Path(tmp) / "gemini_usage.json"
            calls: list[dict[str, Any]] = []

            def ok_post(url, headers, json, timeout):
                calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
                return _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

            # Reject empty, oversized, and non-WAV audio BEFORE touching usage cap or network.
            with self.assertRaises(ValueError):
                generate_content("listen", audio_bytes=b"", api_key="k", usage_path=usage_file, post_fn=ok_post)
            with self.assertRaises(ValueError):
                generate_content(
                    "listen",
                    audio_bytes=b"x" * (MAX_AUDIO_BYTES + 1),
                    api_key="k",
                    usage_path=usage_file,
                    post_fn=ok_post,
                )
            with self.assertRaises(ValueError):
                generate_content(
                    "listen",
                    audio_bytes=self.sample_wav,
                    audio_mime_type="audio/mp3",
                    api_key="k",
                    usage_path=usage_file,
                    post_fn=ok_post,
                )
            self.assertEqual(calls, [])
            self.assertFalse(usage_file.exists())

            # Valid WAV attaches base64 inlineData alongside prompt under Flash-only + MINIMAL + daily cap.
            out = generate_content(
                "listen",
                audio_bytes=self.sample_wav,
                api_key="k-secret",
                usage_path=usage_file,
                daily_cap=1,
                today=date(2026, 11, 21),
                post_fn=ok_post,
            )
            self.assertEqual(out, "ok")
            self.assertEqual(len(calls), 1)
            self.assertIn(f"/{DEFAULT_MODEL}:generateContent", calls[0]["url"])
            self.assertEqual(calls[0]["headers"]["x-goog-api-key"], "k-secret")
            self.assertEqual(calls[0]["json"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "MINIMAL"})
            parts = calls[0]["json"]["contents"][0]["parts"]
            self.assertEqual(parts[0], {"text": "listen"})
            self.assertEqual(parts[1]["inlineData"]["mimeType"], "audio/wav")
            self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), self.sample_wav)

            # Daily cap blocks a second audio request immediately.
            with self.assertRaisesRegex(RuntimeError, "Daily Gemini call cap reached"):
                generate_content(
                    "again",
                    audio_bytes=self.sample_wav,
                    api_key="k-secret",
                    usage_path=usage_file,
                    daily_cap=1,
                    today=date(2026, 11, 21),
                    post_fn=ok_post,
                )
            self.assertEqual(len(calls), 1)

            # Zero retries on network error with audio attached.
            attempts = 0

            def fail_post(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                raise requests.Timeout("boom")

            with self.assertRaisesRegex(RuntimeError, "no retries"):
                generate_content(
                    "timeout",
                    audio_bytes=self.sample_wav,
                    api_key="k-secret",
                    usage_path=usage_file,
                    daily_cap=5,
                    today=date(2026, 11, 22),
                    post_fn=fail_post,
                )
            self.assertEqual(attempts, 1)

    def test_silence_stop_capture_and_wav_file_validation(self) -> None:
        quiet = _pcm_chunk(40)
        loud = _pcm_chunk(2500)

        # Leading silence (2) + speech (3) + brief pause (1) + speech (2) + trailing silence (4 -> stops at 3).
        stream_chunks = [quiet] * 2 + [loud] * 3 + [quiet] + [loud] * 2 + [quiet] * 10
        fake_stream = _FakeRawInputStream(stream_chunks)
        wav_bytes = record_voice_clip(
            max_seconds=10.0,
            silence_seconds=0.3,
            rms_threshold=500,
            chunk_ms=100,
            stream_factory=lambda **kw: fake_stream,
        )
        self.assertTrue(fake_stream.closed)
        self.assertEqual(fake_stream.reads, 11)  # 2 + 3 + 1 + 2 + 3 trailing quiet chunks
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), SAMPLE_RATE)
            self.assertEqual(wf.getnframes(), 11 * 1600)

        # Hard duration cap clamps > 15s to 15s and stops continuous speech at max_seconds.
        self.assertEqual(clamp_max_seconds(99), HARD_MAX_SECONDS)
        with self.assertRaises(ValueError):
            clamp_max_seconds(0)
        continuous = _FakeRawInputStream([loud] * 50)
        clipped_wav = record_voice_clip(
            max_seconds=0.5,
            silence_seconds=0.3,
            chunk_ms=100,
            stream_factory=lambda **kw: continuous,
        )
        self.assertEqual(continuous.reads, 5)
        with wave.open(io.BytesIO(clipped_wav), "rb") as wf:
            self.assertEqual(wf.getnframes(), 5 * 1600)

        # Pure silence raises locally instead of wasting a Gemini call.
        silent_stream = _FakeRawInputStream([quiet] * 5)
        with self.assertRaisesRegex(RuntimeError, "No speech detected"):
            record_voice_clip(max_seconds=0.5, chunk_ms=100, stream_factory=lambda **kw: silent_stream)

        # load_wav_file validates existence, size, WAV header, and non-zero frames.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                load_wav_file(base / "missing.wav")

            empty = base / "empty.wav"
            empty.write_bytes(b"")
            with self.assertRaises(ValueError):
                load_wav_file(empty)

            not_wav = base / "fake.wav"
            not_wav.write_bytes(b"not a wav header at all")
            with self.assertRaises(ValueError):
                load_wav_file(not_wav)

            zero_frames = base / "zero.wav"
            zero_frames.write_bytes(pcm_to_wav_bytes(b""))
            with self.assertRaises(ValueError):
                load_wav_file(zero_frames)

            valid = base / "ok.wav"
            valid.write_bytes(self.sample_wav)
            self.assertEqual(load_wav_file(valid), self.sample_wav)
            with self.assertRaisesRegex(ValueError, "too large"):
                load_wav_file(valid, max_bytes=len(self.sample_wav) - 1)

    def test_replan_from_audio_end_to_end(self) -> None:
        cal_svc = FakeCalendarService()
        task_svc = FakeTasksService()
        list_id = get_or_create_tasklist(task_svc, "Countdown Planner (Test)")
        seed_plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        sync_plan_to_tasks(task_svc, list_id, "Countdown Planner (Test)", seed_plan)

        # 1. Add-event audio replan -> Calendar write + pure-Python reshuffle + Tasks sync + refresh + explain.
        recorded_calls: list[dict[str, Any]] = []

        def fake_gemini_add(prompt: str, **kwargs) -> str:
            recorded_calls.append({"prompt": prompt, **kwargs})
            if len(recorded_calls) == 1:
                return json.dumps({
                    "action": "add_event",
                    "calendar": "personal",
                    "summary": "Dinner with friends",
                    "date": "2026-11-28",
                    "all_day": True,
                })
            return "Blocked Nov 28 for Dinner with friends and shifted remaining units cleanly."

        refreshes: list[dict[str, Any]] = []

        def fake_refresh(plan, config, **kwargs) -> RefreshResult:
            refreshes.append({"plan": plan, **kwargs})
            return RefreshResult(Path("output/index.html"), Path("output/lockscreen.png"), None, (1920, 1080), True, False)

        res_add = replan_from_audio(
            self.sample_wav,
            today=date(2026, 11, 20),
            apply_lock_screen=False,
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=fake_gemini_add,
            refresh_fn=fake_refresh,
        )
        self.assertIsInstance(res_add.action, AddEventAction)
        self.assertEqual(recorded_calls[0]["audio_bytes"], self.sample_wav)
        self.assertEqual(recorded_calls[0]["audio_mime_type"], "audio/wav")
        self.assertNotIn("audio_bytes", recorded_calls[1])  # Job 2 explanation is text-only.
        self.assertEqual(len(cal_svc.inserted), 1)
        nov28 = next(d for d in res_add.after_plan.days if d.day == date(2026, 11, 28))
        self.assertEqual(nov28.day_type, "blocked")
        self.assertEqual(len(refreshes), 1)

        # 2. Tick-task audio replan -> exact task confirmation + progress preserved.
        today_task = next(t for t in task_svc.tasks_by_list[list_id] if (t.get("due") or "").startswith("2026-11-20"))
        tick_replies = iter([
            json.dumps({"action": "tick_task", "task_id": today_task["id"]}),
            f"Checked off '{today_task['title']}' and updated the schedule.",
        ])
        res_tick = replan_from_audio(
            self.sample_wav,
            today=date(2026, 11, 20),
            apply_lock_screen=False,
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=lambda *a, **kw: next(tick_replies),
            refresh_fn=fake_refresh,
        )
        self.assertIsInstance(res_tick.action, TickTaskAction)
        self.assertEqual(res_tick.ticked_task["id"], today_task["id"])
        self.assertEqual(today_task["status"], "completed")
        self.assertEqual(res_tick.sync_result.completed_preserved, 1)

        # Hallucinated task_id from audio must be rejected as UnclearAction.
        hallucinated = parse_audio_action(
            self.sample_wav,
            today=date(2026, 11, 20),
            calendars={"personal": "Kai Nath", "university": "IU"},
            open_tasks=[{"id": "t-real", "title": "Real task", "due": "2026-11-21T00:00:00.000Z"}],
            call_gemini=lambda *a, **kw: json.dumps({"action": "tick_task", "task_id": "t-fake"}),
        )
        self.assertIsInstance(hallucinated, UnclearAction)

        # 3. Unclear audio action -> zero writes, zero refresh, only 1 Gemini call.
        cal_count_before = len(cal_svc.inserted)
        unclear_calls = 0

        def fake_gemini_unclear(*a, **kw) -> str:
            nonlocal unclear_calls
            unclear_calls += 1
            return json.dumps({"action": "unclear", "reason": "Audio was muffled."})

        res_unclear = replan_from_audio(
            self.sample_wav,
            today=date(2026, 11, 20),
            calendar_service=cal_svc,
            tasks_service=task_svc,
            call_gemini=fake_gemini_unclear,
            refresh_fn=lambda *a, **kw: self.fail("refresh_screen must not run on unclear audio"),
        )
        self.assertIsInstance(res_unclear.action, UnclearAction)
        self.assertEqual(unclear_calls, 1)
        self.assertEqual(len(cal_svc.inserted), cal_count_before)
        self.assertIsNone(res_unclear.sync_result)

        # 4. Combined target due-date update via audio (Ethics Essay + CV Assignment -> Dec 17)
        #    updates targets.json, extends plan_window through Dec 17, syncs Tasks, and refreshes screen.
        with tempfile.TemporaryDirectory() as tmp:
            cfg_copy = Path(tmp) / "targets.json"
            cfg_copy.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            seen_prompts: list[str] = []

            def fake_gemini_dates(prompt: str, **kwargs) -> str:
                seen_prompts.append(prompt)
                if len(seen_prompts) == 1:
                    return json.dumps({
                        "action": "update_target_dates",
                        "target_updates": [
                            {"target_id": "ethics-essay", "date": "2026-12-17"},
                            {"target_id": "cv-assignment", "date": "2026-12-17"},
                        ],
                    })
                return "Updated Ethics Essay and Computer Vision Assignment due dates to Dec 17."

            res_dates = replan_from_audio(
                self.sample_wav,
                config_path=cfg_copy,
                today=date(2026, 11, 24),
                apply_lock_screen=False,
                calendar_service=cal_svc,
                tasks_service=task_svc,
                call_gemini=fake_gemini_dates,
                refresh_fn=fake_refresh,
            )
            self.assertIsInstance(res_dates.action, UpdateTargetDatesAction)
            self.assertIn("CONFIGURED TARGETS:", seen_prompts[0])
            self.assertIn("ethics-essay", seen_prompts[0])
            self.assertEqual(res_dates.after_plan.end_date, date(2026, 12, 17))
            saved = json.loads(cfg_copy.read_text(encoding="utf-8"))
            saved_dates = {t["id"]: t["date"] for t in saved["targets"]}
            self.assertEqual(saved_dates["ethics-essay"], "2026-12-17")
            self.assertEqual(saved_dates["cv-assignment"], "2026-12-17")
            self.assertIn("Ethics Essay -> 2026-12-17", res_dates.action_summary)
            self.assertIn("Computer Vision Assignment -> 2026-12-17", res_dates.action_summary)

        # Hallucinated target_id must be rejected as UnclearAction (never writes config).
        bad_target = parse_audio_action(
            self.sample_wav,
            today=date(2026, 11, 24),
            calendars={"personal": "Kai Nath", "university": "IU"},
            open_tasks=[],
            targets=self.config["targets"],
            call_gemini=lambda *a, **kw: json.dumps({
                "action": "update_target_dates",
                "target_updates": [{"target_id": "nonexistent-essay", "date": "2026-12-17"}],
            }),
        )
        self.assertIsInstance(bad_target, UnclearAction)

    def test_local_os_tts_commands_and_schedule_isolation(self) -> None:
        tricky = "Moved NLP to Dec 2; $(calc.exe) 'quoted'"

        # Windows: passes text strictly via env var, never interpolated into PowerShell script.
        win_cmd, win_env = build_tts_command(tricky, platform="win32")
        self.assertEqual(win_cmd[0], "powershell.exe")
        self.assertIn(f"$env:{TTS_ENV_VAR}", win_cmd[-1])
        self.assertNotIn("calc.exe", win_cmd[-1])
        self.assertEqual(win_env, {TTS_ENV_VAR: tricky})

        # macOS: passes text directly as argv to `say`.
        mac_cmd, mac_env = build_tts_command(tricky, platform="darwin")
        self.assertEqual(mac_cmd, ["say", tricky])
        self.assertIsNone(mac_env)

        with self.assertRaises(ValueError):
            build_tts_command("   ", platform="win32")
        with self.assertRaises(NotImplementedError):
            build_tts_command("hello", platform="linux")

        # Audio playback commands for edge-tts MP3 (Windows MediaPlayer via env URI + macOS afplay argv).
        sample_mp3 = Path("tmp_clip; $(calc).mp3")
        win_play_cmd, win_play_env = build_audio_play_command(sample_mp3, platform="win32")
        self.assertEqual(win_play_cmd[0], "powershell.exe")
        self.assertIn(f"$env:{TTS_AUDIO_ENV_VAR}", win_play_cmd[-1])
        self.assertNotIn("calc", win_play_cmd[-1])
        self.assertTrue(win_play_env[TTS_AUDIO_ENV_VAR].startswith("file:///"))

        mac_play_cmd, mac_play_env = build_audio_play_command(sample_mp3, platform="darwin")
        self.assertEqual(mac_play_cmd[0], "afplay")
        self.assertIsNone(mac_play_env)

        # speak_text uses en-US-AriaNeural via edge-tts, plays temp MP3, and deletes temp file afterwards.
        created_paths: list[Path] = []
        seen: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_synth(text: str, out_path: Path, *, voice: str) -> None:
            self.assertEqual(voice, DEFAULT_EDGE_VOICE)
            out_path.write_bytes(b"ID3-fake-mp3")
            created_paths.append(out_path)

        def ok_run(cmd, env=None, **kw):
            seen.append((cmd, env))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        speak_text("All set.", platform="win32", synthesize_fn=fake_synth, run_fn=ok_run)
        self.assertEqual(len(created_paths), 1)
        self.assertFalse(created_paths[0].exists())
        self.assertIn(TTS_AUDIO_ENV_VAR, seen[0][1])

        speak_text("All set.", platform="darwin", synthesize_fn=fake_synth, run_fn=ok_run)
        self.assertEqual(seen[1][0][0], "afplay")
        self.assertFalse(created_paths[1].exists())

        # If edge-tts fails (e.g. offline), speak_text falls back cleanly to local OS TTS.
        def failing_synth(*args, **kwargs):
            raise RuntimeError("offline")

        speak_text("Fallback test.", platform="win32", synthesize_fn=failing_synth, run_fn=ok_run)
        self.assertEqual(seen[2][1][TTS_ENV_VAR], "Fallback test.")

        with self.assertRaisesRegex(RuntimeError, "Local OS TTS failed"):
            speak_text(
                "All set.",
                platform="darwin",
                synthesize_fn=failing_synth,
                run_fn=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "audio unavailable"),
            )

        # Scheduled refreshes never reference voice or gemini.
        for plat in ("win32", "darwin"):
            sched_def = render_schedule_definition(platform=plat)
            self.assertIn("refresh-screen", sched_def)
            self.assertNotIn("voice", sched_def.lower())
            self.assertNotIn("gemini", sched_def.lower())

        # Verify cli.py lazy-imports src.voice and src.gemini (not loaded at module import).
        import cli  # noqa: F401

        cli_source = Path(cli.__file__).read_text(encoding="utf-8")
        top_imports = cli_source.split("def ", 1)[0]
        self.assertNotIn("src.voice", top_imports)
        self.assertNotIn("src.gemini", top_imports)
        self.assertIsNotNone(sys.modules.get("cli"))


if __name__ == "__main__":
    unittest.main()
