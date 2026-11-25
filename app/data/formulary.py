"""R1 ingest — CMS Part D formulary public use file (SPUF) → curated, filtered to the drugs users track.

The CMS file is a large ZIP of ZIPs with pipe-delimited text files. We stream it to disk, read only the
'plan information' and 'basic drugs formulary' members, and keep rows for tracked RxCUIs.
Run: python -m app.data.formulary --url <zip url> --drugs apixaban,rivaroxaban
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.data.connectors import related_product_rxcuis, rxcui_for
from app.models import FormularyPlan, FormularyRestriction, IngestRun, utcnow

log = logging.getLogger("launchlens.formulary")


def _iter_members(zf: zipfile.ZipFile, needle: str) -> Iterator[io.TextIOWrapper]:
    for name in zf.namelist():
        low = name.lower()
        if needle not in low:
            continue
        if low.endswith(".zip"):
            with zf.open(name) as inner_bytes:
                inner = zipfile.ZipFile(io.BytesIO(inner_bytes.read()))
                for n2 in inner.namelist():
                    if n2.lower().endswith(".txt"):
                        yield io.TextIOWrapper(inner.open(n2), encoding="latin-1")
        elif low.endswith(".txt"):
            yield io.TextIOWrapper(zf.open(name), encoding="latin-1")


def _yn(v: str | None) -> bool:
    return (v or "").strip().upper() in ("Y", "1", "TRUE")


def tracked_rxcuis(db: Session, drugs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for d in drugs:
        cui, _ = rxcui_for(db, d)
        if not cui:
            continue
        out[cui] = d.lower()
        for r in related_product_rxcuis(db, cui):
            out[r] = d.lower()
    return out


def ingest_zip(db: Session, zip_path: Path, drugs: list[str], vintage: str) -> tuple[int, int]:
    rx = tracked_rxcuis(db, drugs)
    if not rx:
        raise ValueError("None of the drugs could be mapped to RxNorm.")
    zf = zipfile.ZipFile(zip_path)
    db.execute(delete(FormularyPlan).where(FormularyPlan.vintage == vintage))
    db.execute(delete(FormularyRestriction).where(FormularyRestriction.vintage == vintage,
                                                  FormularyRestriction.drug.in_(set(rx.values()))))
    plans_by_formulary: dict[str, list[tuple[str, str, str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    n_plans = 0
    for fh in _iter_members(zf, "plan information"):
        for r in csv.DictReader(fh, delimiter="|"):
            r = {k.strip().upper(): (v or "").strip() for k, v in r.items() if k}
            if _yn(r.get("PLAN_SUPPRESSED_YN")):
                continue
            cid, pid, st, fid = r.get("CONTRACT_ID", ""), r.get("PLAN_ID", ""), r.get("STATE", ""), r.get("FORMULARY_ID", "")
            if not (cid and fid and st):
                continue
            k = (cid, pid, st)
            if k in seen:
                continue
            seen.add(k)
            ptype = "PDP" if cid.startswith("S") else "MA-PD"
            db.add(FormularyPlan(contract_id=cid, plan_id=pid, state=st, plan_type=ptype, formulary_id=fid,
                                 vintage=vintage))
            plans_by_formulary.setdefault(fid, []).append((cid, pid, st, ptype))
            n_plans += 1
    db.flush()
    n_rows = 0
    seen_r: set[tuple[str, str, str, str]] = set()
    for fh in _iter_members(zf, "basic drugs formulary"):
        for r in csv.DictReader(fh, delimiter="|"):
            r = {k.strip().upper(): (v or "").strip() for k, v in r.items() if k}
            cui = r.get("RXCUI", "")
            if cui not in rx:
                continue
            fid = r.get("FORMULARY_ID", "")
            drug = rx[cui]
            tier = int(r["TIER_LEVEL_VALUE"]) if r.get("TIER_LEVEL_VALUE", "").isdigit() else None
            for cid, pid, st, ptype in plans_by_formulary.get(fid, []):
                key = (cid, pid, st, drug)
                if key in seen_r:  # one row per plan × drug (most permissive handled at query time)
                    continue
                seen_r.add(key)
                db.add(FormularyRestriction(contract_id=cid, plan_id=pid, state=st, plan_type=ptype, formulary_id=fid,
                                            drug=drug, rxcui=cui, tier=tier, prior_auth=_yn(r.get("PRIOR_AUTHORIZATION_YN")),
                                            step_therapy=_yn(r.get("STEP_THERAPY_YN")),
                                            quantity_limit=_yn(r.get("QUANTITY_LIMIT_YN")), vintage=vintage))
                n_rows += 1
    db.commit()
    return n_plans, n_rows


def download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, timeout=600, follow_redirects=True,
                      headers={"User-Agent": get_settings().user_agent}) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


def run(db: Session, url: str, drugs: list[str], vintage: str) -> IngestRun:
    run_row = IngestRun(source_key="partd_formulary")
    db.add(run_row)
    db.commit()
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "formulary.zip"
            download(url, p)
            plans, rows = ingest_zip(db, p, drugs, vintage)
        run_row.status, run_row.rows_in, run_row.rows_out = "ok", plans, rows
    except Exception as exc:  # noqa: BLE001 — record any failure on the run
        db.rollback()
        run_row.status, run_row.error = "failed", str(exc)[:1000]
    run_row.finished_at = utcnow()
    db.commit()
    return run_row


def latest_vintage(db: Session) -> str | None:
    return db.scalar(select(FormularyPlan.vintage).order_by(FormularyPlan.retrieved_at.desc()).limit(1))


if __name__ == "__main__":
    from app.core.db import session_factory
    from app.main import init_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=get_settings().formulary_zip_url)
    ap.add_argument("--drugs", required=True, help="comma-separated generic names")
    ap.add_argument("--vintage", required=True, help="e.g. 2026-Q3")
    a = ap.parse_args()
    init_db()
    with session_factory()() as s:
        res = run(s, a.url, [d.strip() for d in a.drugs.split(",") if d.strip()], a.vintage)
        print(res.status, res.rows_in, res.rows_out, res.error)
