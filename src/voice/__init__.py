"""Short local mic capture (16 kHz WAV) and $0 native OS TTS readout."""

from src.voice.capture import (
    CHANNELS,
    DEFAULT_MAX_SECONDS,
    DEFAULT_RMS_THRESHOLD,
    DEFAULT_SILENCE_SECONDS,
    HARD_MAX_SECONDS,
    MAX_WAV_BYTES,
    SAMPLE_RATE,
    clamp_max_seconds,
    load_wav_file,
    pcm_to_wav_bytes,
    record_from_chunks,
    record_voice_clip,
)
from src.voice.tts import (
    DEFAULT_EDGE_VOICE,
    TTS_AUDIO_ENV_VAR,
    TTS_ENV_VAR,
    build_audio_play_command,
    build_tts_command,
    speak_text,
)

__all__ = [
    "CHANNELS",
    "DEFAULT_EDGE_VOICE",
    "DEFAULT_MAX_SECONDS",
    "DEFAULT_RMS_THRESHOLD",
    "DEFAULT_SILENCE_SECONDS",
    "HARD_MAX_SECONDS",
    "MAX_WAV_BYTES",
    "SAMPLE_RATE",
    "TTS_AUDIO_ENV_VAR",
    "TTS_ENV_VAR",
    "build_audio_play_command",
    "build_tts_command",
    "clamp_max_seconds",
    "load_wav_file",
    "pcm_to_wav_bytes",
    "record_from_chunks",
    "record_voice_clip",
    "speak_text",
]
