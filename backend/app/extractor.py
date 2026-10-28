"""Calls Muse Spark through Meta Model API (OpenAI-compatible Chat Completions + structured output),
and Muse Voice Transcribe for voice notes.

Failures are raised as ExtractionError. There is no fallback that pretends the model answered.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .config import settings
from .prompts import (IMAGE_RESPONSE_SCHEMA, KNOWN_CUSTOMERS_LINE, RESPONSE_SCHEMA, SYSTEM_PROMPT,
                      USER_IMAGE_TEMPLATE, USER_TEXT_TEMPLATE, USER_VOICE_TEMPLATE)
from .schemas import ModelOutput

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


class ExtractionError(Exception):
    pass


@dataclass
class Extraction:
    output: ModelOutput
    raw: dict
    model: str
    cached: bool
    usage: Optional[dict]
    latency_ms: int


def _client():
    if not settings.api_key:
        raise ExtractionError("MODEL_API_KEY is not set. Put it in backend/.env")
    from openai import OpenAI
    return OpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=90, max_retries=2)


def _cache_path(key_material: str) -> Path:
    h = hashlib.sha256(key_material.encode()).hexdigest()[:32]
    return settings.data_dir / "cache" / f"{h}.json"


def _cost(usage: Optional[dict]) -> Optional[float]:
    if not usage:
        return None
    return round(usage.get("prompt_tokens", 0) / 1e6 * settings.price_in
                 + usage.get("completion_tokens", 0) / 1e6 * settings.price_out, 6)


def _call(messages: list[dict], cache_key: str, effort: str, schema: dict = RESPONSE_SCHEMA) -> Extraction:
    if effort:
        cache_key = f"effort={effort}|{cache_key}"
    path = _cache_path(cache_key)
    if settings.cache and path.exists():
        data = json.loads(path.read_text())
        return Extraction(ModelOutput.model_validate(data["output"]), data["output"], data["model"], True,
                          data.get("usage"), 0)

    kwargs = dict(
        model=settings.model,
        messages=messages,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "kanakku_entries", "schema": schema}},
    )
    if effort:
        kwargs["reasoning_effort"] = effort

    t0 = time.monotonic()
    try:
        resp = _client().chat.completions.create(**kwargs)
    except ExtractionError:
        raise
    except Exception as e:  # network, auth, rate limit, 400 schema errors...
        raise ExtractionError(f"Meta Model API call failed: {type(e).__name__}: {e}") from e
    latency = int((time.monotonic() - t0) * 1000)

    content = resp.choices[0].message.content if resp.choices else None
    if not content:
        raise ExtractionError(f"Model returned no content (finish_reason={resp.choices[0].finish_reason if resp.choices else None})")
    try:
        raw = json.loads(content)
        output = ModelOutput.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as e:
        raise ExtractionError(f"Model output failed validation: {e}\nRaw: {content[:500]}") from e

    usage = resp.usage.model_dump() if resp.usage else None
    if usage is not None:
        usage["cost_usd"] = _cost(usage)
    if settings.cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": resp.model or settings.model, "output": raw, "usage": usage},
                                   ensure_ascii=False, indent=2))
    return Extraction(output, raw, resp.model or settings.model, False, usage, latency)


def extract_text(text: str, today: str) -> Extraction:
    user = USER_TEXT_TEMPLATE.format(today=today, text=text.strip())
    messages = [{"role": "developer", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    return _call(messages, f"{settings.model}|{SYSTEM_PROMPT}|{user}", settings.reasoning_effort)


def extract_voice_transcript(transcript: str, today: str) -> Extraction:
    user = USER_VOICE_TEMPLATE.format(today=today, text=transcript.strip())
    messages = [{"role": "developer", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    return _call(messages, f"{settings.model}|{SYSTEM_PROMPT}|{user}", settings.reasoning_effort)


# Words the shopkeeper says that a general speech model would mishear. Customer names are added per call.
VOICE_KEYWORDS = ["pattu", "thannu", "kadam", "adachu", "baaki", "muzhuvan", "chechi", "chettan", "ikka"]


def check_wav(data: bytes) -> int:
    """Meta ASR takes mono 16-bit PCM WAV at 16 or 24 kHz. Returns duration in ms."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise ExtractionError(f"Not a WAV file: {e}") from e
    if ch != 1 or width != 2 or rate not in (16000, 24000):
        raise ExtractionError(f"Need mono 16-bit WAV at 16/24 kHz, got {ch} ch, {width * 8}-bit, {rate} Hz")
    ms = frames * 1000 // rate
    if ms > 10 * 60 * 1000:
        raise ExtractionError("Voice note is longer than 10 minutes")
    return ms


def transcribe(wav: bytes, keywords: list[str]) -> dict:
    """Returns {"transcript", "duration_ms", "cost_usd", "cached"}. Cached on disk like model calls."""
    duration = check_wav(wav)
    req = {"mode": "PUSH_TO_TALK", "model": settings.asr_model, "audioEncoding": "WAV",
           "languageBias": ["English"], "keywords": (VOICE_KEYWORDS + keywords)[:100]}
    path = _cache_path(f"asr|{json.dumps(req, sort_keys=True)}|{hashlib.sha256(wav).hexdigest()}")
    if settings.cache and path.exists():
        return {**json.loads(path.read_text()), "cached": True}
    if not settings.api_key:
        raise ExtractionError("MODEL_API_KEY is not set. Put it in backend/.env")
    import httpx
    try:
        r = httpx.post(f"{settings.base_url}/asr/transcribe", headers={"Authorization": f"Bearer {settings.api_key}"},
                       files={"request": (None, json.dumps(req), "application/json"),
                              "audio": ("voice.wav", wav, "audio/wav")}, timeout=90)
    except httpx.HTTPError as e:
        raise ExtractionError(f"Voice transcription failed: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise ExtractionError(f"Voice transcription failed: HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    ms = body.get("audioDurationMs") or duration
    out = {"transcript": (body.get("transcript") or "").strip(), "duration_ms": ms,
           "cost_usd": round(ms // 1000 / 3600 * settings.asr_price_per_hour, 6)}
    if settings.cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False))
    return {**out, "cached": False}


def extract_image(image_bytes: bytes, mime: str, today: str, hint: str = "",
                  known_names: Optional[list[str]] = None) -> Extraction:
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ExtractionError(f"Unsupported image type {mime}. Use JPEG, PNG or WebP.")
    # Names are the hardest thing to read in handwriting; the shop's own customer list is the best hint.
    known = KNOWN_CUSTOMERS_LINE.format(names=", ".join(known_names[:200])) if known_names else ""
    user = USER_IMAGE_TEMPLATE.format(today=today, known=known, hint=f"Shopkeeper's note: {hint}" if hint else "")
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ]
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    return _call(messages, f"{settings.model}|{SYSTEM_PROMPT}|{user}|img:{img_hash}", settings.image_reasoning_effort,
                 IMAGE_RESPONSE_SCHEMA)


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
