"""Single source of truth for everything LaunchLens says about itself (ADR-016).

Static wording lives here once; live facts (approval window, shared-key limits, data sources and their refresh
cadence, AI profiles, hubs) are read from the database/registries at request time, so the About page, landing page,
footer, sign-up terms, onboarding and briefs can never disagree.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

NAME = "LaunchLens"
TAGLINE = "Research the five forces that decide a drug launch."
SUMMARY = ("LaunchLens is a launch-intelligence research workbench for US life-sciences teams. It brings public CMS, "
           "FDA, NIH, CDC, Census and Federal Register data together with citation-checked AI research agents across "
           "five hubs: market access, pricing policy, policy radar, targeting and differentiation.")
AUTHOR = "Varghese Pathisseril"
SIGNATURE = "By Varghese Pathisseril · VP+AI"
VERSION = "1.2"
DISCLAIMER = ("Research prototype — not promotional material, not legal, financial, or medical advice. Public data "
              "only; Medicare data is a proxy for the total US market.")
TERMS = ("I understand LaunchLens is a research prototype using public data only. It is not promotional material and "
         "not legal, financial, or medical advice.")

PRINCIPLES = [
    ("badge-check", "No made-up numbers",
     "Every AI answer passes a citation checker: each number must appear in the source data and carry a [n] "
     "reference, or the answer is flagged."),
    ("lock-keyhole", "Your keys, your control",
     "Bring your own Anthropic, OpenAI or Gemini key (encrypted at rest, only the last 4 characters ever shown) for "
     "unlimited use, or try the limited shared key."),
    ("scale", "Neutral on policy",
     "Policy items show official status and dates from the Federal Register and agency sources. Proposals are never "
     "presented as law."),
    ("stethoscope", "Healthcare-grade guardrails",
     "Exact regulatory terms, labeled indication vs. trial endpoint, no efficacy or safety inference beyond the "
     "source, no patient-level data, no medical advice."),
]

RESEARCH_LOOP = [("Ask", "message-circle-question"), ("Explore", "database"), ("Analyze", "chart-no-axes-combined"),
                 ("Synthesize", "sparkles"), ("Pin", "pin"), ("Brief", "file-text")]

LIMITATIONS = [
    "Commercial claims are not public, so Medicare Part D/B data is used as a transparent proxy.",
    "CMS omits prescriber-drug rows with fewer than 11 claims; results cover only prescribers above that threshold.",
    "The negotiation-candidate score is an estimate, not a CMS determination.",
    "No dedicated medical model is available through the Anthropic, OpenAI or Gemini APIs used here; healthcare fit "
    "comes from routing to each provider's strongest model plus domain guardrails.",
    "AI output can be wrong; verify findings against the linked sources before acting on them.",
]


def _approval_text(db: Session) -> str:
    from app.services.accounts import approval_mode, approval_window_hours
    mode, hrs = approval_mode(db), approval_window_hours(db)
    return {"MANUAL": "An administrator reviews every access request.",
            "AUTO_IMMEDIATE": "Access is granted immediately after sign-up.",
            "AUTO_AFTER_24H": f"An administrator reviews requests within {hrs} hours; if no one acts by then, access "
                              "is granted automatically."}.get(mode, "")


def _ai_facts(db: Session) -> dict[str, Any]:
    from app.agents.providers import PROVIDER_LABELS
    from app.services.ai_access import DEFAULT_PROFILE, PROFILES, default_providers_available
    shared = [{"provider": PROVIDER_LABELS.get(p.provider, p.provider), "models": ", ".join(p.model_allowlist),
               "max_tokens": p.max_tokens_per_call, "calls_per_day": p.calls_per_day}
              for p in default_providers_available(db)]
    return {"shared": shared, "profiles": PROFILES, "default_profile": DEFAULT_PROFILE,
            "providers": list(PROVIDER_LABELS.values())}


def _sources(db: Session) -> list[dict[str, Any]]:
    from app.data.fetch import REFRESH_DAYS
    from app.data.weekly import MANUAL_SOURCES, freshness
    from app.models import DataSource
    out = []
    for s in db.scalars(select(DataSource).order_by(DataSource.hub_key, DataSource.name)):
        if s.key == "cms_catalog":
            continue
        out.append({"name": s.name, "publisher": s.publisher, "hub": s.hub_key, "url": s.url,
                    "publisher_cadence": s.refresh_cadence,
                    "refresh": "Admin-loaded" if s.key in MANUAL_SOURCES else f"Every {REFRESH_DAYS.get(s.key, 7)} days",
                    "freshness": freshness(s), "vintage": s.latest_vintage})
    return out


def build(db: Session) -> dict[str, Any]:
    from app.hubs import HUBS
    return {"name": NAME, "tagline": TAGLINE, "summary": SUMMARY, "author": AUTHOR, "signature": SIGNATURE,
            "version": VERSION, "disclaimer": DISCLAIMER, "principles": PRINCIPLES, "loop": RESEARCH_LOOP,
            "limitations": LIMITATIONS, "approval": _approval_text(db), "ai": _ai_facts(db),
            "hubs": sorted(HUBS.values(), key=lambda h: h.info.rank), "sources": _sources(db)}
