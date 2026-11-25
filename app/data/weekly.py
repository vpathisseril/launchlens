"""Weekly refresh: re-fetch every public source and pre-load the views users rely on (ADR-012).

Runs as a Replit Scheduled Deployment (`python -m scripts.weekly_refresh`) or from Admin → Data sources.
Scopes warmed = the starter presets + every distinct scope users have saved. Each hub view is rendered once
inside `force_refresh()`, which re-fetches every underlying source exactly once and records lineage.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import connectors as C
from app.data.fetch import SourceUnavailable, force_refresh
from app.hubs import HUBS
from app.hubs.policy import refresh_policy
from app.models import DataSource, IngestRun, User, utcnow
from app.research.contract import norm_scope
from app.research.scopes import PRESETS

log = logging.getLogger("launchlens.weekly")
JOB_KEY = "weekly_refresh"
STALE_AFTER_DAYS = 8  # one weekly cycle + a day of grace
MANUAL_SOURCES = {"partd_formulary", "negotiation_program"}  # loaded by admins (quarterly / per CMS cycle)
MAX_SCOPES = 25


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


def run(db: Session) -> IngestRun:
    job = IngestRun(source_key=JOB_KEY)
    db.add(job)
    db.commit()
    t0 = time.monotonic()
    views_ok, errors = 0, []
    with force_refresh():
        try:
            stats = refresh_policy(db)
            views_ok += 1
            log.info("policy feed: %s", stats)
        except Exception as exc:  # noqa: BLE001 — one failing source must not stop the job
            errors.append(f"policy: {exc}")
        for name, fn in _baseline_tasks():
            try:
                fn(db)
                views_ok += 1
            except SourceUnavailable as exc:
                errors.append(f"{name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
        scopes = scopes_to_warm(db)
        for sc in scopes:
            for hub in HUBS.values():
                for view_id, _title in hub.info.views:
                    try:
                        res = hub.view(db, sc, view_id, {})
                        if res.banner and "unavailable right now" in res.banner:
                            errors.append(f"{hub.info.key}/{view_id}: {res.banner[:120]}")
                        else:
                            views_ok += 1
                    except SourceUnavailable as exc:
                        errors.append(f"{hub.info.key}/{view_id}: {exc}")
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        errors.append(f"{hub.info.key}/{view_id}: {type(exc).__name__}: {str(exc)[:120]}")
    job.rows_in, job.rows_out = len(scopes), views_ok
    job.status = "ok" if not errors else ("partial" if views_ok else "failed")
    job.error = "\n".join(errors[:40])
    job.finished_at = utcnow()
    db.commit()
    log.info("weekly refresh: %s scopes, %s views/feeds ok, %s errors, %.0fs", len(scopes), views_ok, len(errors),
             time.monotonic() - t0)
    return job


def _baseline_tasks() -> list[tuple[str, Any]]:
    """Scope-independent sources, refreshed even if no view happens to need them this week."""
    yr = utcnow().year
    return [
        ("cms_catalog", lambda d: [C.cms_distributions(d, k) for k in C.CMS_TITLES]),
        ("partd_spending", lambda d: C.spending_all(d, "partd_spending")),
        ("partb_spending", lambda d: C.spending_all(d, "partb_spending")),
        ("acs", C.acs_states),
        ("trade", lambda d: C.trade_imports_hs30(d, yr - 1)),
    ]


def last_run(db: Session) -> IngestRun | None:
    return db.scalar(select(IngestRun).where(IngestRun.source_key == JOB_KEY).order_by(IngestRun.started_at.desc()))


def freshness(src: DataSource) -> str:
    """Fresh / Overdue for automated sources; Manual for admin-loaded ones."""
    if src.key in MANUAL_SOURCES:
        return "Manual"
    if not src.last_refreshed_at:
        return "Not loaded"
    return "Fresh" if src.last_refreshed_at >= utcnow() - timedelta(days=STALE_AFTER_DAYS) else "Overdue"


def overdue_count(db: Session) -> int:
    return sum(1 for s in db.scalars(select(DataSource)) if freshness(s) in ("Overdue", "Not loaded")
               and s.key not in MANUAL_SOURCES and s.key != "regulations")


def runs_in_progress(db: Session) -> int:
    cutoff = utcnow() - timedelta(hours=3)
    return db.scalar(select(func.count()).select_from(IngestRun).where(
        IngestRun.source_key == JOB_KEY, IngestRun.status == "running", IngestRun.started_at >= cutoff)) or 0
