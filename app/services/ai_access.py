"""M1 — key vault, default-key policy enforcement, model routing, usage metering and LLM response cache."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import providers
from app.core.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret, sha256
from app.models import (
    AiKeyStatus,
    DefaultKeyPolicy,
    LlmCache,
    ModelPrice,
    UsageEvent,
    User,
    UserApiKey,
    utcnow,
)
from app.services.audit import audit

TASKS = ("route", "answer", "brief")  # economy → standard → premium


class QuotaExceeded(Exception):
    pass


class NoAiAccess(Exception):
    pass


@dataclass
class ResolvedAccess:
    provider: str
    model: str
    api_key: str
    key_type: str  # byok | default
    max_tokens: int
    notice: str = ""


def default_key_for(provider: str) -> str:
    s = get_settings()
    return {"anthropic": s.default_anthropic_key, "openai": s.default_openai_key,
            "google": s.default_google_key}.get(provider, "")


def default_providers_available(db: Session) -> list[DefaultKeyPolicy]:
    pols = db.scalars(select(DefaultKeyPolicy).where(DefaultKeyPolicy.is_enabled.is_(True))).all()
    return [p for p in pols if default_key_for(p.provider) and p.model_allowlist]


def save_byok(db: Session, user: User, provider: str, raw_key: str, label: str = "") -> list[str]:
    """Validates by listing models (no token cost). Raises ProviderError when invalid; nothing is stored then."""
    raw_key = raw_key.strip()
    models = providers.list_models(provider, raw_key)
    row = db.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.provider == provider))
    if not row:
        row = UserApiKey(user_id=user.id, provider=provider, key_ciphertext="", key_last4="")
        db.add(row)
    row.key_ciphertext = encrypt_secret(raw_key)
    row.key_last4 = raw_key[-4:]
    row.label = label[:80]
    row.is_valid = True
    row.validated_at = utcnow()
    user.ai_key_status = AiKeyStatus.byok_valid
    user.preferred_provider = provider
    prefs = dict(user.preferred_models or {})
    prefs.setdefault(provider, {})
    prefs["available_" + provider] = [m for m in models if _chat_capable(provider, m)][:60]
    user.preferred_models = prefs
    audit(db, user.id, "byok_saved", "user_api_key", provider, after={"last4": row.key_last4})
    db.commit()
    return models


def _chat_capable(provider: str, model: str) -> bool:
    m = model.lower()
    if provider == "openai":
        return m.startswith(("gpt", "o1", "o3", "o4", "o5", "chatgpt")) and "audio" not in m and "realtime" not in m \
            and "image" not in m and "tts" not in m and "transcribe" not in m and "search" not in m
    if provider == "google":
        return m.startswith("gemini") and "embedding" not in m and "image" not in m and "tts" not in m
    return True


def accept_default(db: Session, user: User) -> None:
    user.ai_key_status = AiKeyStatus.default_accepted
    audit(db, user.id, "default_key_accepted", "user", user.id)
    db.commit()


def delete_byok(db: Session, user: User, provider: str) -> None:
    row = db.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.provider == provider))
    if row:
        db.delete(row)
    remaining = db.scalar(select(func.count()).select_from(UserApiKey)
                          .where(UserApiKey.user_id == user.id, UserApiKey.provider != provider)) or 0
    if remaining == 0:
        user.ai_key_status = AiKeyStatus.none
    audit(db, user.id, "byok_deleted", "user_api_key", provider)
    db.commit()


def calls_today(db: Session, user: User) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.user_id == user.id, UsageEvent.key_type == "default", UsageEvent.at >= start,
        UsageEvent.status.in_(["ok", "error", "cache_hit"]))) or 0


def default_spend_month(db: Session, provider: str) -> float:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(db.scalar(select(func.coalesce(func.sum(UsageEvent.est_cost_usd), 0.0)).where(
        UsageEvent.key_type == "default", UsageEvent.provider == provider, UsageEvent.at >= start)) or 0.0)


def tier_model(db: Session, provider: str, tier: str, allow: list[str] | None = None) -> str | None:
    q = select(ModelPrice.model).where(ModelPrice.provider == provider, ModelPrice.tier == tier)
    for m in db.scalars(q):
        if allow is None or m in allow:
            return m
    return None


def resolve_access(db: Session, user: User, task: str = "answer", requested_model: str | None = None,
                   requested_provider: str | None = None, max_tokens: int = 900) -> ResolvedAccess:
    """Picks key + model for a call. BYOK: unlimited, user's choice. Default: allowlist, token cap, daily cap."""
    provider = requested_provider or user.preferred_provider or "anthropic"
    byok = db.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.provider == provider,
                                              UserApiKey.is_valid.is_(True)))
    if byok is None and requested_provider is None:
        byok = db.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.is_valid.is_(True)))
        if byok:
            provider = byok.provider
    if byok:
        key = decrypt_secret(byok.key_ciphertext)
        if key:
            prefs = (user.preferred_models or {}).get(provider, {})
            model = requested_model or prefs.get(task) or prefs.get("answer") or _fallback_model(db, provider, task)
            if not model:
                raise NoAiAccess("Choose a model for this provider in Settings → AI Access.")
            return ResolvedAccess(provider, model, key, "byok", max_tokens)

    if user.ai_key_status != AiKeyStatus.default_accepted:
        raise NoAiAccess("Add your own API key or accept the shared default key in Settings → AI Access.")
    pols = default_providers_available(db)
    pol = next((p for p in pols if p.provider == provider), pols[0] if pols else None)
    if pol is None:
        raise NoAiAccess("The shared default key is not configured. Add your own API key to continue.")
    if calls_today(db, user) >= pol.calls_per_day:
        raise QuotaExceeded(f"You've used all {pol.calls_per_day} shared-key calls for today. "
                            "Add your own API key for unlimited use.")
    if default_spend_month(db, pol.provider) >= pol.monthly_budget_usd > 0:
        raise QuotaExceeded("The shared default key reached its monthly budget. Add your own API key to continue.")
    notice = ""
    model = requested_model
    if not model or model not in pol.model_allowlist:
        econ = tier_model(db, pol.provider, "economy", pol.model_allowlist)
        if model and model not in pol.model_allowlist:
            notice = f"Shared key: switched to {econ or pol.model_allowlist[0]} (allowlisted model)."
        model = econ or pol.model_allowlist[0]
    return ResolvedAccess(pol.provider, model, default_key_for(pol.provider), "default",
                          min(max_tokens, pol.max_tokens_per_call), notice)


def _fallback_model(db: Session, provider: str, task: str) -> str | None:
    tier = {"route": "economy", "answer": "standard", "brief": "premium"}.get(task, "standard")
    return tier_model(db, provider, tier) or tier_model(db, provider, "standard") or tier_model(db, provider, "economy")


def estimate_cost(db: Session, provider: str, model: str, tin: int, tout: int) -> float:
    p = db.scalar(select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model))
    if not p:
        return 0.0
    return round(tin / 1e6 * p.usd_per_mtok_in + tout / 1e6 * p.usd_per_mtok_out, 6)


def llm_call(db: Session, user: User, system: str, user_msg: str, *, task: str = "answer", hub_key: str = "",
             agent_id: int | None = None, model: str | None = None, provider: str | None = None,
             max_tokens: int = 900, temperature: float = 0.2, use_cache: bool = True
             ) -> tuple[str, UsageEvent, ResolvedAccess]:
    try:
        access = resolve_access(db, user, task, model, provider, max_tokens)
    except QuotaExceeded:
        db.add(UsageEvent(user_id=user.id, agent_id=agent_id, hub_key=hub_key, provider=provider or "-", model="-",
                          key_type="default", status="quota"))
        db.commit()
        raise
    h = sha256(json.dumps([access.provider, access.model, system, user_msg, access.max_tokens], sort_keys=True))
    cached = db.scalar(select(LlmCache).where(LlmCache.request_hash == h)) if use_cache else None
    if cached and cached.created_at > utcnow() - timedelta(hours=24):
        ev = UsageEvent(user_id=user.id, agent_id=agent_id, hub_key=hub_key, provider=access.provider,
                        model=access.model, key_type=access.key_type, status="cache_hit")
        db.add(ev)
        db.commit()
        return cached.response, ev, access
    t0 = time.monotonic()
    status = "ok"
    try:
        comp = providers.complete(access.provider, access.model, access.api_key, system, user_msg,
                                  access.max_tokens, temperature)
    except providers.ProviderError:
        status = "error"
        db.add(UsageEvent(user_id=user.id, agent_id=agent_id, hub_key=hub_key, provider=access.provider,
                          model=access.model, key_type=access.key_type, status=status,
                          latency_ms=int((time.monotonic() - t0) * 1000)))
        db.commit()
        raise
    ev = UsageEvent(user_id=user.id, agent_id=agent_id, hub_key=hub_key, provider=access.provider, model=access.model,
                    key_type=access.key_type, tokens_in=comp.tokens_in, tokens_out=comp.tokens_out,
                    est_cost_usd=estimate_cost(db, access.provider, access.model, comp.tokens_in, comp.tokens_out),
                    latency_ms=int((time.monotonic() - t0) * 1000), status=status)
    db.add(ev)
    if use_cache and comp.text:
        if cached:
            cached.response, cached.created_at = comp.text, utcnow()
        else:
            db.add(LlmCache(request_hash=h, response=comp.text))
    db.commit()
    return comp.text, ev, access
