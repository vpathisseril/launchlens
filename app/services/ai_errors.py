"""One place for every user-facing AI message (ADR-014). Each message says what happened and exactly how to fix it.

Restrictions (daily calls, token cap, monthly budget, model allowlist) exist ONLY on the shared default key;
personal keys are never limited by LaunchLens — provider-side limits are explained as such.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.agents.providers import PROVIDER_LABELS, ProviderError

ADD_KEY_STEPS = ("Open Settings → AI access, choose a provider (Anthropic, OpenAI or Google), paste your API key and "
                 "click Validate & save. Personal keys have no LaunchLens limits.")


@dataclass
class FriendlyError:
    title: str
    detail: str
    action_label: str
    action_url: str
    level: str = "warn"  # warn | err


def for_quota(message: str) -> FriendlyError:
    return FriendlyError("Shared-key limit reached", f"{message} {ADD_KEY_STEPS}", "Add your own key",
                         "/account/ai", "warn")


def for_no_access(message: str) -> FriendlyError:
    return FriendlyError("AI access isn't set up", f"{message} {ADD_KEY_STEPS}", "Set up AI access",
                         "/account/ai", "warn")


def for_model_choice(message: str) -> FriendlyError:
    return FriendlyError("Pick a model", f"{message} Or click 'Refresh model lists from providers' there.",
                         "Choose a model", "/account/ai#routing", "warn")


def for_provider(exc: ProviderError, provider: str, key_type: str, model: str = "") -> FriendlyError:
    name = PROVIDER_LABELS.get(provider, provider)
    own = key_type == "byok"
    if exc.kind == "auth":
        if own:
            return FriendlyError(f"Your {name} key was rejected",
                                 f"{name} no longer accepts this key (it may have been revoked, rotated or deleted). "
                                 "We've marked it invalid so other keys are used automatically. Create a new key in "
                                 f"your {name} console, then paste it under Settings → AI access → Validate & save.",
                                 "Update your key", "/account/ai", "err")
        return FriendlyError("The shared key isn't working", "The administrator's shared key was rejected by "
                             f"{name}. {ADD_KEY_STEPS}", "Add your own key", "/account/ai", "err")
    if exc.kind == "billing":
        return FriendlyError(f"{name} account needs credits" if own else "Shared key is out of credits",
                             (f"{name} says your account has no remaining credits or quota. Add credits or a payment "
                              f"method in your {name} billing console, or switch to another provider's key under "
                              "Settings → AI access → Preferred provider.") if own else
                             f"The shared key's provider account is out of credits. {ADD_KEY_STEPS}",
                             "Open AI access", "/account/ai", "err")
    if exc.kind == "quota":
        return FriendlyError(f"{name} rate limit", (f"{name} is rate-limiting your account (a provider limit, not a "
                             "LaunchLens limit). Wait a minute and try again, or raise your tier in the "
                             f"{name} console." if own else f"The shared key is busy. Try again shortly. {ADD_KEY_STEPS}"),
                             "Open AI access", "/account/ai", "warn")
    if exc.kind == "model":
        return FriendlyError(f"Model not available: {model}" if model else "Model not available",
                             f"Your {name} key can't use this model. Choose another under Settings → AI access → "
                             "Model routing (Auto picks one for you), or click 'Refresh model lists from providers'.",
                             "Choose a model", "/account/ai#routing", "warn")
    if exc.kind in ("unavailable", "network"):
        return FriendlyError(f"{name} is temporarily unavailable",
                             f"{name} didn't respond. Your settings are fine — try again in a minute. If you have "
                             "another provider's key, set it as Preferred provider under Settings → AI access.",
                             "Open AI access", "/account/ai", "warn")
    return FriendlyError("The AI request failed", f"{name} returned an unexpected error ({exc}). Try again; if it "
                         "persists, check your key and model under Settings → AI access.", "Open AI access",
                         "/account/ai", "err")


KEY_FORMAT = {"anthropic": "Anthropic API keys come from console.anthropic.com → API Keys and start with sk-ant-api03-. "
                           "A Claude.ai Pro/Max subscription or a Claude Code token (sk-ant-oat…) won't work here.",
              "openai": "OpenAI keys come from platform.openai.com → API keys and start with sk- or sk-proj-.",
              "google": "Gemini keys come from aistudio.google.com → Get API key and start with AIza."}


def for_key_validation(exc: ProviderError, provider: str) -> str:
    name = PROVIDER_LABELS.get(provider, provider)
    if exc.kind == "auth":
        why = f"{name} rejected this key ({exc.status or 'unauthorized'})."
    elif exc.kind in ("network", "unavailable"):
        return f"We couldn't reach {name} to check the key. Nothing was stored — please try again in a minute."
    elif exc.kind == "quota":
        why = f"{name} is rate-limiting this account right now."
    else:
        why = f"{name} returned an error ({exc.status})."
    return f"{why} Nothing was stored. {KEY_FORMAT.get(provider, '')} Copy the full key and make sure the provider " \
           "selected above matches the key."
