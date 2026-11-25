"""Hub base class + shared data helpers used by multiple hubs."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.data.connectors import _num, cms_drug_rows
from app.data.fetch import SourceUnavailable
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult

PRESCRIBER_COLS = ["Prscrbr_NPI", "Prscrbr_Last_Org_Name", "Prscrbr_First_Name", "Prscrbr_City",
                   "Prscrbr_State_Abrvtn", "Prscrbr_Type", "Gnrc_Name", "Brnd_Name", "Tot_Clms", "Tot_Benes",
                   "Tot_Drug_Cst"]


class Hub:
    info: HubInfo

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        raise NotImplementedError

    def view(self, db: Session, scope: dict[str, Any], view_id: str, params: dict[str, Any]) -> ViewResult:
        fn: Callable[..., ViewResult] | None = getattr(self, f"v_{view_id}", None)
        if fn is None:
            return ViewResult(id=view_id, title="Unknown view", empty="This view does not exist.")
        if not scope.get("drugs") and view_id not in getattr(self, "scope_free_views", ()):
            return ViewResult(id=view_id, title=dict(self.info.views).get(view_id, view_id),
                              empty="Set a research scope first: pick a starter scope or enter the drugs to compare.")
        try:
            return fn(db, scope, params)
        except SourceUnavailable as exc:
            return ViewResult(id=view_id, title=dict(self.info.views).get(view_id, view_id),
                              banner=f"{exc} Showing nothing rather than guessing; try again shortly.")

    def tool(self, db: Session, scope: dict[str, Any], name: str, **kw: Any) -> ToolResult:
        fn: Callable[..., ToolResult] | None = getattr(self, f"t_{name}", None)
        if fn is None:
            return ToolResult(name=name, digest="Unknown tool.")
        try:
            return fn(db, scope, **kw)
        except SourceUnavailable as exc:
            return ToolResult(name=name, digest=f"DATA UNAVAILABLE: {exc}")

    def changes_since(self, db: Session, scope: dict[str, Any], since: datetime) -> list[dict[str, Any]]:
        return []


def prescriber_rows(db: Session, scope: dict[str, Any], year_index: int = 0, cap: int = 20000
                    ) -> tuple[list[dict[str, Any]], list[Citation], bool, str]:
    """Part D prescriber × drug rows for the scope's drugs (optionally filtered to scope states)."""
    rows: list[dict[str, Any]] = []
    cites: list[Citation] = []
    truncated, year = False, ""
    states = scope.get("states") or [""]
    for drug in scope.get("drugs", []):
        for st in states:
            extra = {"Prscrbr_State_Abrvtn": st} if st else None
            r, c, t, year = cms_drug_rows(db, "partd_prescriber_drug", drug, year_index, extra, PRESCRIBER_COLS, cap)
            for x in r:
                x["_drug"] = drug.lower()
            rows.extend(r)
            cites.append(c)
            truncated = truncated or t
    return rows, cites, truncated, year


def claims_by_state(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        st = r.get("Prscrbr_State_Abrvtn")
        if st:
            out[st] += _num(r.get("Tot_Clms")) or 0
    return dict(out)


def claims_by_drug(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["_drug"]] += _num(r.get("Tot_Clms")) or 0
    return dict(out)


def pctile_rank(values: dict[Any, float]) -> dict[Any, float]:
    """0–100 percentile rank (ties share rank)."""
    if not values:
        return {}
    srt = sorted(values.values())
    n = len(srt)
    import bisect
    return {k: 100.0 * bisect.bisect_left(srt, v) / max(n - 1, 1) for k, v in values.items()}
