"""Zero-accidental-billing Gemini REST client (Flash-only, capped, no retries)."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests

from src.google_auth import secrets_dir

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
ALLOWED_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash")
DEFAULT_DAILY_CAP = 25
MAX_OUTPUT_TOKENS_CAP = 512
DEFAULT_TIMEOUT_SECONDS = 20
USAGE_FILENAME = "gemini_usage.json"


def _assert_outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or resolved.is_relative_to(REPO_ROOT):
        raise ValueError(f"Gemini secret/state file must live outside the repo: {resolved}")
    return resolved


def _parse_dotenv_key(env_file: Path, key_name: str = "GEMINI_API_KEY") -> str | None:
    if not env_file.is_file():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, val = line.split("=", 1)
        if name.strip() != key_name:
            continue
        cleaned = val.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
            cleaned = cleaned[1:-1].strip()
        return cleaned or None
    return None


def load_api_key(env_path: Path | None = None) -> str:
    env_val = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_val:
        return env_val
    path = _assert_outside_repo(env_path or secrets_dir() / ".env")
    key = _parse_dotenv_key(path)
    if not key:
        raise FileNotFoundError(
            f"Missing GEMINI_API_KEY. Set GEMINI_API_KEY or add GEMINI_API_KEY=... to {path}"
        )
    return key


def check_and_increment_usage(
    usage_path: Path | None = None,
    *,
    daily_cap: int = DEFAULT_DAILY_CAP,
    today: date | None = None,
) -> int:
    if daily_cap < 1:
        raise ValueError("daily_cap must be >= 1.")
    path = _assert_outside_repo(usage_path or secrets_dir() / USAGE_FILENAME)
    day_str = (today or date.today()).isoformat()

    count = 0
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("date") == day_str:
                count = int(data.get("count", 0))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError(f"Corrupt Gemini usage file at {path}: {exc}") from exc

    if count >= daily_cap:
        raise RuntimeError(f"Daily Gemini call cap reached ({count}/{daily_cap} on {day_str}). Refusing API call.")

    new_count = count + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": day_str, "count": new_count}), encoding="utf-8")
    return new_count


def generate_content(
    prompt: str,
    *,
    system: str | None = None,
    response_schema: dict[str, Any] | None = None,
    max_output_tokens: int = 256,
    temperature: float = 0.0,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    env_path: Path | None = None,
    usage_path: Path | None = None,
    daily_cap: int = DEFAULT_DAILY_CAP,
    today: date | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    post_fn: Callable[..., Any] | None = None,
) -> str:
    if model not in ALLOWED_MODELS:
        raise ValueError(f"Model {model!r} is not allowed; use one of {ALLOWED_MODELS}.")
    if not (1 <= max_output_tokens <= MAX_OUTPUT_TOKENS_CAP):
        raise ValueError(f"max_output_tokens must be in 1..{MAX_OUTPUT_TOKENS_CAP} (got {max_output_tokens}).")

    key = (api_key or "").strip() or load_api_key(env_path=env_path)
    # Increment cap before network I/O so failed/timed-out requests cannot bypass the daily limit.
    check_and_increment_usage(usage_path=usage_path, daily_cap=daily_cap, today=today)

    gen_config: dict[str, Any] = {
        "maxOutputTokens": max_output_tokens,
        "temperature": temperature,
        # Keep thinking at MINIMAL so Gemini 3.5 Flash stays cheap and never truncates JSON output.
        "thinkingConfig": {"thinkingLevel": "MINIMAL"},
    }
    if response_schema is not None:
        gen_config["responseMimeType"] = "application/json"
        gen_config["responseSchema"] = response_schema

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": gen_config,
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    # Single request, no retries — fail fast to prevent accidental billing storms.
    url = f"{API_BASE}/{model}:generateContent"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    try:
        resp = (post_fn or requests.post)(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Gemini request failed (no retries): {exc.__class__.__name__}") from exc

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = (resp.text or "")[:200]
        raise RuntimeError(f"Gemini API error HTTP {resp.status_code}: {detail}".strip())

    try:
        body = resp.json()
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini returned an unexpected response structure.") from exc

    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text
