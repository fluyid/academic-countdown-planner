"""100% local, offline wake-word listener ('hey gemini') via grammar-constrained Vosk."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import requests

from src.google_auth import prefer_ipv4, secrets_dir
from src.voice.capture import DEFAULT_MAX_SECONDS, SAMPLE_RATE, clamp_max_seconds, record_voice_clip

DEFAULT_WAKE_PHRASE = "hey gemini"
DEFAULT_VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
DEFAULT_VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_COOLDOWN_SECONDS = 3.0
WAKE_CHUNK_MS = 200
CHANNELS = 1


def default_vosk_model_dir() -> Path:
    override = os.environ.get("COUNTDOWN_VOSK_MODEL_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return secrets_dir() / DEFAULT_VOSK_MODEL_NAME


def build_wake_grammar(wake_phrase: str = DEFAULT_WAKE_PHRASE) -> str:
    phrase = " ".join(wake_phrase.lower().split())
    if not phrase:
        raise ValueError("wake_phrase must be non-empty.")
    return json.dumps([phrase, "[unk]"])


def detect_wake_in_result(result_json: str, wake_phrase: str = DEFAULT_WAKE_PHRASE) -> bool:
    phrase = " ".join(wake_phrase.lower().split())
    if not phrase or not result_json:
        return False
    try:
        data = json.loads(result_json)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    text = " ".join(str(data.get("text") or data.get("partial") or "").lower().split())
    if not text:
        return False
    return f" {phrase} " in f" {text} "


def _is_valid_vosk_model(path: Path) -> bool:
    return path.is_dir() and ((path / "conf").is_dir() or (path / "am").is_dir())


def _download_and_extract_model(url: str, target_dir: Path) -> Path:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    prefer_ipv4()
    tmp_zip: Path | None = None
    try:
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(
            prefix="vosk_model_", suffix=".zip", dir=target_dir.parent, delete=False
        ) as tmp:
            tmp_zip = Path(tmp.name)
            for chunk in resp.iter_content(chunk_size=1024 * 128):
                if chunk:
                    tmp.write(chunk)

        dest_root = target_dir.parent.resolve()
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            for member in zf.infolist():
                member_path = (dest_root / member.filename).resolve()
                if not member_path.is_relative_to(dest_root):
                    raise RuntimeError(f"Unsafe path in Vosk model archive: {member.filename}")
            zf.extractall(dest_root)
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download offline Vosk model from {url}: {exc}") from exc
    finally:
        if tmp_zip is not None:
            tmp_zip.unlink(missing_ok=True)

    if not _is_valid_vosk_model(target_dir):
        raise RuntimeError(f"Extracted Vosk model is missing expected files in {target_dir}.")
    return target_dir


def ensure_vosk_model(
    model_dir: str | Path | None = None,
    *,
    auto_download: bool = True,
    download_url: str = DEFAULT_VOSK_MODEL_URL,
    download_fn: Callable[[str, Path], Path] | None = None,
) -> Path:
    target = Path(model_dir).expanduser() if model_dir else default_vosk_model_dir()
    if _is_valid_vosk_model(target):
        return target
    if not auto_download:
        raise FileNotFoundError(
            f"Offline Vosk model not found at {target}. "
            f"Download and unzip {download_url} into {target.parent}, or omit --no-download."
        )
    return (download_fn or _download_and_extract_model)(download_url, target)


def wait_for_wake_from_chunks(
    chunks: Iterable[bytes],
    recognizer: Any,
    *,
    wake_phrase: str = DEFAULT_WAKE_PHRASE,
) -> bool:
    for raw in chunks:
        chunk = bytes(raw)
        if not chunk:
            break
        # Require finalized AcceptWaveform results so transient partial noise never causes false wakes.
        if recognizer.AcceptWaveform(chunk):
            if detect_wake_in_result(recognizer.Result(), wake_phrase):
                if hasattr(recognizer, "Reset"):
                    recognizer.Reset()
                return True
    return False


def _iter_forever(stream: Any, chunk_frames: int) -> Iterator[bytes]:
    while True:
        data, _overflowed = stream.read(chunk_frames)
        yield bytes(data)


def load_vosk_model(model_dir: str | Path | None = None, *, auto_download: bool = True) -> Any:
    try:
        import vosk
    except ImportError as exc:
        raise RuntimeError(
            "Offline wake-word detection requires 'vosk'. "
            "Install it in your venv: .venv\\Scripts\\pip install vosk (or pip install vosk)."
        ) from exc

    resolved = ensure_vosk_model(model_dir, auto_download=auto_download)
    vosk.SetLogLevel(-1)
    return vosk.Model(str(resolved))


def listen_for_wake_word(
    *,
    wake_phrase: str = DEFAULT_WAKE_PHRASE,
    model: Any = None,
    model_dir: str | Path | None = None,
    auto_download: bool = True,
    sample_rate: int = SAMPLE_RATE,
    chunk_ms: int = WAKE_CHUNK_MS,
    stream_factory: Callable[..., Any] | None = None,
    recognizer_factory: Callable[..., Any] | None = None,
) -> bool:
    grammar = build_wake_grammar(wake_phrase)
    chunk_frames = max(1, int(sample_rate * chunk_ms / 1000))

    if recognizer_factory is None:
        try:
            import vosk
        except ImportError as exc:
            raise RuntimeError(
                "Offline wake-word detection requires 'vosk'. "
                "Install it in your venv: .venv\\Scripts\\pip install vosk (or pip install vosk)."
            ) from exc
        loaded_model = model if model is not None else load_vosk_model(model_dir, auto_download=auto_download)
        recognizer = vosk.KaldiRecognizer(loaded_model, sample_rate, grammar)
    else:
        recognizer = recognizer_factory(sample_rate, grammar)

    portaudio_err: type[Exception] | None = None
    if stream_factory is None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                "Microphone capture requires 'sounddevice' (PortAudio). "
                "Install it in your venv: .venv\\Scripts\\pip install sounddevice (or pip install sounddevice)."
            ) from exc
        stream_factory = sd.RawInputStream
        portaudio_err = sd.PortAudioError

    try:
        with stream_factory(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            blocksize=chunk_frames,
        ) as stream:
            return wait_for_wake_from_chunks(
                _iter_forever(stream, chunk_frames),
                recognizer,
                wake_phrase=wake_phrase,
            )
    except Exception as exc:
        if portaudio_err is not None and isinstance(exc, portaudio_err):
            raise RuntimeError(f"Microphone wake listener failed: {exc}") from exc
        raise


def _play_wake_chime() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(880, 120)
            return
        except Exception:
            pass
    print("\a", end="", flush=True)


def run_wake_loop(
    *,
    wake_phrase: str = DEFAULT_WAKE_PHRASE,
    model_dir: str | Path | None = None,
    auto_download: bool = True,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    once: bool = False,
    speak: bool = False,
    chime: bool = False,
    replan_fn: Callable[[bytes], Any],
    on_result: Callable[[Any], None],
    on_status: Callable[[str], None] = lambda msg: print(msg, flush=True),
    on_error: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr, flush=True),
    listen_fn: Callable[..., bool] | None = None,
    record_fn: Callable[..., bytes] = record_voice_clip,
    speak_fn: Callable[[str], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    capped = clamp_max_seconds(max_seconds)
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be >= 0.")
    phrase = " ".join(wake_phrase.lower().split())
    if not phrase:
        raise ValueError("wake_phrase must be non-empty.")

    loaded_model = None
    if listen_fn is None:
        on_status(f"Loading offline wake-word model for '{phrase}' (0 Gemini calls while idle)...")
        loaded_model = load_vosk_model(model_dir, auto_download=auto_download)
        listen_fn = lambda: listen_for_wake_word(wake_phrase=phrase, model=loaded_model)

    if speak and speak_fn is None:
        from src.voice.tts import speak_text

        speak_fn = speak_text

    on_status(f"Listening offline for '{phrase}' (Ctrl+C to stop)...")
    try:
        while True:
            detected = listen_fn()
            if not detected:
                return 0
            if chime:
                _play_wake_chime()
            on_status(f"Wake word '{phrase}' heard — listening (up to {capped:g}s, auto-stops on silence)...")
            try:
                wav_bytes = record_fn(max_seconds=capped)
                res = replan_fn(wav_bytes)
                on_result(res)
                if speak and speak_fn is not None and getattr(res, "explanation", None):
                    speak_fn(res.explanation)
            except (ValueError, RuntimeError, FileNotFoundError, NotImplementedError) as exc:
                msg = str(exc)
                on_error(f"Error: {msg}")
                if "Daily Gemini call cap reached" in msg:
                    return 1

            if once:
                return 0
            if cooldown_seconds > 0:
                sleep_fn(cooldown_seconds)
            on_status(f"\nListening offline for '{phrase}' (Ctrl+C to stop)...")
    except KeyboardInterrupt:
        on_status("\nStopped wake-word listener.")
        return 0
