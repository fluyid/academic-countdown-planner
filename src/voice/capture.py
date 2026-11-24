"""Short 16 kHz mono 16-bit PCM WAV capture (silence-stop + hard duration cap) and WAV file loader."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
DEFAULT_MAX_SECONDS = 10.0
HARD_MAX_SECONDS = 15.0
DEFAULT_SILENCE_SECONDS = 1.8
DEFAULT_CHUNK_MS = 100
DEFAULT_RMS_THRESHOLD = 500
MAX_WAV_BYTES = 2 * 1024 * 1024


def clamp_max_seconds(max_seconds: float) -> float:
    if max_seconds <= 0:
        raise ValueError("max_seconds must be > 0.")
    return min(float(max_seconds), HARD_MAX_SECONDS)


def _pcm16_rms(chunk: bytes) -> float:
    n = len(chunk) // SAMPLE_WIDTH_BYTES
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", chunk[: n * SAMPLE_WIDTH_BYTES])
    return math.sqrt(sum(s * s for s in samples) / n)


def pcm_to_wav_bytes(
    pcm_bytes: bytes,
    *,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def record_from_chunks(
    chunks: Iterable[bytes],
    *,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
    rms_threshold: float = DEFAULT_RMS_THRESHOLD,
    sample_rate: int = SAMPLE_RATE,
    chunk_ms: int = DEFAULT_CHUNK_MS,
) -> bytes:
    capped = clamp_max_seconds(max_seconds)
    if silence_seconds <= 0:
        raise ValueError("silence_seconds must be > 0.")
    chunk_seconds = chunk_ms / 1000.0
    max_chunks = max(1, int(math.ceil(capped / chunk_seconds)))
    silence_limit = max(1, int(math.ceil(silence_seconds / chunk_seconds)))

    collected: list[bytes] = []
    speech_detected = False
    silent_after_speech = 0

    for raw in chunks:
        chunk = bytes(raw)
        if not chunk:
            break
        collected.append(chunk)
        if _pcm16_rms(chunk) >= rms_threshold:
            speech_detected = True
            silent_after_speech = 0
        elif speech_detected:
            silent_after_speech += 1
            if silent_after_speech >= silence_limit:
                break
        if len(collected) >= max_chunks:
            break

    if not speech_detected:
        raise RuntimeError("No speech detected before timeout. Speak closer to the mic or pass --file PATH.")
    return pcm_to_wav_bytes(b"".join(collected), sample_rate=sample_rate, channels=CHANNELS)


def _iter_input_chunks(stream: Any, chunk_frames: int, max_chunks: int) -> Iterator[bytes]:
    for _ in range(max_chunks):
        data, _overflowed = stream.read(chunk_frames)
        yield bytes(data)


def record_voice_clip(
    *,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    silence_seconds: float = DEFAULT_SILENCE_SECONDS,
    rms_threshold: float = DEFAULT_RMS_THRESHOLD,
    sample_rate: int = SAMPLE_RATE,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    stream_factory: Callable[..., Any] | None = None,
) -> bytes:
    capped = clamp_max_seconds(max_seconds)
    chunk_frames = max(1, int(sample_rate * chunk_ms / 1000))
    max_chunks = max(1, int(math.ceil(capped / (chunk_ms / 1000.0))))

    portaudio_err: type[Exception] | None = None
    if stream_factory is None:
        # Lazy-import sounddevice so non-voice commands and tests need no audio hardware.
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
            return record_from_chunks(
                _iter_input_chunks(stream, chunk_frames, max_chunks),
                max_seconds=capped,
                silence_seconds=silence_seconds,
                rms_threshold=rms_threshold,
                sample_rate=sample_rate,
                chunk_ms=chunk_ms,
            )
    except Exception as exc:
        if portaudio_err is not None and isinstance(exc, portaudio_err):
            raise RuntimeError(f"Microphone capture failed: {exc}") from exc
        raise


def load_wav_file(path: str | Path, *, max_bytes: int = MAX_WAV_BYTES) -> bytes:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"Audio file not found: {resolved}")
    size = resolved.stat().st_size
    if size == 0:
        raise ValueError(f"Audio file is empty: {resolved}")
    if size > max_bytes:
        raise ValueError(f"Audio file too large ({size} bytes; max {max_bytes} bytes): {resolved}")

    data = resolved.read_bytes()
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getnframes() <= 0:
                raise ValueError(f"WAV file contains no audio frames: {resolved}")
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Invalid WAV file: {resolved}") from exc
    return data
