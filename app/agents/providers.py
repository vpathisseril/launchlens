"""Provider adapters (Anthropic, OpenAI, Google). Plain HTTPS via httpx — no SDK lock-in (ADR-006).

Keys are passed in per call and never logged.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import get_settings

PROVIDERS = ("anthropic", "openai", "google")
PROVIDER_LABELS = {"anthropic": "Anthropic Claude", "openai": "OpenAI", "google": "Google Gemini"}


class ProviderError(Exception):
    pass


@dataclass
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


def _client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().http_timeout)


def list_models(provider: str, api_key: str) -> list[str]:
    """Zero-cost key validation: listing models succeeds only for a valid key."""
    with _client() as c:
        if provider == "anthropic":
            r = c.get("https://api.anthropic.com/v1/models",
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            key = "data"
        elif provider == "openai":
            r = c.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {api_key}"})
            key = "data"
        elif provider == "google":
            r = c.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": api_key})
            key = "models"
        else:
            raise ProviderError("Unknown provider")
    if r.status_code != 200:
        raise ProviderError(f"Key could not be validated (HTTP {r.status_code}).")
    items = r.json().get(key, [])
    ids = [str(i.get("id") or i.get("name", "")).removeprefix("models/") for i in items]
    return sorted(i for i in ids if i)


def complete(provider: str, model: str, api_key: str, system: str, user_msg: str,
             max_tokens: int = 800, temperature: float = 0.2) -> Completion:
    try:
        with _client() as c:
            if provider == "anthropic":
                return _anthropic(c, model, api_key, system, user_msg, max_tokens, temperature)
            if provider == "openai":
                return _openai(c, model, api_key, system, user_msg, max_tokens)
            if provider == "google":
                return _google(c, model, api_key, system, user_msg, max_tokens, temperature)
    except httpx.HTTPError as exc:
        raise ProviderError(f"Provider request failed: {type(exc).__name__}") from exc
    raise ProviderError("Unknown provider")


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("message", "") if isinstance(msg, dict) else str(msg)
        except ValueError:
            msg = ""
        raise ProviderError(f"Provider returned HTTP {r.status_code}. {msg[:200]}")
    return r.json()


def _anthropic(c: httpx.Client, model: str, key: str, system: str, user_msg: str, max_tokens: int,
               temperature: float) -> Completion:
    body = {
        "model": model, "max_tokens": max_tokens, "temperature": temperature,
        # Long static guardrail prompt is cached by the provider (prompt caching) to cut cost.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user_msg}],
    }
    r = c.post("https://api.anthropic.com/v1/messages", json=body,
               headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    data = _check(r)
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    u = data.get("usage", {})
    tin = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return Completion(text, tin, u.get("output_tokens", 0), model)


def _openai(c: httpx.Client, model: str, key: str, system: str, user_msg: str, max_tokens: int) -> Completion:
    body = {"model": model, "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]}
    r = c.post("https://api.openai.com/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    data = _check(r)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    u = data.get("usage", {})
    return Completion(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), model)


def _google(c: httpx.Client, model: str, key: str, system: str, user_msg: str, max_tokens: int,
            temperature: float) -> Completion:
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}}
    r = c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", json=body,
               headers={"x-goog-api-key": key})
    data = _check(r)
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts", [])
    u = data.get("usageMetadata", {})
    return Completion("".join(p.get("text", "") for p in parts), u.get("promptTokenCount", 0),
                      u.get("candidatesTokenCount", 0), model)
