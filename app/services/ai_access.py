"""M1 — key vault, default-key policy enforcement, model routing, usage metering and LLM response cache."""
from __future__ import annotations

import json
import re
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

# Healthcare & life-sciences profiles (ADR-015). No dedicated medical model is offered through the Anthropic, OpenAI
# or Gemini APIs used here (MedGemma is open-weights / self-hosted only), so domain fit comes from routing pharma
# research to each provider's strongest current model plus healthcare guardrails in every prompt.
PROFILES = {
    "accuracy": {"label": "Healthcare accuracy-first (recommended)",
                 "help": "Flagship models for every research answer and brief: best for clinical, regulatory and "
                         "market-access nuance. Highest cost on your key.",
                 "tiers": {"route": "economy", "answer": "standard", "brief": "premium"},
                 "pick": {"route": "light", "answer": "heavy", "brief": "heavy"}},
    "balanced": {"label": "Balanced",
                 "help": "Fast mid-tier models for answers, flagship for executive briefs.",
                 "tiers": {"route": "economy", "answer": "standard", "brief": "premium"},
                 "pick": {"route": "light", "answer": "light", "brief": "heavy"}},
    "cost": {"label": "Cost-saver",
             "help": "Light models everywhere. Good for exploration; verify important findings.",
             "tiers": {"route": "economy", "answer": "economy", "brief": "standard"},
             "pick": {"route": "light", "answer": "light", "brief": "light"}},
}
DEFAULT_PROFILE = "accuracy"


def profile_of(user: User) -> str:
    p = (user.preferred_models or {}).get("_profile")
    return p if p in PROFILES else DEFAULT_PROFILE


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
    prefs["available_" + provider] = rank_models(provider, models)
    user.preferred_models = prefs
    audit(db, user.id, "byok_saved", "user_api_key", provider, after={"last4": row.key_last4})
    db.commit()
    return models


def rank_models(provider: str, models: list[str], cap: int = 80) -> list[str]:
    """Chat-capable models, stable names first, newest version first (so a cap never drops current models)."""
    chat = [m for m in models if _chat_capable(provider, m)]
    return sorted(chat, key=lambda m: (bool(_SKIP.search(m)), tuple(-x for x in _version(m)), m))[:cap]


def refresh_model_lists(db: Session, user: User) -> dict[str, int]:
    """Re-list models for each valid key (free API call) and store the ranked list."""
    out: dict[str, int] = {}
    prefs = dict(user.preferred_models or {})
    for k in db.scalars(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.is_valid.is_(True))):
        key = decrypt_secret(k.key_ciphertext)
        if not key:
            continue
        try:
            prefs["available_" + k.provider] = rank_models(k.provider, providers.list_models(k.provider, key))
            out[k.provider] = len(prefs["available_" + k.provider])
        except providers.ProviderError:
            out[k.provider] = -1
    user.preferred_models = prefs
    db.commit()
    return out


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


class NeedsModelChoice(NoAiAccess):
    """User has valid keys but no model could be determined — send them to model routing, not to 'add a key'."""


def resolve_access(db: Session, user: User, task: str = "answer", requested_model: str | None = None,
                   requested_provider: str | None = None, max_tokens: int = 900,
                   exclude: frozenset[str] = frozenset()) -> ResolvedAccess:
    """Picks key + model for a call.

    BYOK (unlimited): try the requested provider, then the user's preferred provider, then any other provider with a
    valid key. For each, the model is: requested → the user's choice for this task → the registry default for the
    task's tier → the best match from the models the provider listed when the key was validated. The user is asked
    to act only when nothing usable is configured.
    Shared default key: allowlist, token cap, daily cap and monthly budget.
    """
    keys = {k.provider: k for k in db.scalars(select(UserApiKey).where(UserApiKey.user_id == user.id,
                                                                       UserApiKey.is_valid.is_(True)))}
    order = [p for p in (requested_provider, user.preferred_provider, *sorted(keys)) if p and p not in exclude]
    tried: list[str] = []
    for provider in dict.fromkeys(order):
        row = keys.get(provider)
        if row is None:
            continue
        key = decrypt_secret(row.key_ciphertext)
        if not key:
            continue
        own_model = requested_model if provider == (requested_provider or provider) else None
        model = own_model or model_for(db, user, provider, task)
        if model:
            notice = "" if provider == (requested_provider or user.preferred_provider) else \
                f"Using your {provider} key (no model available for your preferred provider)."
            return ResolvedAccess(provider, model, key, "byok", max_tokens, notice)
        tried.append(provider)
    if tried:
        raise NeedsModelChoice(f"Your {', '.join(tried)} key is valid, but no model is selected. Pick one under "
                               "Settings → AI access → Model routing.")
    return _resolve_default(db, user, requested_model, requested_provider, max_tokens)


def model_for(db: Session, user: User, provider: str, task: str) -> str | None:
    prefs = (user.preferred_models or {}).get(provider, {}) or {}
    prof = PROFILES[profile_of(user)]
    chosen = prefs.get(task) or (prefs.get("answer") if task == "route" else None)
    return chosen or _fallback_model(db, provider, task, prof["tiers"].get(task)) or \
        pick_from_available((user.preferred_models or {}).get("available_" + provider) or [],
                            prof["pick"].get(task, "light")) or prefs.get("answer")


_SKIP = re.compile(r"(\d{4}-\d{2}-\d{2}|-\d{4}$|\d{8}|preview|exp|audio|realtime|search|tts|transcri|embed|image|vision"
                   r"|instruct|codex|oss|moderation|learnlm|gemma|aqa|live|thinking|computer)", re.I)
_LIGHT = ("mini", "flash", "haiku")
_TINY = ("nano", "lite", "8b")


def _version(m: str) -> tuple[float, ...]:
    return tuple(float(x) for x in re.findall(r"\d+(?:\.\d+)?", m)) or (0.0,)


def pick_from_available(models: list[str], task: str) -> str | None:
    """Choose a sensible current model from the provider's own list. `task` is 'light'/'heavy' (or a task name:
    route/answer → light, brief → heavy). Newest stable wins; dated snapshots, previews and non-chat models skipped."""
    task = {"light": "answer", "heavy": "brief"}.get(task, task)
    cands = [m for m in models if not _SKIP.search(m)] or list(models)
    if not cands:
        return None
    def parts(m: str) -> set[str]:  # whole name segments, so "gemini" is not mistaken for "mini"
        return set(re.split(r"[-_.:/ ]", m.lower()))

    light = [m for m in cands if parts(m) & set(_LIGHT) and not parts(m) & set(_TINY)]
    heavy = [m for m in cands if not parts(m) & set(_LIGHT + _TINY)]
    pool = (light or heavy) if task in ("route", "answer") else (heavy or light)
    pool = pool or cands
    return max(pool, key=lambda m: (_version(m), -len(m)))


def effective_models(db: Session, user: User) -> dict[str, dict[str, str]]:
    """What each task will use per provider with the user's keys (shown in Settings)."""
    out: dict[str, dict[str, str]] = {}
    for k in db.scalars(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.is_valid.is_(True))):
        out[k.provider] = {t: (model_for(db, user, k.provider, t) or "") for t in ("answer", "brief")}
    return out


def _resolve_default(db: Session, user: User, requested_model: str | None, requested_provider: str | None,
                     max_tokens: int) -> ResolvedAccess:
    provider = requested_provider or user.preferred_provider or "anthropic"
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


def _fallback_model(db: Session, provider: str, task: str, tier: str | None = None) -> str | None:
    tier = tier or {"route": "economy", "answer": "standard", "brief": "premium"}.get(task, "standard")
    return tier_model(db, provider, tier) or tier_model(db, provider, "standard") or tier_model(db, provider, "economy")


def _has_valid_key(db: Session, user: User) -> bool:
    return db.scalar(select(func.count()).select_from(UserApiKey).where(
        UserApiKey.user_id == user.id, UserApiKey.is_valid.is_(True))) > 0


def _other_valid_key(db: Session, user: User, provider: str) -> bool:
    return db.scalar(select(func.count()).select_from(UserApiKey).where(
        UserApiKey.user_id == user.id, UserApiKey.is_valid.is_(True), UserApiKey.provider != provider)) > 0


def _invalidate_key(db: Session, user: User, provider: str) -> None:
    """A provider rejected the key (401/403): stop using it; ask the user to update it only if nothing else works."""
    row = db.scalar(select(UserApiKey).where(UserApiKey.user_id == user.id, UserApiKey.provider == provider))
    if row:
        row.is_valid = False
        audit(db, user.id, "byok_rejected_by_provider", "user_api_key", provider)
    if not _has_valid_key(db, user) and user.ai_key_status == AiKeyStatus.byok_valid:
        user.ai_key_status = AiKeyStatus.none  # forced onboarding resumes on next page load
    db.commit()


def estimate_cost(db: Session, provider: str, model: str, tin: int, tout: int) -> float:
    p = db.scalar(select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model))
    if not p:
        return 0.0
    return round(tin / 1e6 * p.usd_per_mtok_in + tout / 1e6 * p.usd_per_mtok_out, 6)


def llm_call(db: Session, user: User, system: str, user_msg: str, *, task: str = "answer", hub_key: str = "",
             agent_id: int | None = None, model: str | None = None, provider: str | None = None,
             max_tokens: int = 900, temperature: float = 0.2, use_cache: bool = True, _retry: bool = True,
             _exclude: frozenset[str] = frozenset()) -> tuple[str, UsageEvent, ResolvedAccess]:
    try:
        access = resolve_access(db, user, task, model, provider, max_tokens, exclude=_exclude)
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
    except providers.ProviderError as exc:
        db.add(UsageEvent(user_id=user.id, agent_id=agent_id, hub_key=hub_key, provider=access.provider,
                          model=access.model, key_type=access.key_type, status="error:" + exc.kind,
                          latency_ms=int((time.monotonic() - t0) * 1000)))
        db.commit()
        exc.provider, exc.key_type, exc.model = access.provider, access.key_type, access.model  # type: ignore[attr-defined]
        if access.key_type == "byok" and exc.kind == "auth" and _retry:
            _invalidate_key(db, user, access.provider)
            if _has_valid_key(db, user):  # transparently retry with the user's other key
                return llm_call(db, user, system, user_msg, task=task, hub_key=hub_key, agent_id=agent_id,
                                max_tokens=max_tokens, temperature=temperature, use_cache=use_cache, _retry=False)
        out_of_credits = access.key_type == "byok" and exc.kind in ("billing", "quota") and _retry
        if out_of_credits and _other_valid_key(db, user, access.provider):  # key is fine, account has no credits
            text, ev, alt = llm_call(db, user, system, user_msg, task=task, hub_key=hub_key, agent_id=agent_id,
                                     max_tokens=max_tokens, temperature=temperature, use_cache=use_cache,
                                     _retry=False, _exclude=frozenset({access.provider}))
            alt.notice = f"Your {access.provider} account is out of credits, so your {alt.provider} key was used instead."
            return text, ev, alt
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
