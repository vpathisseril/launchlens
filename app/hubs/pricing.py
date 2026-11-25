"""R2 — Drug Pricing Policy hub: IRA Medicare negotiation + MFN (rank #2)."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.connectors import _num, drugsfda_for_brand, first_approval, spend_years, spending_all
from app.data.fetch import SourceUnavailable, source_name
from app.hubs.base import Hub, pctile_rank
from app.models import NegotiationDrug, PolicyItem
from app.research import charts
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult


def _ncite(db: Session, n: NegotiationDrug) -> Citation:
    return Citation(n.source_key, source_name(db, n.source_key), n.vintage, n.source_url,
                    Citation.now_iso(n.retrieved_at), f"IPAY {n.ipay} · {n.drug}")


def _matches(generic_or_name: str, scope_drugs: list[str]) -> bool:
    g = generic_or_name.lower()
    return any(d.lower() in g or g in d.lower() for d in scope_drugs if d)


def _brand_key(name: str) -> str:
    return name.lower().replace("®", "").split(";")[0].split("/")[0].split(" ")[0].strip()


def class_spend(db: Session, drugs: list[str]) -> tuple[dict[str, dict[int, float]], list[Citation], str]:
    """Total Medicare spend by year (Part D + Part B 'Overall' rows) for scope drugs, keyed by generic."""
    out: dict[str, dict[int, float]] = {}
    cites: list[Citation] = []
    year = ""
    for key in ("partd_spending", "partb_spending"):
        try:
            rows, cite, yr = spending_all(db, key)
        except SourceUnavailable:
            continue
        cites.append(cite)
        year = year or yr
        for r in rows:
            g = str(r.get("Gnrc_Name", "")).strip()
            if not g or not _matches(g, drugs):
                continue
            series = spend_years(r)
            acc = out.setdefault(g.lower(), {})
            for y, v in series.items():
                acc[y] = acc.get(y, 0.0) + v
    return out, cites, year


class PricingHub(Hub):
    info = HubInfo(
        key="pricing", title="Drug Pricing Policy · IRA + MFN", short="Pricing", icon="badge-dollar-sign", rank=2,
        question="Which drugs in this market face Medicare negotiated prices or MFN agreements — and who is next?",
        views=[("tracker", "Negotiation Tracker"), ("candidates", "Candidate Radar"), ("exposure", "Price Exposure"),
               ("class_impact", "Class Impact"), ("mfn", "MFN Tracker")],
        tools={"selected_drugs": "Drugs selected for Medicare negotiation by payment year with negotiated prices",
               "candidates": "Estimated next-cycle negotiation candidates with transparent rule inputs",
               "class_exposure": "Share of the class's Medicare spend under negotiated prices",
               "mfn_items": "Official MFN pricing items with status and date"},
    )
    scope_free_views = ("tracker", "candidates", "mfn")

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        sel = db.scalars(select(NegotiationDrug)).all()
        if not scope.get("drugs"):
            return [Kpi("Drugs with Medicare negotiation", f"{len(sel)}", float(len(sel)), hint="All IPAYs")]
        hit = [n for n in sel if _matches(n.generic or n.drug, scope["drugs"])]
        k = Kpi("Scope drugs selected for negotiation", f"{len({n.drug for n in hit})} of {len(scope['drugs'])}",
                float(len(hit)), citation=_ncite(db, hit[0]) if hit else None,
                hint=", ".join(sorted({f"{n.drug} ({n.ipay})" for n in hit}))[:120])
        return [k]

    def v_tracker(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        sel = db.scalars(select(NegotiationDrug).order_by(NegotiationDrug.ipay, NegotiationDrug.medicare_spend_usd.desc()
                                                          )).all()
        if not sel:
            return ViewResult("tracker", "Negotiation Tracker", empty="Negotiation data not loaded (run seed).")
        ipays = sorted({n.ipay for n in sel})
        counts = [sum(1 for n in sel if n.ipay == y) for y in ipays]
        chart = charts.bar([f"IPAY {y}" for y in ipays], [("Drugs selected", counts)])
        drugs = scope.get("drugs") or []
        rows = [[("★ " if drugs and _matches(n.generic or n.drug, drugs) else "") + n.drug, n.generic, n.ipay,
                 f"Part {n.part}", n.selection_type, charts.fmt_usd(n.medicare_spend_usd),
                 charts.fmt_usd(n.list_price_30d, 0) if n.list_price_30d else "—",
                 f"${n.mfp_30d:,.2f}" if n.mfp_30d else "—", charts.fmt_pct(n.discount_pct, 0), n.status]
                for n in sel]
        cites = {n.source_url: _ncite(db, n) for n in sel}
        return ViewResult("tracker", "Negotiation Tracker",
                          "★ = in your scope. MFP = negotiated 'maximum fair price' per 30-day supply as published "
                          "by CMS; discount is vs. the list price CMS cites.", chart,
                          charts.table(["Drug", "Generic", "IPAY", "Part", "Type", "Medicare spend (CMS)",
                                        "List / 30d", "MFP / 30d", "Discount", "Status"], rows, [5, 6, 7, 8]),
                          list(cites.values()))

    def _candidates(self, db: Session) -> tuple[list[dict[str, Any]], list[Citation]]:
        rows, cite, _ = spending_all(db, "partd_spending")
        cites = [cite]
        selected = {_brand_key(n.drug) for n in db.scalars(select(NegotiationDrug))}
        cands = []
        for r in rows:
            b, g = str(r.get("Brnd_Name", "")).strip(), str(r.get("Gnrc_Name", "")).strip()
            if not b or b.lower() == g.lower() or _brand_key(b) in selected:
                continue
            sy = spend_years(r)
            if not sy:
                continue
            latest_y = max(sy)
            cands.append({"brand": b, "generic": g, "spend": sy[latest_y], "year": latest_y,
                          "mftrs": _num(r.get("Tot_Mftr")) or 0})
        cands.sort(key=lambda c: c["spend"], reverse=True)
        cands = cands[:25]
        pct = pctile_rank({i: c["spend"] for i, c in enumerate(cands)})
        sel_date = date(date.today().year + 1, 2, 1)
        for i, c in enumerate(cands):
            c["spend_pctile"] = pct.get(i, 0)
            c["approved"], c["app_type"], c["years"] = None, "", None
            if i < 15:
                try:
                    apps, fcite = drugsfda_for_brand(db, c["brand"])
                    d, kind = first_approval(apps)
                    if d:
                        ad = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
                        c["approved"], c["app_type"] = ad.isoformat(), kind
                        c["years"] = round((sel_date - ad).days / 365.25, 1)
                    if i < 3:
                        cites.append(fcite)
                except SourceUnavailable:
                    pass
            thresh = 11 if c["app_type"] == "BLA" else 7
            c["age_ok"] = c["years"] is not None and c["years"] >= thresh
            c["single"] = c["mftrs"] <= 1
            c["score"] = round(0.45 * c["spend_pctile"] + 40 * c["age_ok"] + 15 * c["single"], 1)
        cands.sort(key=lambda c: c["score"], reverse=True)
        return cands, cites

    def v_candidates(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        cands, cites = self._candidates(db)
        drugs = scope.get("drugs") or []
        pts = [{"name": c["brand"], "x": c["years"] or 0, "y": round(c["spend"] / 1e9, 2)} for c in cands if c["years"]]
        rows = [[("★ " if drugs and _matches(c["generic"], drugs) else "") + c["brand"], c["generic"],
                 charts.fmt_usd(c["spend"]), c["approved"] or "not checked", c["app_type"] or "—",
                 c["years"] if c["years"] is not None else "—", "yes" if c["age_ok"] else "no",
                 "yes" if c["single"] else "no", c["score"]] for c in cands]
        return ViewResult("candidates", "Candidate Radar",
                          "Top Part D spenders not yet selected, scored by transparent rules: 45% spend percentile, "
                          "40% meets years-since-approval threshold (7 NDA / 11 BLA at next Feb 1), 15% single "
                          "manufacturer.",
                          charts.quadrant(pts, "Years since first approval", "Part D spend ($B)", 7, 1,
                                          ("Likely", "", "", "Approaching")) if pts else None,
                          charts.table(["Brand", "Generic", "Part D spend", "First approval", "Type", "Years",
                                        "Age threshold", "Single mfr", "Score"], rows, [2, 5, 8]), cites,
                          banner="ESTIMATE — not a CMS determination. Does not model orphan, small-biotech, "
                                 "biosimilar-delay or Part B rules; 'single manufacturer' is a proxy for no "
                                 "generic/biosimilar competition.")

    def v_exposure(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        hit = [n for n in db.scalars(select(NegotiationDrug)) if _matches(n.generic or n.drug, scope["drugs"])
               and n.mfp_30d and n.list_price_30d]
        if not hit:
            return ViewResult("exposure", "Price Exposure",
                              empty="No scope drug has a published negotiated price with a CMS-cited list price yet.")
        n = hit[0] if not params.get("drug") else next((h for h in hit if h.drug == params["drug"]), hit[0])
        chart = charts.waterfall([("List price (30d)", n.list_price_30d or 0),
                                  ("Negotiated reduction", -((n.list_price_30d or 0) - (n.mfp_30d or 0))),
                                  ("MFP (30d)", 0)])
        chart["series"][0]["data"][-1] = 0
        chart["series"][1]["data"][-1] = round(n.mfp_30d or 0, 2)
        rows = [[h.drug, h.ipay, charts.fmt_usd(h.list_price_30d, 0), f"${h.mfp_30d:,.2f}",
                 charts.fmt_pct(h.discount_pct, 0)] for h in hit]
        return ViewResult("exposure", "Price Exposure", f"{n.drug}: list ${n.list_price_30d:,.0f} → MFP "
                          f"${n.mfp_30d:,.2f} per 30-day supply ({n.discount_pct:.0f}% lower), effective "
                          f"{n.ipay}.", chart, charts.table(["Drug", "IPAY", "List / 30d", "MFP / 30d", "Discount"],
                                                             rows, [2, 3, 4]), [_ncite(db, h) for h in hit],
                          banner="Net prices after rebates are confidential; only CMS-published figures are shown.")

    def v_class_impact(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        spend, cites, _ = class_spend(db, scope["drugs"])
        if not spend:
            return ViewResult("class_impact", "Class Impact", empty="No Medicare spending rows matched the scope drugs.",
                              citations=cites)
        latest = max(max(s) for s in spend.values() if s)
        neg = {}
        for n in db.scalars(select(NegotiationDrug)):
            for g in spend:
                if _matches(n.generic or n.drug, [g]):
                    neg[g] = min(neg.get(g, 9999), n.ipay)
        years = sorted({y for s in spend.values() for y in s})
        ser = [(g.title() + (f" (IPAY {neg[g]})" if g in neg else ""), [round(spend[g].get(y, 0) / 1e9, 3)
                                                                          for y in years]) for g in spend]
        total = sum(s.get(latest, 0) for s in spend.values())
        under = sum(s.get(latest, 0) for g, s in spend.items() if g in neg)
        k = Kpi(f"Class spend under negotiation ({latest})", charts.fmt_pct(100 * under / total if total else 0),
                100 * under / total if total else 0, citation=cites[0] if cites else None)
        return ViewResult("class_impact", "Class Impact",
                          f"{charts.fmt_usd(under)} of {charts.fmt_usd(total)} Medicare spend in {latest} is on drugs "
                          "selected for negotiation (any IPAY).", charts.bar([str(y) for y in years], ser,
                                                                              stacked=True, unit="$B"),
                          citations=cites, kpis=[k])

    def v_mfn(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        items = db.scalars(select(PolicyItem).where(PolicyItem.topic == "mfn")
                           .order_by(PolicyItem.status_date.desc())).all()
        if not items:
            return ViewResult("mfn", "MFN Tracker", empty="No MFN items yet. Refresh the policy feed in R3 or "
                                                          "add an official item in Admin → Policy items.")
        rows = [[i.title[:140], i.doc_type.replace("_", " "), i.status,
                 i.status_date.isoformat() if i.status_date else "—", i.agency[:60]] for i in items[:40]]
        cites = [Citation(i.source_key, source_name(db, i.source_key), i.status_date.isoformat() if i.status_date else "",
                          i.source_url, Citation.now_iso(i.retrieved_at), i.external_id) for i in items[:12]]
        return ViewResult("mfn", "MFN Tracker", "Official items only, with status and date from the source. "
                          "Details of individual company agreements are shown only where officially disclosed.",
                          table=charts.table(["Item", "Type", "Status", "Status date", "Agency"], rows),
                          citations=cites)

    # ---------------------------------------------------------------- tools
    def t_selected_drugs(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        sel = db.scalars(select(NegotiationDrug).order_by(NegotiationDrug.ipay)).all()
        drugs = scope.get("drugs") or []
        rel = [n for n in sel if not drugs or _matches(n.generic or n.drug, drugs)] or sel
        lines = [f"IPAY {n.ipay}: {n.drug} ({n.generic}; Part {n.part}; {n.selection_type}); status {n.status}"
                 + (f"; MFP ${n.mfp_30d:,.2f}/30d" if n.mfp_30d else "")
                 + (f"; list ${n.list_price_30d:,.0f}" if n.list_price_30d else "")
                 + (f"; discount {n.discount_pct:.0f}%" if n.discount_pct else "") for n in rel]
        return ToolResult("selected_drugs", "CMS Medicare Drug Price Negotiation Program:\n" + "\n".join(lines),
                          list({n.source_url: _ncite(db, n) for n in rel}.values()))

    def t_candidates(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        cands, cites = self._candidates(db)
        lines = [f"{c['brand']} ({c['generic']}): Part D spend {charts.fmt_usd(c['spend'])} in {c['year']}; "
                 f"first approval {c['approved'] or 'unknown'} {c['app_type']}; score {c['score']} (estimate)"
                 for c in cands[:12]]
        return ToolResult("candidates", "ESTIMATED candidates (not CMS determinations):\n" + "\n".join(lines), cites)

    def t_class_exposure(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_class_impact(db, scope, {})
        return ToolResult("class_exposure", v.insight or v.empty, v.citations)

    def t_mfn_items(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_mfn(db, scope, {})
        rows = (v.table or {}).get("rows", [])[:12]
        return ToolResult("mfn_items", "\n".join(" | ".join(map(str, r)) for r in rows) or v.empty, v.citations)
