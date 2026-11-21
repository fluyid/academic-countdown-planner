"""Zero-cost local OS text-to-speech readout (Windows System.Speech + macOS say)."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Callable

TTS_ENV_VAR = "COUNTDOWN_TTS_TEXT"


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


def speak_text(
    text: str,
    *,
    platform: str | None = None,
    timeout: int = 30,
    run_fn: Callable[..., Any] = subprocess.run,
) -> None:
    cmd, extra_env = build_tts_command(text, platform=platform)
    env = {**os.environ, **extra_env} if extra_env else None
    proc = run_fn(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Local OS TTS failed (exit {proc.returncode}): {err[:300]}")
