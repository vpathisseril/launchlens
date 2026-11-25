"""R1 — Market Access & Payer Barriers hub (rank #1)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.connectors import acs_states
from app.data.fetch import source_name
from app.hubs.base import Hub, claims_by_state, prescriber_rows
from app.models import FormularyPlan, FormularyRestriction
from app.research import charts
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult

FORMULARY_PAGE = ("https://www.cms.gov/research-statistics-data-and-systems/files-for-order/nonidentifiabledatafiles/"
                  "prescriptiondrugplanformularypharmacynetworkandpricinginformationfiles")
W = {"pa": 0.4, "st": 0.3, "ql": 0.1, "nonpref": 0.2}
NO_DATA = ("Formulary data has not been ingested yet. An admin can load the latest CMS Part D formulary file under "
           "Admin → Data Sources (or run `python -m app.data.formulary`).")


def _vintages(db: Session) -> list[str]:
    return list(db.scalars(select(FormularyPlan.vintage).distinct().order_by(FormularyPlan.vintage.desc())))


def _cite(db: Session, vintage: str, locator: str = "") -> Citation:
    ts = db.scalar(select(func.max(FormularyPlan.retrieved_at)).where(FormularyPlan.vintage == vintage))
    return Citation("partd_formulary", source_name(db, "partd_formulary"), vintage,
                    FORMULARY_PAGE,
                    Citation.now_iso(ts), locator or "plan × RxCUI; plan-count weighted")


def access_metrics(db: Session, drugs: list[str], vintage: str, state: str | None = None) -> dict[str, dict[str, float]]:
    """Per drug: % plans covering, PA/ST/QL/non-preferred rates among covering plans, and Access Ease Index."""
    pq = select(FormularyPlan.contract_id, FormularyPlan.plan_id, FormularyPlan.state).where(FormularyPlan.vintage == vintage)
    if state:
        pq = pq.where(FormularyPlan.state == state)
    plans = {(c, p, s) for c, p, s in db.execute(pq)}
    out: dict[str, dict[str, float]] = {}
    if not plans:
        return out
    for d in drugs:
        q = select(FormularyRestriction).where(FormularyRestriction.vintage == vintage,
                                               FormularyRestriction.drug == d.lower())
        if state:
            q = q.where(FormularyRestriction.state == state)
        rows = [r for r in db.scalars(q) if (r.contract_id, r.plan_id, r.state) in plans]
        n_cov, n = len(rows), len(plans)
        pa = sum(r.prior_auth for r in rows) / n_cov if n_cov else 0
        st = sum(r.step_therapy for r in rows) / n_cov if n_cov else 0
        ql = sum(r.quantity_limit for r in rows) / n_cov if n_cov else 0
        npf = sum(1 for r in rows if r.tier == 4) / n_cov if n_cov else 0
        cov = n_cov / n
        aei = 100 * cov * (1 - W["pa"] * pa - W["st"] * st - W["ql"] * ql - W["nonpref"] * npf)
        out[d.lower()] = {"coverage": 100 * cov, "pa": 100 * pa, "st": 100 * st, "ql": 100 * ql,
                          "nonpref": 100 * npf, "aei": aei, "plans": n, "covering": n_cov}
    return out


def state_aei(db: Session, drugs: list[str], vintage: str) -> dict[str, float]:
    """Class-average Access Ease Index by state."""
    states = list(db.scalars(select(FormularyPlan.state).where(FormularyPlan.vintage == vintage).distinct()))
    out = {}
    for st in states:
        m = access_metrics(db, drugs, vintage, st)
        if m:
            out[st] = round(sum(v["aei"] for v in m.values()) / len(m), 1)
    return out


class AccessHub(Hub):
    info = HubInfo(
        key="access", title="Market Access & Payer Barriers", short="Access", icon="shield-check", rank=1,
        question="How hard is it for patients to get this class covered — and where is access the bottleneck?",
        views=[("scorecard", "Access Scorecard"), ("heatmap", "Restriction Heatmap"), ("geo", "Geographic Access"),
               ("quadrant", "Access vs. Demand"), ("trend", "Trend across vintages")],
        tools={"access_summary": "Coverage, PA, step therapy, quantity limits and Access Ease Index per drug",
               "state_access": "Access Ease Index by state for the class",
               "access_trend": "Change in restriction rates between formulary vintages"},
    )

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        vs = _vintages(db)
        if not vs or not scope.get("drugs"):
            return [Kpi("Access Ease Index", "—", hint="Load formulary data" if not vs else "Set a scope")]
        m = access_metrics(db, scope["drugs"], vs[0])
        if not m:
            return [Kpi("Access Ease Index", "—", hint="No plans in this vintage")]
        aei = sum(v["aei"] for v in m.values()) / len(m)
        k = Kpi("Access Ease Index (class avg)", f"{aei:.0f}/100", aei, citation=_cite(db, vs[0]))
        if len(vs) > 1:
            prev = access_metrics(db, scope["drugs"], vs[1])
            if prev:
                p = sum(v["aei"] for v in prev.values()) / len(prev)
                k.delta, k.delta_dir = f"{aei - p:+.1f} vs {vs[1]}", "up" if aei > p else ("down" if aei < p else "flat")
        return [k]

    # ---------------------------------------------------------------- views
    def v_scorecard(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        vs = _vintages(db)
        if not vs:
            return ViewResult("scorecard", "Access Scorecard", empty=NO_DATA)
        m = access_metrics(db, scope["drugs"], vs[0])
        cite = _cite(db, vs[0])
        drugs = list(m)
        chart = charts.bar([d.title() for d in drugs], [
            ("Coverage %", [round(m[d]["coverage"], 1) for d in drugs]),
            ("Prior auth %", [round(m[d]["pa"], 1) for d in drugs]),
            ("Step therapy %", [round(m[d]["st"], 1) for d in drugs]),
            ("Quantity limit %", [round(m[d]["ql"], 1) for d in drugs])], unit="% of plans")
        rows = [[d.title(), f"{m[d]['covering']:,} / {m[d]['plans']:,}", charts.fmt_pct(m[d]["coverage"]),
                 charts.fmt_pct(m[d]["pa"]), charts.fmt_pct(m[d]["st"]), charts.fmt_pct(m[d]["ql"]),
                 charts.fmt_pct(m[d]["nonpref"]), f"{m[d]['aei']:.0f}"] for d in drugs]
        best = max(drugs, key=lambda d: m[d]["aei"]) if drugs else None
        worst = min(drugs, key=lambda d: m[d]["aei"]) if drugs else None
        insight = (f"{best.title()} has the easiest access (AEI {m[best]['aei']:.0f}); {worst.title()} the hardest "
                   f"(AEI {m[worst]['aei']:.0f}). PA/ST rates are among plans that cover the drug.") if best else ""
        return ViewResult("scorecard", "Access Scorecard", insight, chart,
                          charts.table(["Drug", "Plans covering", "Coverage", "PA", "Step therapy", "Qty limit",
                                        "Non-preferred tier", "AEI"], rows, [1, 2, 3, 4, 5, 6, 7]),
                          [cite], banner="Plan-count weighted (each plan × state counts once). AEI = 100 × coverage × "
                                         "(1 − 0.4·PA − 0.3·ST − 0.1·QL − 0.2·non-preferred).")

    def v_heatmap(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        vs = _vintages(db)
        if not vs:
            return ViewResult("heatmap", "Restriction Heatmap", empty=NO_DATA)
        cells, ycats = [], []
        xcats = ["Not covered", "Prior auth", "Step therapy", "Qty limit", "Non-preferred"]
        for ptype in ("PDP", "MA-PD"):
            sub = _by_plan_type(db, scope["drugs"], vs[0], ptype)
            for d, v in sub.items():
                ycats.append(f"{d.title()} · {ptype}")
                y = len(ycats) - 1
                for x, key in enumerate(["uncov", "pa", "st", "ql", "nonpref"]):
                    cells.append([x, y, round(v[key], 1)])
        return ViewResult("heatmap", "Restriction Heatmap", "Share of plans applying each barrier, by plan type.",
                          charts.heatmap(xcats, ycats, cells), citations=[_cite(db, vs[0])])

    def v_geo(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        vs = _vintages(db)
        if not vs:
            return ViewResult("geo", "Geographic Access", empty=NO_DATA)
        aei = state_aei(db, scope["drugs"], vs[0])
        top = sorted(aei.items(), key=lambda kv: kv[1])[:5]
        insight = "Hardest-access states: " + ", ".join(f"{s} ({v:.0f})" for s, v in top) if top else ""
        return ViewResult("geo", "Geographic Access", insight, charts.state_tiles(aei, "AEI"),
                          charts.table(["State", "Access Ease Index"], [[s, f"{v:.1f}"] for s, v in
                                                                         sorted(aei.items(), key=lambda kv: kv[1])], [1]),
                          [_cite(db, vs[0], "class-average AEI by state")])

    def v_quadrant(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        vs = _vintages(db)
        if not vs:
            return ViewResult("quadrant", "Access vs. Demand", empty=NO_DATA)
        aei = state_aei(db, scope["drugs"], vs[0])
        rows, pcites, trunc, year = prescriber_rows(db, {**scope, "states": []})
        demand_raw = claims_by_state(rows)
        acs, acite = acs_states(db)
        demand = {st: 1000 * c / acs[st]["pop65"] for st, c in demand_raw.items() if st in acs and acs[st]["pop65"]}
        pts = [{"name": st, "x": aei[st], "y": round(demand[st], 1)} for st in aei if st in demand]
        if not pts:
            return ViewResult("quadrant", "Access vs. Demand", empty="Not enough overlapping state data yet.")
        xm = sorted(p["x"] for p in pts)[len(pts) // 2]
        ym = sorted(p["y"] for p in pts)[len(pts) // 2]
        fix = sorted([p for p in pts if p["x"] < xm and p["y"] >= ym], key=lambda p: -p["y"])
        insight = ("'Fix access' states (high demand, low access): " + ", ".join(p["name"] for p in fix[:8])) if fix else ""
        rows_t = [[p["name"], f"{p['x']:.0f}", f"{p['y']:.1f}",
                   "Win" if p["x"] >= xm and p["y"] >= ym else "Fix access" if p["y"] >= ym else
                   "Build demand" if p["x"] >= xm else "Deprioritize"] for p in sorted(pts, key=lambda p: -p["y"])]
        return ViewResult("quadrant", "Access vs. Demand", insight,
                          charts.quadrant(pts, "Access Ease Index", "Class Part D claims per 1k age 65+", xm, ym,
                                          ("Win", "Fix access", "Deprioritize", "Build demand")),
                          charts.table(["State", "AEI", "Claims/1k 65+", "Quadrant"], rows_t, [1, 2]),
                          [_cite(db, vs[0]), *pcites[:3], acite],
                          banner="Demand = Medicare Part D claims only (proxy). Prescriber rows with <11 claims are "
                                 "suppressed by CMS." + (" Results truncated — narrow by state." if trunc else ""))

    def v_trend(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        vs = _vintages(db)
        if len(vs) < 2:
            return ViewResult("trend", "Trend across vintages",
                              empty="Load at least two formulary vintages to see tightening or loosening.")
        a, b = access_metrics(db, scope["drugs"], vs[1]), access_metrics(db, scope["drugs"], vs[0])
        drugs = [d for d in b if d in a]
        rows = []
        for d in drugs:
            dpa, dst = b[d]["pa"] - a[d]["pa"], b[d]["st"] - a[d]["st"]
            flag = "⚑ >10 pts" if abs(dpa) > 10 or abs(dst) > 10 else ""
            rows.append([d.title(), f"{dpa:+.1f}", f"{dst:+.1f}", f"{b[d]['aei'] - a[d]['aei']:+.1f}", flag])
        chart = charts.bar([d.title() for d in drugs], [(vs[1], [round(a[d]["aei"], 1) for d in drugs]),
                                                        (vs[0], [round(b[d]["aei"], 1) for d in drugs])], unit="AEI")
        return ViewResult("trend", "Trend across vintages", f"Comparing {vs[1]} → {vs[0]}.", chart,
                          charts.table(["Drug", "Δ PA pts", "Δ ST pts", "Δ AEI", "Flag"], rows, [1, 2, 3]),
                          [_cite(db, vs[0]), _cite(db, vs[1])])

    # ---------------------------------------------------------------- agent tools
    def t_access_summary(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        vs = _vintages(db)
        if not vs:
            return ToolResult("access_summary", "DATA UNAVAILABLE: formulary data not ingested.")
        m = access_metrics(db, scope["drugs"], vs[0])
        lines = [f"{d.title()}: coverage {v['coverage']:.1f}% of {int(v['plans'])} plans; PA {v['pa']:.1f}%; "
                 f"step therapy {v['st']:.1f}%; quantity limit {v['ql']:.1f}%; non-preferred tier {v['nonpref']:.1f}%; "
                 f"AEI {v['aei']:.1f}" for d, v in m.items()]
        return ToolResult("access_summary", f"Part D formulary {vs[0]} (plan-count weighted):\n" + "\n".join(lines),
                          [_cite(db, vs[0])], m)

    def t_state_access(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        vs = _vintages(db)
        if not vs:
            return ToolResult("state_access", "DATA UNAVAILABLE: formulary data not ingested.")
        aei = state_aei(db, scope["drugs"], vs[0])
        srt = sorted(aei.items(), key=lambda kv: kv[1])
        digest = ("Lowest AEI states: " + ", ".join(f"{s} {v:.1f}" for s, v in srt[:8]) +
                  "\nHighest AEI states: " + ", ".join(f"{s} {v:.1f}" for s, v in srt[-8:]))
        return ToolResult("state_access", digest, [_cite(db, vs[0], "class-average AEI by state")], aei)

    def t_access_trend(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_trend(db, scope, {})
        if v.empty:
            return ToolResult("access_trend", "DATA UNAVAILABLE: " + v.empty)
        digest = "\n".join(" | ".join(map(str, r)) for r in (v.table or {}).get("rows", []))
        return ToolResult("access_trend", "Drug | ΔPA | ΔST | ΔAEI | flag\n" + digest, v.citations)


def _by_plan_type(db: Session, drugs: list[str], vintage: str, ptype: str) -> dict[str, dict[str, float]]:
    plans = {(c, p, s) for c, p, s in db.execute(select(FormularyPlan.contract_id, FormularyPlan.plan_id,
                                                         FormularyPlan.state).where(FormularyPlan.vintage == vintage,
                                                                                    FormularyPlan.plan_type == ptype))}
    out: dict[str, dict[str, float]] = {}
    if not plans:
        return out
    for d in drugs:
        rows = [r for r in db.scalars(select(FormularyRestriction).where(
            FormularyRestriction.vintage == vintage, FormularyRestriction.drug == d.lower(),
            FormularyRestriction.plan_type == ptype)) if (r.contract_id, r.plan_id, r.state) in plans]
        n = len(rows)
        agg: dict[str, float] = defaultdict(float)
        for r in rows:
            agg["pa"] += r.prior_auth
            agg["st"] += r.step_therapy
            agg["ql"] += r.quantity_limit
            agg["nonpref"] += r.tier == 4
        out[d.lower()] = {"uncov": 100 * (1 - n / len(plans)), **{k: (100 * agg[k] / n if n else 0)
                                                                    for k in ("pa", "st", "ql", "nonpref")}}
    return out
