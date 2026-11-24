"""Zero-cost TTS readout: Edge neural voice (en-US-AriaNeural) on Windows & macOS, with local OS fallback."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
TTS_ENV_VAR = "COUNTDOWN_TTS_TEXT"
TTS_AUDIO_ENV_VAR = "COUNTDOWN_TTS_AUDIO_PATH"


def build_tts_command(text: str, *, platform: str | None = None) -> tuple[list[str], dict[str, str] | None]:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("Nothing to speak.")

    plat = platform or sys.platform
    if plat == "win32":
        # Read text from an env var so arbitrary text is never interpolated into PowerShell.
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"try {{ $s.Speak($env:{TTS_ENV_VAR}) }} finally {{ $s.Dispose() }}"
        )
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        return cmd, {TTS_ENV_VAR: cleaned}

    if plat == "darwin":
        return ["say", cleaned], None

    raise NotImplementedError(f"Local OS TTS is supported on Windows and macOS only (got {plat}).")


def build_audio_play_command(
    audio_path: str | Path,
    *,
    platform: str | None = None,
) -> tuple[list[str], dict[str, str] | None]:
    resolved = Path(audio_path).resolve()
    plat = platform or sys.platform
    if plat == "win32":
        # Read file URI from env var so arbitrary paths are never interpolated into PowerShell.
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "Add-Type -AssemblyName PresentationCore; "
            "$p = New-Object System.Windows.Media.MediaPlayer; "
            "try { "
            f"$p.Open([Uri]::new($env:{TTS_AUDIO_ENV_VAR})); "
            "$deadline = (Get-Date).AddSeconds(5); "
            "while (-not $p.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; "
            "$ms = if ($p.NaturalDuration.HasTimeSpan) { [int]$p.NaturalDuration.TimeSpan.TotalMilliseconds } else { 5000 }; "
            "$p.Play(); "
            "Start-Sleep -Milliseconds ($ms + 250) "
            "} finally { $p.Close() }"
        )
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        return cmd, {TTS_AUDIO_ENV_VAR: resolved.as_uri()}

    if plat == "darwin":
        return ["afplay", str(resolved)], None

    raise NotImplementedError(f"Audio playback is supported on Windows and macOS only (got {plat}).")


def _synthesize_edge_tts(text: str, out_path: Path, *, voice: str = DEFAULT_EDGE_VOICE) -> None:
    import edge_tts

    edge_tts.Communicate(text, voice=voice).save_sync(str(out_path))


def _run_cmd(
    cmd: list[str],
    extra_env: dict[str, str] | None,
    *,
    label: str,
    timeout: int,
    run_fn: Callable[..., Any],
) -> None:
    env = {**os.environ, **extra_env} if extra_env else None
    proc = run_fn(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{label} failed (exit {proc.returncode}): {err[:300]}")


def speak_text(
    text: str,
    *,
    voice: str = DEFAULT_EDGE_VOICE,
    platform: str | None = None,
    timeout: int = 45,
    synthesize_fn: Callable[..., None] | None = None,
    run_fn: Callable[..., Any] = subprocess.run,
) -> None:
    cleaned = " ".join(text.split())
    if not cleaned:
        raise ValueError("Nothing to speak.")

    plat = platform or sys.platform
    if plat not in ("win32", "darwin"):
        raise NotImplementedError(f"TTS readout is supported on Windows and macOS only (got {plat}).")

    synth = synthesize_fn or _synthesize_edge_tts
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="countdown_tts_", suffix=".mp3", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        synth(cleaned, tmp_path, voice=voice)
        cmd, extra_env = build_audio_play_command(tmp_path, platform=plat)
    except Exception:
        # Fall back to $0 offline OS TTS if edge-tts is unavailable or unreachable.
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        cmd, extra_env = build_tts_command(cleaned, platform=plat)
        _run_cmd(cmd, extra_env, label="Local OS TTS", timeout=timeout, run_fn=run_fn)
        return

    try:
        _run_cmd(cmd, extra_env, label="TTS audio playback", timeout=timeout, run_fn=run_fn)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
