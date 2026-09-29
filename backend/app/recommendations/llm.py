"""LLM provider layer for intent parsing.

The LLM is confined to one job: turn a query into a JSON object that validates against a Pydantic
schema. This module knows nothing about courses, rules or students. Providers:

  gemini     Google Gemini via the `google-genai` SDK (has a free tier)   GEMINI_API_KEY / GOOGLE_API_KEY
  anthropic  Claude via the `anthropic` SDK                               ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
  none       force the deterministic rule-based parser

Selection: LLM_PROVIDER=gemini|anthropic|none, otherwise automatic (Gemini if a Gemini key is set,
else Anthropic). LLM_MODEL overrides the provider's default model; LLM_TIMEOUT_S sets the timeout.
Any failure (missing SDK, auth, quota/429, timeout, blocked or malformed output) returns None and
the caller falls back to the rule-based parser. API keys are never logged.
"""
from __future__ import annotations

import logging
import os
from typing import Type, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Gemini: a comma-separated chain, tried in order when a model is out of quota (429), overloaded (5xx)
# or retired for this key (404). Model names change often; "gemini-flash-latest" is Google's alias.
DEFAULT_MODELS = {"gemini": "gemini-3-flash-preview,gemini-flash-latest", "anthropic": "claude-opus-5"}


def _gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def _anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or None


def provider() -> str | None:
    """Configured provider name, or None when the LLM path is unavailable/disabled."""
    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if forced == "none":
        return None
    if forced == "gemini":
        return "gemini" if _gemini_key() else None
    if forced == "anthropic":
        return "anthropic" if _anthropic_key() else None
    if _gemini_key():
        return "gemini"
    if _anthropic_key():
        return "anthropic"
    return None


def model_name(prov: str | None = None) -> str | None:
    prov = prov or provider()
    return (os.environ.get("LLM_MODEL") or DEFAULT_MODELS.get(prov or "")) if prov else None


def model_chain(prov: str | None = None) -> list[str]:
    return [m.strip() for m in (model_name(prov) or "").split(",") if m.strip()]


def call_json(system: str, user: str, schema_cls: Type[T]) -> tuple[T | None, str | None]:
    """Return (validated instance or None, provider name). Never raises."""
    prov = provider()
    if prov is None:
        return None, None
    try:
        fn = _gemini if prov == "gemini" else _anthropic
        return fn(system, user, schema_cls), prov
    except Exception as exc:  # network, auth, quota, schema: caller falls back to rules
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        log.warning("LLM (%s) intent parsing failed, using deterministic parser: %s%s", prov, type(exc).__name__,
                    f" (HTTP {code})" if code else "")
        return None, prov


def _timeout() -> float:
    try:
        return float(os.environ.get("LLM_TIMEOUT_S", "20"))
    except ValueError:
        return 20.0


# ---------------------------------------------------------------------------- Gemini
_MOVE_ON = {404, 429, 500, 502, 503, 504}  # this model can't serve us right now: try the next one
_TRANSIENT = {500, 502, 503, 504}           # overloaded/hiccup: retry the same model once first
RETRY_DELAY_S = 1.5


def _gemini(system: str, user: str, schema_cls: Type[T]) -> T | None:
    import json
    import time

    from google import genai
    from google.genai import errors, types

    timeout = _timeout()
    client = genai.Client(api_key=_gemini_key(), http_options=types.HttpOptions(timeout=int(timeout * 1000)))
    schema = schema_cls.model_json_schema()
    base = dict(temperature=0, max_output_tokens=8192, response_mime_type="application/json")
    # Progressively simpler requests, used only when the server answers 400 (unsupported option):
    #   1. schema + low thinking (fast, ~1.5 s)   2. schema only   3. schema described in the prompt
    variants = [
        (dict(base, response_json_schema=schema, thinking_config=types.ThinkingConfig(thinking_level="low")), system),
        (dict(base, response_json_schema=schema), system),
        (base, system + "\n\nReturn ONLY a JSON object that conforms to this JSON Schema:\n" + json.dumps(schema)),
    ]
    started, last_exc = time.monotonic(), None
    for model in model_chain("gemini"):
        if time.monotonic() - started > timeout:  # overall budget, not per model
            break
        retried = False
        vi = 0
        while vi < len(variants):
            cfg, sys_prompt = variants[vi]
            try:
                resp = client.models.generate_content(
                    model=model, contents=user, config=types.GenerateContentConfig(system_instruction=sys_prompt, **cfg))
            except errors.APIError as exc:
                last_exc = exc
                code = getattr(exc, "code", None)
                if code == 400:
                    vi += 1  # simplify the request
                    continue
                if code in _TRANSIENT and not retried and time.monotonic() - started < timeout / 2:
                    retried = True
                    time.sleep(RETRY_DELAY_S)
                    continue  # same model, same request, once
                if code in _MOVE_ON:
                    log.info("Gemini model %s unavailable (HTTP %s); trying next", model, code)
                    break
                raise  # 401/403 auth etc.: no point trying other models
            text = getattr(resp, "text", None)
            if not text:  # blocked, empty or truncated
                log.warning("Gemini %s returned no text", model)
                return None
            return schema_cls.model_validate_json(text)
    if last_exc is not None:
        raise last_exc
    return None


# ---------------------------------------------------------------------------- Anthropic
def _anthropic(system: str, user: str, schema_cls: Type[T]) -> T | None:
    import anthropic

    client = anthropic.Anthropic(timeout=_timeout(), max_retries=1)
    resp = client.messages.parse(
        model=model_name("anthropic"),
        max_tokens=2000,
        system=system,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": user}],
        output_format=schema_cls,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.warning("Claude returned no usable output (stop_reason=%s)", resp.stop_reason)
        return None
    return schema_cls.model_validate(resp.parsed_output.model_dump())
