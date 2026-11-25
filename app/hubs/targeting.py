"""R4 — Precision Targeting & Time-to-Peak hub (rank #4). Transparent, explainable opportunity score."""
from __future__ import annotations

import contextlib
import json
import time
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.data.connectors import PLACES_MEASURES, _num, places_state_prevalence, spend_years, spending_all
from app.data.fetch import SourceUnavailable
from app.hubs.access import _vintages, state_aei
from app.hubs.base import Hub, pctile_rank, prescriber_rows
from app.hubs.pricing import _matches
from app.research import charts
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult

DEFAULT_W = {"volume": 25, "growth": 20, "need": 15, "access": 20, "vulnerability": 10, "engagement": 10}
COMP_LABEL = {"volume": "Class volume", "growth": "Growth", "need": "Need (prevalence)", "access": "Access ease",
              "vulnerability": "Openness (1−HHI)", "engagement": "Engagement"}
_cache: dict[str, tuple[float, Any]] = {}


def parse_weights(params: dict[str, Any]) -> dict[str, float]:
    w = dict(DEFAULT_W)
    for k in w:
        v = params.get(f"w_{k}")
        if v not in (None, ""):
            with contextlib.suppress(ValueError):
                w[k] = max(0.0, min(100.0, float(v)))
    return w


def _hcp_table(db: Session, scope: dict[str, Any], year_index: int) -> tuple[dict[str, dict[str, Any]], list[Citation],
                                                                            bool, str]:
    rows, cites, trunc, year = prescriber_rows(db, scope, year_index)
    hcps: dict[str, dict[str, Any]] = {}
    for r in rows:
        npi = str(r.get("Prscrbr_NPI", ""))
        if not npi:
            continue
        h = hcps.setdefault(npi, {"npi": npi, "name": f"{r.get('Prscrbr_First_Name') or ''} "
                                                      f"{r.get('Prscrbr_Last_Org_Name') or ''}".strip(),
                                  "city": r.get("Prscrbr_City") or "", "state": r.get("Prscrbr_State_Abrvtn") or "",
                                  "specialty": r.get("Prscrbr_Type") or "Unknown", "vol": 0.0, "by_drug": Counter(),
                                  "benes_suppressed": False})
        c = _num(r.get("Tot_Clms")) or 0
        h["vol"] += c
        h["by_drug"][r["_drug"]] += c
        if r.get("Tot_Benes") in (None, ""):
            h["benes_suppressed"] = True
    return hcps, cites, trunc, year


def score(db: Session, scope: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    key = json.dumps([scope.get("drugs"), scope.get("states"), scope.get("places_measure"), weights], sort_keys=True)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    cur, cites, trunc, year = _hcp_table(db, scope, 0)
    try:
        prev, pc, trunc2, prev_year = _hcp_table(db, scope, 1)
        cites += pc[:1]
    except SourceUnavailable:
        prev, prev_year, trunc2 = {}, "", False
    comps: dict[str, dict[str, float]] = {}
    by_spec: dict[str, dict[str, float]] = defaultdict(dict)
    for npi, h in cur.items():
        by_spec[h["specialty"]][npi] = h["vol"]
    comps["volume"] = {}
    for _spec, vals in by_spec.items():
        comps["volume"].update(pctile_rank(vals))
    if prev:
        comps["growth"] = pctile_rank({npi: (h["vol"] - prev.get(npi, {}).get("vol", 0.0)) /
                                       max(prev.get(npi, {}).get("vol", 0.0), 11.0) for npi, h in cur.items()})
    need_state: dict[str, float] = {}
    measure = scope.get("places_measure") or ""
    if measure:
        try:
            prev_st, pcite = places_state_prevalence(db, measure)
            need_state = pctile_rank(prev_st)
            cites.append(pcite)
        except SourceUnavailable:
            pass
    if need_state:
        comps["need"] = {npi: need_state.get(h["state"], 50.0) for npi, h in cur.items()}
    vs = _vintages(db)
    if vs:
        aei = state_aei(db, scope["drugs"], vs[0])
        if aei:
            comps["access"] = {npi: aei.get(h["state"], 50.0) for npi, h in cur.items()}
    comps["vulnerability"] = {}
    for npi, h in cur.items():
        tot = sum(h["by_drug"].values()) or 1
        hhi = sum((v / tot) ** 2 for v in h["by_drug"].values())
        comps["vulnerability"][npi] = 100 * (1 - hhi)
    avail = {k: w for k, w in weights.items() if k in comps and w > 0}
    wsum = sum(avail.values()) or 1
    for npi, h in cur.items():
        parts = {k: comps[k].get(npi, 0.0) * avail[k] / wsum for k in avail}
        h["parts"] = parts
        h["score"] = round(sum(parts.values()), 2)
        h["prev_vol"] = prev.get(npi, {}).get("vol", 0.0)
    ranked = sorted(cur.values(), key=lambda h: h["score"], reverse=True)
    n = len(ranked)
    for i, h in enumerate(ranked):
        h["decile"] = 10 - min(9, int(10 * i / max(n, 1)))
    missing = [COMP_LABEL[k] for k in weights if weights[k] > 0 and k not in comps]
    res = {"hcps": ranked, "cites": cites, "truncated": trunc or trunc2, "year": year, "prev_year": prev_year,
           "used": {k: round(100 * v / wsum, 1) for k, v in avail.items()}, "missing": missing}
    _cache[key] = (time.monotonic(), res)
    return res


def _banner(res: dict[str, Any]) -> str:
    b = [f"Medicare Part D {res['year']}" + (f" vs {res['prev_year']}" if res["prev_year"] else "") +
         " (proxy — no commercial claims). CMS omits prescriber-drug rows with <11 claims."]
    if res["missing"]:
        b.append("Weights re-normalized; unavailable components: " + ", ".join(res["missing"]) + ".")
    if res["truncated"]:
        b.append("Row cap reached — add states to the scope for complete results.")
    return " ".join(b)


class TargetingHub(Hub):
    info = HubInfo(
        key="targeting", title="Precision Targeting & Time-to-Peak", short="Targeting", icon="crosshair", rank=4,
        question="Which prescribers and states should a launch reach first — and how fast do analogs ramp?",
        views=[("deciles", "HCP Deciles"), ("whitespace", "White Space"), ("segments", "Segment Mix"),
               ("ramp", "Analog Ramp"), ("scenario", "Scenario Compare"), ("model", "Model Card & Backtest")],
        tools={"top_hcps": "Top prescribers by opportunity score with component breakdown",
               "white_space": "States ranked by opportunity (need × access × class volume)",
               "analog_ramp": "Medicare spend trajectory of class analogs indexed to peak",
               "explain_score": "How the opportunity score is built and which components are available"},
    )

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        if not scope.get("drugs"):
            return [Kpi("Top-decile share of class volume", "—", hint="Set a scope")]
        try:
            res = score(db, scope, DEFAULT_W)
        except SourceUnavailable:
            return [Kpi("Top-decile share of class volume", "—", hint="Data source unavailable")]
        hcps = res["hcps"]
        tot = sum(h["vol"] for h in hcps) or 1
        top = sum(h["vol"] for h in hcps if h["decile"] == 10)
        return [Kpi("Top-decile share of class volume", charts.fmt_pct(100 * top / tot, 0), 100 * top / tot,
                    hint=f"{len(hcps):,} prescribers scored", citation=res["cites"][0] if res["cites"] else None)]

    def v_deciles(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        res = score(db, scope, parse_weights(params))
        hcps = res["hcps"]
        if not hcps:
            return ViewResult("deciles", "HCP Deciles", empty="No prescribers found for these drugs.", citations=res["cites"])
        dec_vol = defaultdict(float)
        for h in hcps:
            dec_vol[h["decile"]] += h["vol"]
        tot = sum(dec_vol.values()) or 1
        chart = charts.bar([f"D{d}" for d in range(10, 0, -1)],
                           [("Share of class claims %", [round(100 * dec_vol[d] / tot, 1) for d in range(10, 0, -1)])])
        rows = [[h["decile"], h["npi"], h["name"], h["specialty"], f"{h['city']}, {h['state']}", f"{h['vol']:,.0f}",
                 f"{h['prev_vol']:,.0f}" if res["prev_year"] else "—", f"{h['score']:.1f}",
                 " · ".join(f"{COMP_LABEL[k][:6]} {v:.0f}" for k, v in h["parts"].items())] for h in hcps[:150]]
        return ViewResult("deciles", "HCP Deciles",
                          f"Decile 10 prescribers write {100 * dec_vol[10] / tot:.0f}% of class claims. Weights in use: "
                          + ", ".join(f"{COMP_LABEL[k]} {v:.0f}%" for k, v in res["used"].items()), chart,
                          charts.table(["Decile", "NPI", "Prescriber", "Specialty", "Location",
                                        f"Claims {res['year']}", f"Claims {res['prev_year']}", "Score", "Components"],
                                       rows, [5, 6, 7]), res["cites"], banner=_banner(res))

    def _state_opp(self, res: dict[str, Any]) -> dict[str, float]:
        agg: dict[str, list[float]] = defaultdict(list)
        for h in res["hcps"]:
            if h["state"]:
                agg[h["state"]].append(h["score"])
        vol: dict[str, float] = defaultdict(float)
        for h in res["hcps"]:
            vol[h["state"]] += h["vol"]
        vp = pctile_rank(dict(vol))
        return {st: round(0.5 * (sum(v) / len(v)) + 0.5 * vp.get(st, 0), 1) for st, v in agg.items() if v}

    def v_whitespace(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        res = score(db, {**scope, "states": []}, parse_weights(params))
        opp = self._state_opp(res)
        top = sorted(opp.items(), key=lambda kv: -kv[1])[:8]
        return ViewResult("whitespace", "White Space", "Highest-opportunity states: " +
                          ", ".join(f"{s} ({v:.0f})" for s, v in top), charts.state_tiles(opp, "Opportunity"),
                          charts.table(["State", "Opportunity index"], [[s, f"{v:.1f}"] for s, v in
                                                                         sorted(opp.items(), key=lambda kv: -kv[1])], [1]),
                          res["cites"], banner="State index = 50% mean HCP opportunity score + 50% class-volume "
                                               "percentile. " + _banner(res))

    def v_segments(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        res = score(db, scope, parse_weights(params))
        spec = Counter()
        for h in res["hcps"]:
            if h["decile"] >= 8:
                spec[h["specialty"]] += h["vol"]
        top = spec.most_common(10)
        return ViewResult("segments", "Segment Mix", "Specialties driving deciles 8–10 by class claims.",
                          charts.bar([s for s, _ in top][::-1], [("Claims (D8–10)", [round(v) for _, v in top][::-1])],
                                     horizontal=True), citations=res["cites"], banner=_banner(res))

    def _ramp(self, db: Session, scope: dict[str, Any]) -> tuple[dict[str, dict[int, float]], list[Citation]]:
        rows, cite, _ = spending_all(db, "partd_spending")
        out: dict[str, dict[int, float]] = {}
        for r in rows:
            g = str(r.get("Gnrc_Name", ""))
            if g and _matches(g, scope["drugs"]):
                acc = out.setdefault(g.title(), {})
                for y, v in spend_years(r).items():
                    acc[y] = acc.get(y, 0.0) + v
        return out, [cite]

    def v_ramp(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        series, cites = self._ramp(db, scope)
        if not series:
            return ViewResult("ramp", "Analog Ramp", empty="No Part D spending history matched.", citations=cites)
        years = sorted({y for s in series.values() for y in s})
        lines, rows = [], []
        for g, s in series.items():
            peak = max(s.values()) or 1
            idx = [round(100 * s.get(y, 0) / peak, 1) if y in s else None for y in years]
            lines.append((g, idx))
            first = next((y for y in years if s.get(y, 0) > 0), None)
            y50 = next((y for y in years if s.get(y, 0) >= 0.5 * peak), None)
            y80 = next((y for y in years if s.get(y, 0) >= 0.8 * peak), None)
            rows.append([g, first, y50, y80, charts.fmt_usd(peak), "still rising" if s.get(years[-1], 0) >= peak else
                         f"peaked {max(s, key=s.get)}"])
        return ViewResult("ramp", "Analog Ramp", "Medicare Part D spend indexed to each drug's peak within the CMS "
                          "window. Drugs launched before the window show only their late curve.",
                          charts.line([str(y) for y in years], lines, unit="% of peak"),
                          charts.table(["Drug", "First year in window", "≥50% peak", "≥80% peak", "Peak spend", "Status"],
                                       rows), cites, banner="Medicare-only proxy for time-to-peak.")

    def v_scenario(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        a = {"volume": 40, "growth": 10, "need": 10, "access": 10, "vulnerability": 20, "engagement": 10}
        b = {"volume": 15, "growth": 15, "need": 15, "access": 45, "vulnerability": 10, "engagement": 0}
        ra, rb = score(db, scope, a), score(db, scope, b)
        ta = {h["npi"] for h in ra["hcps"][:100]}
        tb = {h["npi"] for h in rb["hcps"][:100]}
        both = ta & tb
        rows = [["Volume-first", *[f"{a[k]}%" for k in DEFAULT_W]], ["Access-first", *[f"{b[k]}%" for k in DEFAULT_W]]]
        chart = charts.bar(["Only volume-first", "Both", "Only access-first"],
                           [("Top-100 prescribers", [len(ta - tb), len(both), len(tb - ta)])])
        return ViewResult("scenario", "Scenario Compare",
                          f"{len(both)} of the top 100 prescribers are the same under both strategies — the rest "
                          "depend on whether you lead with volume or with access.", chart,
                          charts.table(["Scenario", *[COMP_LABEL[k] for k in DEFAULT_W]], rows), ra["cites"],
                          banner=_banner(ra))

    def v_model(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        cur, cites, _, year = _hcp_table(db, scope, 0)
        try:
            prev, _, _, prev_year = _hcp_table(db, scope, 1)
        except SourceUnavailable:
            prev, prev_year = {}, ""
        if not prev:
            return ViewResult("model", "Model Card & Backtest", empty="Backtest needs two CMS vintages.", citations=cites)
        # Score on the prior year only (volume + openness), then measure capture of the following year's growth.
        vp = pctile_rank({n: h["vol"] for n, h in prev.items()})
        op = {}
        for n, h in prev.items():
            tot = sum(h["by_drug"].values()) or 1
            op[n] = 100 * (1 - sum((v / tot) ** 2 for v in h["by_drug"].values()))
        s = sorted(prev, key=lambda n: 0.7 * vp[n] + 0.3 * op[n], reverse=True)
        growth = {n: max(0.0, cur.get(n, {}).get("vol", 0.0) - prev[n]["vol"]) for n in prev}
        total = sum(growth.values()) or 1
        k = max(1, len(s) // 10)
        caps = [round(100 * sum(growth[n] for n in s[: k * (i + 1)]) / total, 1) for i in range(10)]
        lift = caps[0] / 10
        chart = charts.line([f"Top {10 * (i + 1)}%" for i in range(10)],
                            [("Model capture %", caps), ("Random", [10.0 * (i + 1) for i in range(10)])])
        return ViewResult("model", "Model Card & Backtest",
                          f"Scored on {prev_year} data only, the top decile captured {caps[0]:.0f}% of {year} class "
                          f"growth (lift {lift:.1f}× vs random).", chart,
                          charts.table(["Component", "Definition", "Source"], [
                              ["Class volume", "Percentile of class Part D claims within specialty", "CMS Part D Prescribers"],
                              ["Growth", "YoY claim change ÷ max(prior, 11), percentile", "CMS Part D Prescribers (2 vintages)"],
                              ["Need", "State crude prevalence (pop-weighted), percentile", "CDC PLACES"],
                              ["Access ease", "State class-average Access Ease Index", "CMS Part D formulary (R1)"],
                              ["Openness", "100 × (1 − HHI of class drug shares at HCP)", "CMS Part D Prescribers"],
                              ["Engagement", "Not active in v1 (Open Payments optional)", "CMS Open Payments"]]),
                          cites, banner="Limitations: Medicare-only, prescriber-level aggregates, no commercial "
                                        "claims; backtest uses volume+openness only. Prevalence measure: "
                                        + PLACES_MEASURES.get(scope.get("places_measure") or "", ""))

    # ---------------------------------------------------------------- tools
    def t_top_hcps(self, db: Session, scope: dict[str, Any], n: int = 15, **_: Any) -> ToolResult:
        res = score(db, scope, DEFAULT_W)
        lines = [f"D{h['decile']} {h['name']} (NPI {h['npi']}, {h['specialty']}, {h['state']}): class claims "
                 f"{h['vol']:,.0f}; score {h['score']:.1f}" for h in res["hcps"][:n]]
        return ToolResult("top_hcps", f"{len(res['hcps']):,} prescribers scored ({res['year']}).\n" + "\n".join(lines),
                          res["cites"])

    def t_white_space(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_whitespace(db, scope, {})
        return ToolResult("white_space", v.insight + "\n" + v.banner, v.citations)

    def t_analog_ramp(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_ramp(db, scope, {})
        rows = (v.table or {}).get("rows", [])
        return ToolResult("analog_ramp", "Drug | first year | ≥50% | ≥80% | peak | status\n" +
                          "\n".join(" | ".join(map(str, r)) for r in rows), v.citations)

    def t_explain_score(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        res = score(db, scope, DEFAULT_W)
        return ToolResult("explain_score", "Weights in use: " + ", ".join(f"{COMP_LABEL[k]} {v}%" for k, v in
                                                                            res["used"].items()) + ". " + _banner(res),
                          res["cites"])
