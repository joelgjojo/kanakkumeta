"""Calls Muse Spark through Meta Model API (OpenAI-compatible Chat Completions + structured output).

Failures are raised as ExtractionError. There is no fallback that pretends the model answered.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .config import settings
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, USER_IMAGE_TEMPLATE, USER_TEXT_TEMPLATE
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


def _call(messages: list[dict], cache_key: str) -> Extraction:
    path = _cache_path(cache_key)
    if settings.cache and path.exists():
        data = json.loads(path.read_text())
        return Extraction(ModelOutput.model_validate(data["output"]), data["output"], data["model"], True,
                          data.get("usage"), 0)

    kwargs = dict(
        model=settings.model,
        messages=messages,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "kanakku_entries", "schema": RESPONSE_SCHEMA}},
    )
    if settings.reasoning_effort:
        kwargs["reasoning_effort"] = settings.reasoning_effort

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
    return _call(messages, f"{settings.model}|{SYSTEM_PROMPT}|{user}")


def extract_image(image_bytes: bytes, mime: str, today: str, hint: str = "") -> Extraction:
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ExtractionError(f"Unsupported image type {mime}. Use JPEG, PNG or WebP.")
    user = USER_IMAGE_TEMPLATE.format(today=today, hint=f"Shopkeeper's note: {hint}" if hint else "")
    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ]
    img_hash = hashlib.sha256(image_bytes).hexdigest()
    return _call(messages, f"{settings.model}|{SYSTEM_PROMPT}|{user}|img:{img_hash}")


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
