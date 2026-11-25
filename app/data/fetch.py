"""M5 — polite, cached HTTP fetches with lineage. Every payload returned carries a Citation."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import sha256
from app.models import DataSource, SourceCache, utcnow
from app.research.contract import Citation

log = logging.getLogger("launchlens.fetch")
_force_refresh: ContextVar[bool] = ContextVar("force_refresh", default=False)
_refreshed: ContextVar[set[str] | None] = ContextVar("refreshed", default=None)


@contextmanager
def force_refresh():  # noqa: ANN201
    """Within this block every cached request is re-fetched once (used by the weekly refresh job)."""
    t1, t2 = _force_refresh.set(True), _refreshed.set(set())
    try:
        yield
    finally:
        _force_refresh.reset(t1)
        _refreshed.reset(t2)


class SourceUnavailable(Exception):
    def __init__(self, source_key: str, message: str) -> None:
        super().__init__(message)
        self.source_key = source_key


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if "key" in k.lower() else v) for k, v in params.items()}


def http_get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                  retries: int = 3) -> Any:
    s = get_settings()
    hdrs = {"User-Agent": s.user_agent, "Accept": "application/json"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=s.http_timeout, follow_redirects=True) as c:
                r = c.get(url, params=params, headers=hdrs)
            if r.status_code == 429 or r.status_code >= 500:
                last = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                time.sleep(min(2 ** attempt, 8))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 8))
    raise SourceUnavailable("", f"{type(last).__name__}: {last}")


def mark_source(db: Session, key: str, ok: bool, vintage: str = "", error: str = "") -> None:
    src = db.scalar(select(DataSource).where(DataSource.key == key))
    if not src:
        return
    if ok:
        src.status, src.last_refreshed_at, src.last_error = "ok", utcnow(), ""
        if vintage:
            src.latest_vintage = vintage
    else:
        src.status, src.last_error = "failed", error[:500]


def source_name(db: Session, key: str) -> str:
    src = db.scalar(select(DataSource).where(DataSource.key == key))
    return src.name if src else key


def cached_json(db: Session, source_key: str, url: str, params: dict[str, Any] | None = None, *,
                ttl_hours: int = 24, headers: dict[str, str] | None = None, vintage: str = "",
                locator: str = "", reducer: Callable[[Any], Any] | None = None,
                allow_stale: bool = True) -> tuple[Any, Citation]:
    """Fetch through the source cache. On outage, falls back to the last good payload (stale) with a flag."""
    params = params or {}
    shown = url + ("?" + urlencode(_redact(params), doseq=True) if params else "")
    h = sha256(json.dumps([source_key, url, sorted(params.items(), key=str)], default=str))
    row = db.scalar(select(SourceCache).where(SourceCache.request_hash == h))
    now = utcnow()
    done = _refreshed.get()
    forcing = _force_refresh.get() and done is not None and h not in done
    if row and row.expires_at > now and not forcing:
        return row.payload, _cite(db, source_key, shown, row.retrieved_at, vintage, locator)
    if done is not None:
        done.add(h)
    try:
        payload = http_get_json(url, params, headers)
        if reducer is not None and payload is not None:
            payload = reducer(payload)
    except SourceUnavailable as exc:
        mark_source(db, source_key, False, error=str(exc))
        db.commit()
        if row and allow_stale:
            c = _cite(db, source_key, shown, row.retrieved_at, vintage, locator)
            c.locator = (c.locator + " · STALE (source unavailable)").strip(" ·")
            return row.payload, c
        raise SourceUnavailable(source_key, f"{source_name(db, source_key)} is unavailable right now.") from exc
    if row:
        row.payload, row.retrieved_at, row.expires_at, row.url = payload, now, now + timedelta(hours=ttl_hours), shown
    else:
        db.add(SourceCache(source_key=source_key, request_hash=h, url=shown[:1000], payload=payload,
                           retrieved_at=now, expires_at=now + timedelta(hours=ttl_hours)))
    mark_source(db, source_key, True, vintage)
    db.commit()
    return payload, _cite(db, source_key, shown, now, vintage, locator)


def _cite(db: Session, source_key: str, url: str, retrieved, vintage: str, locator: str) -> Citation:  # noqa: ANN001
    return Citation(source_key=source_key, dataset=source_name(db, source_key), vintage=vintage or "latest",
                    url=url, retrieved_at=Citation.now_iso(retrieved), locator=locator)
