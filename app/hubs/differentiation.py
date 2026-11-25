"""R5 — Market Understanding & Differentiation hub (rank #5)."""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.data.connectors import (
    class_peers,
    ctgov_studies,
    drugsfda_for_generic,
    faers_count,
    first_approval,
    label_for_generic,
    label_search,
    pubmed_count,
)
from app.data.fetch import SourceUnavailable
from app.hubs.base import Hub
from app.hubs.pricing import class_spend
from app.research import charts
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult

PHASES = ["PHASE2", "PHASE3"]
ACTIVE = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION"]


def _first(v: Any) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def _excerpt(label: dict[str, Any], field: str, n: int = 160) -> str:
    t = " ".join(label.get(field) or [])
    t = " ".join(t.split())
    return (t[:n] + "…") if len(t) > n else t


def _yr(s: str) -> float | None:
    try:
        parts = [int(x) for x in s.split("-")]
        return parts[0] + ((parts[1] - 1) / 12 if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


class DifferentiationHub(Hub):
    info = HubInfo(
        key="differentiation", title="Market Understanding & Differentiation", short="Differentiation",
        icon="git-compare", rank=5,
        question="Who competes for this indication today and tomorrow — and where can a new product stand out?",
        views=[("market", "Market Map"), ("competitors", "Competitor Discovery"), ("matrix", "Label Matrix"),
               ("pipeline", "Pipeline Timeline"), ("approvals", "Approval Timeline"), ("evidence", "Evidence Pulse")],
        tools={"find_competitors": "Competitors for the indication from FDA labels, ATC class and active trials",
               "compare_labels": "Label-grounded comparison of scope drugs (route, boxed warning, dosing)",
               "pipeline": "Active Phase 2/3 trials for the indication with sponsors and completion dates",
               "market_size": "Medicare spend by drug in the class with growth"},
    )

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        if not scope.get("indication"):
            return [Kpi("Active Phase 3 trials", "—", hint="Set an indication")]
        try:
            trials, cite = ctgov_studies(db, scope["indication"], ["PHASE3"], ACTIVE)
        except SourceUnavailable:
            return [Kpi("Active Phase 3 trials", "—", hint="ClinicalTrials.gov unavailable")]
        soon = [t for t in trials if (_yr(t["primary_completion"]) or 9999) <= date.today().year + 2]
        return [Kpi("Active Phase 3 trials", f"{len(trials)}", float(len(trials)),
                    delta=f"{len(soon)} complete ≤24 mo", delta_dir="flat", citation=cite)]

    def v_market(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        spend, cites, _ = class_spend(db, scope["drugs"])
        if not spend:
            return ViewResult("market", "Market Map", empty="No Medicare spend matched the scope drugs.", citations=cites)
        latest = max(max(s) for s in spend.values() if s)
        items, rows = [], []
        for g, s in spend.items():
            cur, prev = s.get(latest, 0), s.get(latest - 1, 0)
            yoy = 100 * (cur - prev) / prev if prev else None
            items.append({"name": g.title(), "value": round(cur), "yoy": None if yoy is None else round(yoy, 1)})
            rows.append([g.title(), charts.fmt_usd(cur), charts.fmt_pct(yoy) if yoy is not None else "—"])
        total = sum(i["value"] for i in items)
        rows.sort(key=lambda r: -spend[r[0].lower()].get(latest, 0))
        return ViewResult("market", f"Market Map · Medicare {latest}",
                          f"Scope drugs total {charts.fmt_usd(total)} in Medicare Part D + B spend ({latest}).",
                          charts.treemap(items), charts.table(["Drug", f"Spend {latest}", "YoY"], rows, [1, 2]), cites,
                          banner="Gross Medicare spend before rebates.")

    def _discover(self, db: Session, scope: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Citation]]:
        found: dict[str, dict[str, Any]] = {}
        cites: list[Citation] = []
        if scope.get("indication"):
            labels, c = label_search(db, scope["indication"])
            cites.append(c)
            for lab in labels:
                o = lab.get("openfda", {})
                g = _first(o.get("generic_name")).lower()
                if not g or _first(o.get("product_type")).upper() == "HUMAN OTC DRUG":
                    continue
                e = found.setdefault(g, {"generic": g, "brands": set(), "sources": set(), "mfr": set()})
                e["brands"].update(b.title() for b in o.get("brand_name", [])[:2])
                e["mfr"].update(o.get("manufacturer_name", [])[:1])
                e["sources"].add("FDA label")
        for d in scope.get("drugs", [])[:3]:
            try:
                peers, c = class_peers(db, d)
            except SourceUnavailable:
                continue
            if c:
                cites.append(c)
            for name, cls in peers:
                e = found.setdefault(name.lower(), {"generic": name.lower(), "brands": set(), "sources": set(),
                                                    "mfr": set()})
                e["sources"].add(f"ATC: {cls}")
        if scope.get("indication"):
            trials, c = ctgov_studies(db, scope["indication"], ["PHASE3"], ACTIVE)
            cites.append(c)
            cnt = Counter(i.lower() for t in trials for i in t["interventions"] if i)
            for name, k in cnt.most_common(12):
                if "placebo" in name or len(name) < 4:
                    continue
                e = found.setdefault(name, {"generic": name, "brands": set(), "sources": set(), "mfr": set()})
                e["sources"].add(f"Phase 3 trials ({k})")
        scope_l = {d.lower() for d in scope.get("drugs", [])}
        out = []
        for e in found.values():
            e["in_scope"] = any(s in e["generic"] or e["generic"] in s for s in scope_l)
            out.append(e)
        out.sort(key=lambda e: (not e["in_scope"], -len(e["sources"]), e["generic"]))
        return out, cites

    def v_competitors(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        comps, cites = self._discover(db, scope)
        if not comps:
            return ViewResult("competitors", "Competitor Discovery", empty="Nothing found — check the indication text.",
                              citations=cites)
        rows = [[("✓ " if c["in_scope"] else "") + c["generic"].title(), ", ".join(sorted(c["brands"]))[:60] or "—",
                 ", ".join(sorted(c["mfr"]))[:50] or "—", " · ".join(sorted(c["sources"]))] for c in comps[:60]]
        return ViewResult("competitors", "Competitor Discovery",
                          f"{len(comps)} candidates found. ✓ = already in your scope; use 'Edit scope' to add or remove.",
                          table=charts.table(["Ingredient", "Brands", "Labeler", "Discovered via"], rows),
                          citations=cites)

    def v_matrix(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        rows, cites = [], []
        for d in scope["drugs"]:
            lab, c = label_for_generic(db, d)
            cites.append(c)
            if not lab:
                rows.append([d.title(), "label not found", "", "", "", "", ""])
                continue
            o = lab.get("openfda", {})
            faers, fc = faers_count(db, d)
            cites.append(fc)
            rows.append([d.title(), ", ".join(o.get("brand_name", [])[:2]), ", ".join(o.get("route", [])[:2]),
                         "Yes" if lab.get("boxed_warning") else "No",
                         _excerpt(lab, "dosage_and_administration", 140), _excerpt(lab, "indications_and_usage", 160),
                         f"{faers:,}" if faers is not None else "—"])
            c.locator = f"set_id {lab.get('set_id', '')} · sections: boxed_warning, dosage_and_administration, indications"
        return ViewResult("matrix", "Label Matrix", "Each cell comes from the drug's current FDA label (openFDA SPL).",
                          table=charts.table(["Drug", "Brand", "Route", "Boxed warning", "Dosing (excerpt)",
                                              "Indications (excerpt)", "FAERS reports"], rows, [6]), citations=cites,
                          banner="FAERS report counts do not establish causation or incidence and are not comparable "
                                 "across drugs with different exposure.")

    def v_pipeline(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        if not scope.get("indication"):
            return ViewResult("pipeline", "Pipeline Timeline", empty="Set an indication in the scope.")
        trials, cite = ctgov_studies(db, scope["indication"], PHASES, ACTIVE)
        bars, rows = [], []
        for t in trials:
            s, e = _yr(t["start"]), _yr(t["primary_completion"])
            ph = "/".join(p.replace("PHASE", "P") for p in t["phases"]) or "NA"
            if s and e:
                bars.append((f"{t['nct']} · {t['sponsor'][:24]}", s, e, ph))
            rows.append([t["nct"], t["title"][:90], ph, t["sponsor"][:40], ", ".join(t["interventions"])[:60],
                         t["primary_completion"], (t["primary_outcome"] or "")[:80]])
        bars.sort(key=lambda b: b[2])
        sponsors = Counter(t["sponsor"] for t in trials).most_common(3)
        return ViewResult("pipeline", "Pipeline Timeline",
                          f"{len(trials)} active Phase 2/3 trials. Most active sponsors: " +
                          ", ".join(f"{s} ({n})" for s, n in sponsors),
                          charts.timeline_bars(bars[:30]) if bars else None,
                          charts.table(["NCT", "Title", "Phase", "Sponsor", "Interventions", "Primary completion",
                                        "Primary outcome"], rows), [cite])

    def v_approvals(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        rows, cites, pts = [], [], []
        for d in scope["drugs"]:
            apps, c = drugsfda_for_generic(db, d)
            cites.append(c)
            dt, kind = first_approval(apps)
            generics = sum(1 for a in apps if str(a.get("application_number", "")).startswith("ANDA"))
            sponsor = next((a.get("sponsor_name", "") for a in apps if not
                            str(a.get("application_number", "")).startswith("ANDA")), "")
            if dt:
                yrs = (date.today() - date(int(dt[:4]), int(dt[4:6]), int(dt[6:8]))).days / 365.25
                pts.append((d.title(), int(dt[:4]) + int(dt[4:6]) / 12, date.today().year + 0.5, kind))
                rows.append([d.title(), sponsor, kind, f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", f"{yrs:.1f}", generics])
            else:
                rows.append([d.title(), sponsor, kind or "—", "not found", "—", generics])
        return ViewResult("approvals", "Approval Timeline", "Time on market since first original approval (Drugs@FDA). "
                          "ANDA count indicates generic entry.", charts.timeline_bars(pts) if pts else None,
                          charts.table(["Drug", "Sponsor", "Type", "First approval", "Years on market", "ANDAs"],
                                       rows, [4, 5]), cites,
                          banner="Patent and exclusivity expiry (Orange/Purple Book) is on the roadmap; dates here "
                                 "are approval dates only.")

    def v_evidence(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        y1 = date.today().year
        years = list(range(y1 - 5, y1))
        lines, cites = [], []
        for d in scope["drugs"][:5]:
            vals = []
            for y in years:
                n, c = pubmed_count(db, f'"{d}"[tiab] AND {y}[dp]')
                vals.append(n)
                if y == years[-1]:
                    cites.append(c)
            lines.append((d.title(), vals))
        return ViewResult("evidence", "Evidence Pulse", "PubMed records mentioning each drug in title/abstract, by year.",
                          charts.line([str(y) for y in years], lines, unit="articles"), citations=cites)

    # ---------------------------------------------------------------- tools
    def t_find_competitors(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        comps, cites = self._discover(db, scope)
        lines = [f"{c['generic'].title()} ({', '.join(sorted(c['brands'])) or 'no brand on label'}): "
                 f"found via {', '.join(sorted(c['sources']))}" for c in comps[:20]]
        return ToolResult("find_competitors", "\n".join(lines) or "No competitors found.", cites)

    def t_compare_labels(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_matrix(db, scope, {})
        rows = (v.table or {}).get("rows", [])
        return ToolResult("compare_labels", "Drug | Brand | Route | Boxed warning | Dosing | Indications | FAERS reports\n"
                          + "\n".join(" | ".join(map(str, r)) for r in rows) + "\nNote: " + v.banner, v.citations)

    def t_pipeline(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_pipeline(db, scope, {})
        rows = (v.table or {}).get("rows", [])[:20]
        return ToolResult("pipeline", v.insight + "\n" + "\n".join(" | ".join(map(str, r)) for r in rows), v.citations)

    def t_market_size(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_market(db, scope, {})
        rows = (v.table or {}).get("rows", [])
        return ToolResult("market_size", v.insight + "\n" + "\n".join(" | ".join(map(str, r)) for r in rows), v.citations)
