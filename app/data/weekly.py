"""Weekly data pipeline — one sequential, resource-bounded run (ADR-012, ADR-013).

Steps run in order; a failing step is recorded and the pipeline moves on:
  1. preflight     database reachable, which optional API keys are present
  2. approvals     auto-approve pending sign-ups past their window (cheap; backs up the hourly job)
  3. policy_feed   Federal Register / Regulations.gov → policy items + watchlist alerts
  4. baseline      scope-independent sources (CMS catalog & spending, Census ACS, Census trade)
  5. warm_scopes   every hub view for the starter presets + saved user scopes, one scope at a time
  6. housekeeping  prune caches that would otherwise grow forever
  7. summary       IngestRun row + log line (shown in Admin → Data sources)

Optimal by design:
  * "due" mode re-downloads a cached request only when it is older than its publisher's cadence (REFRESH_DAYS);
    annual CMS files are re-checked every 90 days, while the weekly catalog check picks up a new data year at once.
  * Each request is fetched at most once per run; PubMed/openFDA requests are paced; CMS row caps still apply.
  * Single-flight lock (no overlapping runs) and a wall-clock budget (default 20 min) that stops warming early.
  * Zero AI tokens: the pipeline never calls an LLM (verified by counting usage events before/after).
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.data import connectors as C
from app.data.fetch import REFRESH_DAYS, SourceUnavailable, force_refresh
from app.hubs import HUBS
from app.hubs.policy import refresh_policy
from app.models import DataSource, IngestRun, LlmCache, Notification, SourceCache, UsageEvent, User, utcnow
from app.research.contract import norm_scope
from app.research.scopes import PRESETS
from app.services.accounts import auto_approve_due

log = logging.getLogger("launchlens.weekly")
JOB_KEY = "weekly_refresh"
MANUAL_SOURCES = {"partd_formulary", "negotiation_program"}  # admin-loaded (quarterly / per CMS cycle)
OPTIONAL_SOURCES = {"regulations"}  # needs REGULATIONS_API_KEY
MAX_SCOPES = int(os.getenv("WEEKLY_MAX_SCOPES", "25"))
MAX_RUNTIME_S = int(os.getenv("WEEKLY_MAX_RUNTIME_S", "1200"))
PG_LOCK_ID = 727_001  # pg_try_advisory_lock key for single-flight


# ------------------------------------------------------------------ scopes
def scopes_to_warm(db: Session) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    candidates = [norm_scope(p) for p in PRESETS.values()]
    candidates += [norm_scope(u.scope) for u in db.scalars(select(User).where(User.scope.is_not(None)))]
    for sc in candidates:
        if not sc.get("drugs"):
            continue
        key = json.dumps([sorted(d.lower() for d in sc["drugs"]), sc.get("indication", "").lower(),
                          sorted(sc.get("states") or []), sc.get("places_measure", "")])
        if key not in seen:
            seen.add(key)
            out.append(sc)
    return out[:MAX_SCOPES]


# ------------------------------------------------------------------ steps
class Pipeline:
    def __init__(self, db: Session, mode: str = "due") -> None:
        self.db, self.mode = db, mode
        self.t0 = time.monotonic()
        self.notes: list[str] = []
        self.ok_units = 0
        self.steps: list[tuple[str, str, float]] = []  # (name, status, seconds)
        self.scopes_done = 0

    def over_budget(self) -> bool:
        return time.monotonic() - self.t0 > MAX_RUNTIME_S

    def step(self, name: str, fn: Callable[[], None]) -> None:
        t = time.monotonic()
        status = "ok"
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — record and continue with the next step
            self.db.rollback()
            status = "failed"
            self.notes.append(f"{name}: {type(exc).__name__}: {str(exc)[:160]}")
        self.steps.append((name, status, round(time.monotonic() - t, 1)))
        log.info("step %-12s %-6s %.1fs", name, status, time.monotonic() - t)

    def preflight(self) -> None:
        self.db.execute(text("SELECT 1"))
        from app.core.config import get_settings
        s = get_settings()
        for key, label in (("census_api_key", "CENSUS_API_KEY"), ("openfda_api_key", "OPENFDA_API_KEY"),
                           ("regulations_api_key", "REGULATIONS_API_KEY")):
            if not getattr(s, key):
                self.notes.append(f"preflight: {label} not set (that source may be limited or skipped)")

    def approvals(self) -> None:
        n = auto_approve_due(self.db)
        if n:
            self.notes.append(f"approvals: auto-approved {n}")

    def policy_feed(self) -> None:
        stats = refresh_policy(self.db)
        self.ok_units += 1
        if stats.get("errors"):
            self.notes.append(f"policy_feed: {stats['errors']} query(ies) unavailable")

    def baseline(self) -> None:
        yr = utcnow().year
        tasks: list[tuple[str, Callable[[Session], Any]]] = [
            ("cms_catalog", lambda d: [C.cms_distributions(d, k) for k in C.CMS_TITLES]),
            ("partd_spending", lambda d: C.spending_all(d, "partd_spending")),
            ("partb_spending", lambda d: C.spending_all(d, "partb_spending")),
            ("acs", C.acs_states),
            ("trade", lambda d: C.trade_imports_hs30(d, yr - 1)),
        ]
        for name, fn in tasks:
            try:
                fn(self.db)
                self.ok_units += 1
            except SourceUnavailable as exc:
                self.notes.append(f"baseline/{name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                self.db.rollback()
                self.notes.append(f"baseline/{name}: {type(exc).__name__}: {str(exc)[:120]}")

    def warm_scopes(self) -> None:
        scopes = scopes_to_warm(self.db)
        for sc in scopes:
            if self.over_budget():
                self.notes.append(f"warm_scopes: time budget reached after {self.scopes_done}/{len(scopes)} scopes")
                break
            for hub in HUBS.values():
                for view_id, _title in hub.info.views:
                    try:
                        res = hub.view(self.db, sc, view_id, {})
                        if res.banner and "unavailable right now" in res.banner:
                            self.notes.append(f"{hub.info.key}/{view_id}: {res.banner[:100]}")
                        else:
                            self.ok_units += 1
                    except Exception as exc:  # noqa: BLE001
                        self.db.rollback()
                        self.notes.append(f"{hub.info.key}/{view_id}: {type(exc).__name__}: {str(exc)[:100]}")
            self.scopes_done += 1
            self.db.expunge_all()  # release ORM objects between scopes to keep memory flat

    def housekeeping(self) -> None:
        now = utcnow()
        r1 = self.db.execute(delete(SourceCache).where(SourceCache.retrieved_at < now - timedelta(days=180)))
        r2 = self.db.execute(delete(LlmCache).where(LlmCache.created_at < now - timedelta(days=7)))
        r3 = self.db.execute(delete(Notification).where(Notification.read_at.is_not(None),
                                                        Notification.read_at < now - timedelta(days=90)))
        self.db.commit()
        pruned = (r1.rowcount or 0) + (r2.rowcount or 0) + (r3.rowcount or 0)
        if pruned:
            self.notes.append(f"housekeeping: pruned {pruned} old cache/notification rows")


def _try_lock(db: Session) -> bool:
    if db.get_bind().dialect.name == "postgresql":
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": PG_LOCK_ID}).scalar())
    return runs_in_progress(db) == 0


def _unlock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PG_LOCK_ID})
        db.commit()


def run(db: Session, mode: str = "due") -> IngestRun:
    """Run the full weekly pipeline sequentially. mode='due' (default, optimal) or 'full' (re-download all)."""
    if not _try_lock(db):
        skipped = IngestRun(source_key=JOB_KEY, status="skipped", error="Another weekly run is in progress.",
                            finished_at=utcnow())
        db.add(skipped)
        db.commit()
        return skipped
    job = IngestRun(source_key=JOB_KEY)
    db.add(job)
    db.commit()
    llm_before = db.scalar(select(func.count()).select_from(UsageEvent)) or 0
    p = Pipeline(db, mode)
    try:
        with force_refresh(mode) as fetch_stats:
            p.step("preflight", p.preflight)
            p.step("approvals", p.approvals)
            p.step("policy_feed", p.policy_feed)
            p.step("baseline", p.baseline)
            p.step("warm_scopes", p.warm_scopes)
        p.step("housekeeping", p.housekeeping)
    finally:
        _unlock(db)
    llm_calls = (db.scalar(select(func.count()).select_from(UsageEvent)) or 0) - llm_before
    elapsed = round(time.monotonic() - p.t0)
    data_errors = [n for n in p.notes if not n.startswith(("preflight", "approvals", "housekeeping"))]
    job = db.merge(job)
    job.rows_in, job.rows_out = p.scopes_done, p.ok_units
    job.status = "ok" if not data_errors else ("partial" if p.ok_units else "failed")
    summary = (f"mode={mode} elapsed={elapsed}s scopes={p.scopes_done} units_ok={p.ok_units} "
               f"downloads={fetch_stats['fetched']} reused_cache={fetch_stats['reused']} llm_calls={llm_calls}")
    steps = " | ".join(f"{n}:{s}:{sec}s" for n, s, sec in p.steps)
    job.error = "\n".join([summary, steps, *p.notes[:40]])
    job.finished_at = utcnow()
    db.commit()
    log.info("weekly pipeline: status=%s %s", job.status, summary)
    return job


# ------------------------------------------------------------------ status helpers (admin UI)
def last_run(db: Session) -> IngestRun | None:
    return db.scalar(select(IngestRun).where(IngestRun.source_key == JOB_KEY, IngestRun.status != "skipped")
                     .order_by(IngestRun.started_at.desc()))


def freshness(src: DataSource) -> str:
    """Fresh / Overdue against the source's own refresh cadence (+1 day grace); Manual for admin-loaded sources."""
    if src.key in MANUAL_SOURCES:
        return "Manual"
    if not src.last_refreshed_at:
        return "Not loaded"
    window = REFRESH_DAYS.get(src.key, 7) + 1
    return "Fresh" if src.last_refreshed_at >= utcnow() - timedelta(days=window) else "Overdue"


def overdue_count(db: Session) -> int:
    return sum(1 for s in db.scalars(select(DataSource))
               if s.key not in MANUAL_SOURCES | OPTIONAL_SOURCES and freshness(s) in ("Overdue", "Not loaded"))


def runs_in_progress(db: Session) -> int:
    cutoff = utcnow() - timedelta(hours=3)
    return db.scalar(select(func.count()).select_from(IngestRun).where(
        IngestRun.source_key == JOB_KEY, IngestRun.status == "running", IngestRun.started_at >= cutoff)) or 0
