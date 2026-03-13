"""
LLM Client — multi-backend model interface.

Tiers:
  small  → Qwen3.5-35B-A3B   via local Ollama  (192.168.2.25:11434)  [low]
  medium → Gemini Flash        via OpenRouter    (balanced)             [middle]
  large  → Claude Sonnet 4.6   via OpenRouter    (best quality)         [high]
  local  → alias for small (Ollama)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from api.config import settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Tiers routed to OpenRouter
OPENROUTER_TIERS: dict[str, str] = {
    "medium": "google/gemini-3-flash-preview",
    "large":  "anthropic/claude-sonnet-4.6",
}

# Tiers routed to local Ollama (192.168.2.25:11434)
OLLAMA_TIERS = {"small", "local"}

# Combined map for display / fallback lookup
MODEL_TIERS: dict[str, str] = {
    "small":  "qwen3.5:35b-a3b",          # resolved from settings at runtime
    "local":  "qwen3.5:35b-a3b",          # alias for small
    **OPENROUTER_TIERS,
}

RETRY_COUNT = 1
RETRY_BACKOFF_S = 5.0

# ---------------------------------------------------------------------------
# Per-test LLM call log (drained by test runner into raw_output)
# ---------------------------------------------------------------------------
_LLM_LOG: list[dict] = []


def clear_llm_log() -> None:
    _LLM_LOG.clear()


def get_llm_log() -> list[dict]:
    return list(_LLM_LOG)


@dataclass
class LLMResponse:
    content: str
    model: str
    tier: str
    latency_ms: float
    parsed_json: dict | list | None = None
    error: str | None = None
    raw: dict = field(default_factory=dict)


def _extract_json(text: str) -> dict | list | None:
    """Try to parse JSON from model output, with markdown fence fallback.

    Strips <think>...</think> blocks (Qwen/reasoning models) before parsing.
    """
    # Strip reasoning/thinking blocks emitted by models like Qwen3
    clean = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

    for candidate in (clean, text):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", candidate)
        if fence:
            try:
                return json.loads(fence.group(1).strip())
            except json.JSONDecodeError:
                pass
        for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
            m = re.search(pattern, candidate)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
    return None


async def _call_ollama(
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict]:
    """Call local Ollama via native /api/chat endpoint.

    Uses the native endpoint (not OpenAI-compat) so we can pass think=false
    to disable extended-thinking mode on Qwen3 models.
    """
    model = settings.ollama_model
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "think": False,          # disable Qwen3 chain-of-thought tokens
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    content = data["message"]["content"]
    return content, data


async def _call_openrouter(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict]:
    """Call OpenRouter API."""
    api_key = settings.openrouter_api_key
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/GNG-5140-AI-Project-Management",
        "X-Title": "Work Pulse",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return content, data


async def complete(
    messages: list[dict[str, str]],
    tier: str = "small",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    expect_json: bool = True,
) -> LLMResponse:
    """
    Call the LLM for the given tier. Retries once on failure.

    tier="small" / "local" → Ollama at 192.168.2.25:11434  (Qwen3.5-35B, free)
    tier="medium"           → OpenRouter / Gemini Flash      (cheap)
    tier="large"            → OpenRouter / Claude Sonnet 4.6 (best quality)
    """
    is_local = tier in OLLAMA_TIERS
    model = settings.ollama_model if is_local else OPENROUTER_TIERS.get(tier, OPENROUTER_TIERS["large"])

    last_error: str = ""
    for attempt in range(RETRY_COUNT + 1):
        if attempt > 0:
            await asyncio.sleep(RETRY_BACKOFF_S)
        start = time.perf_counter()
        try:
            if is_local:
                content, raw = await _call_ollama(messages, temperature, max_tokens)
            else:
                content, raw = await _call_openrouter(messages, model, temperature, max_tokens)

            latency_ms = (time.perf_counter() - start) * 1000
            parsed = _extract_json(content) if expect_json else None
            logger.debug("LLM ok tier=%s model=%s latency=%.0fms", tier, model, latency_ms)
            resp = LLMResponse(
                content=content,
                model=model,
                tier=tier,
                latency_ms=latency_ms,
                parsed_json=parsed,
                raw=raw,
            )
            _LLM_LOG.append({
                "tier": tier,
                "model": model,
                "latency_ms": round(latency_ms, 1),
                "content": content,
                "parsed_json": parsed,
                "error": None,
            })
            return resp
        except ValueError as e:
            # config error (missing key) — don't retry
            return LLMResponse(content="", model=model, tier=tier,
                               latency_ms=0.0, error=str(e))
        except httpx.HTTPStatusError as e:
            last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.warning("LLM attempt %d failed: %s", attempt + 1, last_error)
        except Exception as e:
            last_error = str(e)
            logger.warning("LLM attempt %d failed: %s", attempt + 1, last_error)

    _LLM_LOG.append({"tier": tier, "model": model, "latency_ms": 0.0,
                     "content": "", "parsed_json": None, "error": last_error})
    return LLMResponse(content="", model=model, tier=tier, latency_ms=0.0, error=last_error)


def complete_sync(
    messages: list[dict[str, str]],
    tier: str = "small",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    expect_json: bool = True,
) -> LLMResponse:
    """Synchronous wrapper around complete() for non-async contexts."""
    return asyncio.run(complete(messages, tier, temperature, max_tokens, expect_json))
