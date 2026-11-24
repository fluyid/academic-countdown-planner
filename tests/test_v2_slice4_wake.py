"""Unit tests for V2 Slice 4: Offline 'Hey Gemini' wake-word listener (100% mocked, zero live mic/API)."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.gemini import AddEventAction, replan_from_audio
from src.gemini.client import REPO_ROOT
from src.planner import build_plan, load_config
from src.tasks import get_or_create_tasklist, sync_plan_to_tasks
from src.voice import (
    DEFAULT_WAKE_PHRASE,
    build_wake_grammar,
    default_vosk_model_dir,
    detect_wake_in_result,
    ensure_vosk_model,
    listen_for_wake_word,
    pcm_to_wav_bytes,
    run_wake_loop,
)
from src.voice.wake import _download_and_extract_model
from src.wallpaper import RefreshResult, render_schedule_definition
from tests.test_slice3_tasks import FakeTasksService
from tests.test_slice6_gemini import FakeCalendarService
from tests.test_v2_slice3_voice import _FakeRawInputStream, _pcm_chunk


class _FakeVoskRecognizer:
    """Scripted Vosk KaldiRecognizer mock returning (accept_bool, result_json, partial_json) per chunk."""

    def __init__(self, script: list[tuple[bool, str, str]]):
        self._script = list(script)
        self.index = 0
        self.reset_count = 0
        self._last_result = '{"text": ""}'
        self._last_partial = '{"partial": ""}'

    def AcceptWaveform(self, data: bytes) -> bool:
        if self.index >= len(self._script):
            self._last_result = '{"text": ""}'
            self._last_partial = '{"partial": ""}'
            return False
        accepted, res, partial = self._script[self.index]
        self.index += 1
        self._last_result = res
        self._last_partial = partial
        return accepted

    def Result(self) -> str:
        return self._last_result

    def PartialResult(self) -> str:
        return self._last_partial

    def Reset(self) -> None:
        self.reset_count += 1


class TestV2Slice4Wake(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.sample_wav = pcm_to_wav_bytes(_pcm_chunk(2000, frames=1600))

    def test_grammar_phrase_matching_and_model_outside_repo(self) -> None:
        self.assertEqual(json.loads(build_wake_grammar("  Hey   Gemini  ")), ["hey gemini", "[unk]"])
        with self.assertRaises(ValueError):
            build_wake_grammar("   ")

        self.assertTrue(detect_wake_in_result('{"text": "hey gemini"}'))
        self.assertTrue(detect_wake_in_result('{"partial": "[unk] Hey Gemini"}'))
        self.assertFalse(detect_wake_in_result('{"text": "[unk]"}'))
        self.assertFalse(detect_wake_in_result('{"text": "hey"}'))
        self.assertFalse(detect_wake_in_result('{"partial": "gemini"}'))
        self.assertFalse(detect_wake_in_result("not-json"))

        # Default offline Vosk model path must live outside the git repository.
        model_path = default_vosk_model_dir().resolve()
        self.assertFalse(model_path.is_relative_to(REPO_ROOT))

    def test_listen_for_wake_word_partial_final_and_idle_eof(self) -> None:
        chunk = _pcm_chunk(100, frames=3200)

        # 1. Partial match triggers immediately, resets recognizer, and closes mic stream.
        rec_partial = _FakeVoskRecognizer([
            (False, "", '{"partial": ""}'),
            (True, '{"text": "[unk]"}', ""),
            (False, "", '{"partial": "hey gemini"}'),
            (False, "", '{"partial": "never reached"}'),
        ])
        stream1 = _FakeRawInputStream([chunk] * 4)
        grammars_seen: list[str] = []

        detected = listen_for_wake_word(
            wake_phrase=DEFAULT_WAKE_PHRASE,
            stream_factory=lambda **kw: stream1,
            recognizer_factory=lambda sr, g: (grammars_seen.append(g) or rec_partial),
        )
        self.assertTrue(detected)
        self.assertEqual(stream1.reads, 3)
        self.assertTrue(stream1.closed)
        self.assertEqual(rec_partial.reset_count, 1)
        self.assertEqual(json.loads(grammars_seen[0]), ["hey gemini", "[unk]"])

        # 2. Final waveform result match also triggers cleanly.
        rec_final = _FakeVoskRecognizer([(True, '{"text": "hey gemini"}', "")])
        stream2 = _FakeRawInputStream([chunk] * 2)
        self.assertTrue(
            listen_for_wake_word(
                stream_factory=lambda **kw: stream2,
                recognizer_factory=lambda sr, g: rec_final,
            )
        )
        self.assertEqual(rec_final.reset_count, 1)

        # 3. Stream ends without wake phrase -> returns False, zero false positives.
        rec_idle = _FakeVoskRecognizer([(True, '{"text": "[unk]"}', ""), (False, "", '{"partial": ""}')])
        stream3 = _FakeRawInputStream([chunk] * 2)
        self.assertFalse(
            listen_for_wake_word(
                stream_factory=lambda **kw: stream3,
                recognizer_factory=lambda sr, g: rec_idle,
            )
        )
        self.assertTrue(stream3.closed)

    def test_ensure_vosk_model_and_zip_slip_protection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            model_dir = base / "vosk-model-small-en-us-0.15"

            # Missing model with auto_download=False raises FileNotFoundError with helpful hint.
            with self.assertRaisesRegex(FileNotFoundError, "Offline Vosk model not found"):
                ensure_vosk_model(model_dir, auto_download=False)

            # Missing model with auto_download=True delegates to download_fn once.
            dl_calls: list[tuple[str, Path]] = []

            def fake_download(url: str, target: Path) -> Path:
                dl_calls.append((url, target))
                (target / "conf").mkdir(parents=True)
                return target

            resolved = ensure_vosk_model(model_dir, auto_download=True, download_fn=fake_download)
            self.assertEqual(resolved, model_dir)
            self.assertEqual(len(dl_calls), 1)

            # Existing valid model directory skips download completely.
            resolved2 = ensure_vosk_model(
                model_dir,
                auto_download=True,
                download_fn=lambda *a: self.fail("must not re-download existing model"),
            )
            self.assertEqual(resolved2, model_dir)

            # Zip-slip archive must be rejected before extraction.
            evil_zip = base / "evil.zip"
            with zipfile.ZipFile(evil_zip, "w") as zf:
                zf.writestr("../escape.txt", "boom")

            class _FakeStreamResp:
                def raise_for_status(self) -> None:
                    pass

                def iter_content(self, chunk_size: int = 1024):
                    yield evil_zip.read_bytes()

            with patch("src.voice.wake.requests.get", return_value=_FakeStreamResp()):
                with self.assertRaisesRegex(RuntimeError, "Unsafe path"):
                    _download_and_extract_model("https://example.invalid/model.zip", base / "sub" / "model")

    def test_run_wake_loop_end_to_end_resilience_and_billing_guardrails(self) -> None:
        cal_svc = FakeCalendarService()
        task_svc = FakeTasksService()
        list_id = get_or_create_tasklist(task_svc, "Countdown Planner (Test)")
        seed_plan = build_plan(self.config, [], start_date=date(2026, 11, 20))
        sync_plan_to_tasks(task_svc, list_id, "Countdown Planner (Test)", seed_plan)

        # 1. Idle stream ending never touches record_fn or replan_fn (0 Gemini calls while idle).
        rc_idle = run_wake_loop(
            chime=False,
            listen_fn=lambda: False,
            record_fn=lambda **kw: self.fail("must not record while idle"),
            replan_fn=lambda wav: self.fail("must not call Gemini while idle"),
            on_result=lambda res: None,
            on_status=lambda msg: None,
            on_error=lambda msg: None,
        )
        self.assertEqual(rc_idle, 0)

        # 2. Single wake activation (--once) runs full V2-3 replan_from_audio + speaks explanation.
        replies = iter([
            json.dumps({
                "action": "add_event",
                "calendar": "personal",
                "summary": "Coffee",
                "date": "2026-11-28",
                "all_day": True,
            }),
            "Blocked Nov 28 for Coffee and updated your schedule.",
        ])
        results: list[Any] = []
        spoken: list[str] = []

        rc_once = run_wake_loop(
            once=True,
            speak=True,
            chime=False,
            listen_fn=lambda: True,
            record_fn=lambda **kw: self.sample_wav,
            replan_fn=lambda wav: replan_from_audio(
                wav,
                today=date(2026, 11, 20),
                apply_lock_screen=False,
                calendar_service=cal_svc,
                tasks_service=task_svc,
                call_gemini=lambda *a, **kw: next(replies),
                refresh_fn=lambda *a, **kw: RefreshResult(
                    Path("output/index.html"), Path("output/lockscreen.png"), None, (1920, 1080), True, False
                ),
            ),
            on_result=results.append,
            on_status=lambda msg: None,
            on_error=lambda msg: None,
            speak_fn=spoken.append,
            sleep_fn=lambda s: None,
        )
        self.assertEqual(rc_once, 0)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0].action, AddEventAction)
        self.assertEqual(spoken, ["Blocked Nov 28 for Coffee and updated your schedule."])

        # 3. Transient error sleeps cooldown and continues; daily cap error exits immediately with code 1.
        listen_seq = iter([True, True, True])
        record_seq = iter([RuntimeError("No speech detected"), self.sample_wav])
        sleeps: list[float] = []
        errors: list[str] = []

        def scripted_record(**kw):
            item = next(record_seq)
            if isinstance(item, Exception):
                raise item
            return item

        rc_cap = run_wake_loop(
            cooldown_seconds=2.5,
            chime=False,
            listen_fn=lambda: next(listen_seq),
            record_fn=scripted_record,
            replan_fn=lambda wav: (_ for _ in ()).throw(RuntimeError("Daily Gemini call cap reached (25/25).")),
            on_result=lambda res: None,
            on_status=lambda msg: None,
            on_error=errors.append,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(rc_cap, 1)
        self.assertEqual(sleeps, [2.5])
        self.assertEqual(len(errors), 2)
        self.assertIn("Daily Gemini call cap reached", errors[1])

        # 4. Ctrl+C exits cleanly with 0, and scheduled refreshes never reference listen/voice/gemini.
        rc_ctrl_c = run_wake_loop(
            chime=False,
            listen_fn=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
            replan_fn=lambda wav: None,
            on_result=lambda res: None,
            on_status=lambda msg: None,
            on_error=lambda msg: None,
        )
        self.assertEqual(rc_ctrl_c, 0)

        for plat in ("win32", "darwin"):
            sched_def = render_schedule_definition(platform=plat)
            self.assertNotIn("listen", sched_def.lower())
            self.assertNotIn("voice", sched_def.lower())
            self.assertNotIn("gemini", sched_def.lower())


if __name__ == "__main__":
    unittest.main()
