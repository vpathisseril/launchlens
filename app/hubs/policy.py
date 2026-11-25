"""R3 — Policy Convergence Radar: MFN · Section 232 tariffs · PBM reform · IRA (rank #3)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.data.connectors import federal_register, regulations_documents, trade_imports_hs30
from app.data.fetch import SourceUnavailable, source_name
from app.hubs.base import Hub
from app.models import Notification, PolicyItem, User, Watchlist, utcnow
from app.research import charts
from app.research.contract import Citation, HubInfo, Kpi, ToolResult, ViewResult

TOPIC_QUERIES = {
    "mfn": ['"most favored nation" drug', '"most-favored-nation" pricing'],
    "tariff": ['"section 232" pharmaceutical', 'pharmaceutical imports tariff'],
    "pbm": ['"pharmacy benefit manager"', '"pharmacy benefit managers"'],
    "ira": ['"drug price negotiation program"', '"maximum fair price"'],
}
TOPIC_LABEL = {"mfn": "MFN", "tariff": "Tariffs", "pbm": "PBM", "ira": "IRA"}
CHANNEL_TERMS = {
    "Part D": [r"\bpart d\b", r"prescription drug plan"], "Part B": [r"\bpart b\b", r"physician-administered"],
    "Medicaid": [r"medicaid"], "Commercial": [r"commercial", r"employer", r"erisa", r"group health plan"],
    "Supply chain": [r"import", r"tariff", r"manufactur", r"supply chain", r"active pharmaceutical ingredient"],
}


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _status(doc_type: str, effective: date | None) -> str:
    t = doc_type.lower()
    if "proposed" in t:
        return "Proposed"
    if t == "rule":
        return "In effect" if effective and effective <= date.today() else "Final"
    if "presidential" in t:
        return "In effect"
    return "Notice"


TOPIC_TERMS = {"pbm": [r"pharmacy benefit manag", r"\bpbms?\b"], "tariff": [r"section 232", r"tariff", r"imports of pharm"],
               "mfn": [r"most[- ]favored[- ]nation"], "ira": [r"drug price negotiation", r"maximum fair price"]}


def classify_topic(text: str, default: str) -> str:
    """Topic from the official title/abstract; the search query's topic is only the fallback."""
    low = text.lower()
    for topic, pats in TOPIC_TERMS.items():
        if any(re.search(p, low) for p in pats):
            return topic
    return default


def _channels(text: str) -> list[str]:
    low = text.lower()
    return [ch for ch, pats in CHANNEL_TERMS.items() if any(re.search(p, low) for p in pats)]


def refresh_policy(db: Session) -> dict[str, int]:
    """Pull official documents per topic; insert new, update changed (status/effective/comment dates)."""
    stats = {"new": 0, "changed": 0, "errors": 0}
    changed_items: list[PolicyItem] = []
    for topic, queries in TOPIC_QUERIES.items():
        for q in queries:
            try:
                docs, _ = federal_register(db, q)
            except SourceUnavailable:
                stats["errors"] += 1
                continue
            for d in docs:
                ext = "FR-" + str(d.get("document_number", ""))
                eff = _d(d.get("effective_on"))
                doc_type = str(d.get("type", "Notice"))
                status = _status(doc_type, eff)
                abstract = d.get("abstract") or ""
                row = db.scalar(select(PolicyItem).where(PolicyItem.external_id == ext))
                agencies = ", ".join(a.get("name", "") for a in d.get("agencies") or [] if a.get("name"))
                if row is None:
                    row = PolicyItem(external_id=ext, title=str(d.get("title", ""))[:500],
                                     doc_type=doc_type.lower().replace(" ", "_"),
                                     topic=classify_topic(f"{d.get('title', '')} {abstract}", topic), status=status,
                                     status_date=_d(d.get("publication_date")), effective_date=eff,
                                     comments_close=_d(d.get("comments_close_on")), agency=agencies[:300],
                                     abstract=abstract, affected_channels=_channels(f"{d.get('title', '')} {abstract}"),
                                     source_key="federal_register", source_url=str(d.get("html_url", "")))
                    db.add(row)
                    stats["new"] += 1
                    changed_items.append(row)
                elif (row.status, row.effective_date, row.comments_close) != (status, eff, _d(d.get("comments_close_on"))):
                    row.status, row.effective_date = status, eff
                    row.comments_close = _d(d.get("comments_close_on"))
                    row.last_changed_at = utcnow()
                    stats["changed"] += 1
                    changed_items.append(row)
                row.retrieved_at = utcnow()
    for topic in TOPIC_QUERIES:
        try:
            docs, _ = regulations_documents(db, TOPIC_QUERIES[topic][0].replace('"', ""))
        except SourceUnavailable:
            continue
        for d in docs:
            a = d.get("attributes", {})
            ext = "REG-" + str(d.get("id", ""))
            if db.scalar(select(PolicyItem.id).where(PolicyItem.external_id == ext)):
                continue
            row = PolicyItem(external_id=ext, title=str(a.get("title", ""))[:500],
                             doc_type=str(a.get("documentType", "notice")).lower().replace(" ", "_"), topic=topic,
                             status="Proposed" if "proposed" in str(a.get("documentType", "")).lower() else "Notice",
                             status_date=_d(str(a.get("postedDate", ""))[:10]),
                             comments_close=_d(str(a.get("commentEndDate") or "")[:10]),
                             agency=str(a.get("agencyId", "")), affected_channels=_channels(str(a.get("title", ""))),
                             source_key="regulations", source_url=f"https://www.regulations.gov/document/{d.get('id')}")
            db.add(row)
            stats["new"] += 1
            changed_items.append(row)
    db.flush()
    _notify(db, changed_items)
    db.commit()
    return stats


def _notify(db: Session, items: list[PolicyItem]) -> None:
    if not items:
        return
    for w in db.scalars(select(Watchlist).where(Watchlist.kind.in_(["policy_topic", "drug"]))):
        user = db.get(User, w.user_id)
        if not user:
            continue
        for it in items:
            hit = (w.kind == "policy_topic" and it.topic == w.ref) or \
                  (w.kind == "drug" and w.ref.lower() in f"{it.title} {it.abstract}".lower())
            if hit:
                db.add(Notification(user_id=w.user_id, title=f"Policy update: {it.title[:150]}",
                                    body=f"{TOPIC_LABEL.get(it.topic, it.topic)} · {it.status}",
                                    url=f"/hubs/policy?view=week&item={it.id}"))


def _pcite(db: Session, i: PolicyItem) -> Citation:
    return Citation(i.source_key, source_name(db, i.source_key), i.status_date.isoformat() if i.status_date else "",
                    i.source_url, Citation.now_iso(i.retrieved_at), i.external_id)


def _scope_terms(scope: dict[str, Any]) -> list[str]:
    terms = [d.lower() for d in scope.get("drugs", [])]
    if scope.get("therapeutic_area"):
        terms.append(scope["therapeutic_area"].lower())
    return terms


class PolicyHub(Hub):
    info = HubInfo(
        key="policy", title="Policy Convergence Radar", short="Policy", icon="radar", rank=3,
        question="Which federal actions on MFN pricing, tariffs, PBMs and negotiation affect this market, and when?",
        views=[("radar", "Radar"), ("timeline", "Deadlines & Effective Dates"), ("exposure", "Exposure Matrix"),
               ("trade", "Trade Context"), ("week", "This Week")],
        tools={"search_policy": "Search official policy items by topic/status/keyword",
               "exposure": "Relevance of policy items to the scope's channels and drugs",
               "deadlines": "Comment deadlines and effective dates in the next 90 days",
               "trade": "US pharmaceutical (HS 30) imports by source country"},
    )
    scope_free_views = ("radar", "timeline", "trade", "week", "exposure")

    def headline(self, db: Session, scope: dict[str, Any]) -> list[Kpi]:
        items = db.scalars(select(PolicyItem)).all()
        if not items:
            return [Kpi("Active policy items", "—", hint="Refresh the policy feed")]
        soon = [i for i in items if (i.comments_close and date.today() <= i.comments_close <= date.today() +
                                     timedelta(days=30)) or (i.effective_date and date.today() <= i.effective_date <=
                                                             date.today() + timedelta(days=30))]
        by = {s: sum(1 for i in items if i.status == s) for s in ("Proposed", "Final", "In effect")}
        return [Kpi("Policy items tracked", f"{len(items)}", float(len(items)),
                    delta=f"{len(soon)} deadlines ≤30d", delta_dir="flat",
                    hint=f"Proposed {by['Proposed']} · Final {by['Final']} · In effect {by['In effect']}")]

    def changes_since(self, db: Session, scope: dict[str, Any], since: datetime) -> list[dict[str, Any]]:
        rows = db.scalars(select(PolicyItem).where(or_(PolicyItem.first_seen_at >= since,
                                                       PolicyItem.last_changed_at >= since))).all()
        return [{"title": r.title, "status": r.status, "id": r.id} for r in rows]

    def v_radar(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        items = db.scalars(select(PolicyItem).order_by(PolicyItem.status_date.desc())).all()
        if not items:
            return ViewResult("radar", "Radar", empty="No policy items yet — click 'Refresh policy feed'.")
        pts = [{"ring": i.status if i.status in ("In effect", "Final", "Proposed") else "Notice",
                "sector": TOPIC_LABEL.get(i.topic, "IRA"), "title": i.title[:90]} for i in items[:120]]
        counts = {t: sum(1 for i in items if i.topic == t) for t in TOPIC_LABEL}
        return ViewResult("radar", "Radar", "Rings: In effect (center) → Final → Proposed → Notice. "
                          + " · ".join(f"{TOPIC_LABEL[t]} {n}" for t, n in counts.items()),
                          charts.policy_radar(pts), citations=[_pcite(db, i) for i in items[:10]])

    def v_timeline(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        today = date.today()
        items = db.scalars(select(PolicyItem)).all()
        ev = []
        for i in items:
            if i.comments_close and i.comments_close >= today - timedelta(days=7):
                ev.append((i.comments_close, "Comments close", i))
            if i.effective_date and i.effective_date >= today - timedelta(days=7):
                ev.append((i.effective_date, "Effective", i))
        ev.sort(key=lambda e: e[0])
        if not ev:
            return ViewResult("timeline", "Deadlines & Effective Dates", empty="No upcoming deadlines found.")
        rows = [[d.isoformat(), kind, TOPIC_LABEL.get(i.topic, i.topic), i.title[:120], i.status] for d, kind, i in ev[:60]]

        def yr(d: date) -> float:
            return d.year + (d.timetuple().tm_yday - 1) / 365.25
        bars = [(i.title[:50], yr(i.status_date or d), yr(d) + 0.01, kind) for d, kind, i in ev[:20]]
        return ViewResult("timeline", "Deadlines & Effective Dates", f"{len(ev)} upcoming dates.",
                          charts.timeline_bars(bars), charts.table(["Date", "Milestone", "Topic", "Item", "Status"], rows),
                          [_pcite(db, i) for _, _, i in ev[:12]])

    def v_exposure(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        items = db.scalars(select(PolicyItem).order_by(PolicyItem.status_date.desc())).all()[:60]
        if not items:
            return ViewResult("exposure", "Exposure Matrix", empty="No policy items yet.")
        cols = list(CHANNEL_TERMS)
        terms = _scope_terms(scope)
        rows = []
        for i in items:
            text = f"{i.title} {i.abstract}".lower()
            direct = [t for t in terms if t and t in text]
            rows.append([i.title[:100], TOPIC_LABEL.get(i.topic, i.topic), i.status,
                         *["●" if c in (i.affected_channels or []) else "" for c in cols],
                         ("Direct: " + ", ".join(direct)) if direct else ("Indirect" if i.affected_channels else "—")])
        return ViewResult("exposure", "Exposure Matrix",
                          "Channel tags are keyword-derived from the official title/abstract; 'Direct' means a scope "
                          "drug or therapeutic area is named. Always confirm in the source document.",
                          table=charts.table(["Item", "Topic", "Status", *cols, "Scope relevance"], rows),
                          citations=[_pcite(db, i) for i in items[:12]])

    def v_trade(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        year = int(params.get("year") or date.today().year - 1)
        rows, cite = trade_imports_hs30(db, year)
        if not rows:
            rows, cite = trade_imports_hs30(db, year - 1)
            year -= 1
        top = rows[:12]
        total = sum(r["value"] for r in rows)
        return ViewResult("trade", f"Trade Context · HS 30 imports {year}",
                          f"Top 3 source countries account for {100 * sum(r['value'] for r in top[:3]) / total:.0f}% "
                          f"of {charts.fmt_usd(total)} in pharmaceutical (HS chapter 30) imports." if total else "",
                          charts.bar([r["country"] for r in top][::-1], [("Imports ($B)",
                                     [round(r["value"] / 1e9, 2) for r in top][::-1])], horizontal=True, unit="$B"),
                          charts.table(["Country", "Imports"], [[r["country"], charts.fmt_usd(r["value"])] for r in top],
                                       [1]), [cite],
                          banner="Context for tariff exposure only; product-level origin is not public.")

    def v_week(self, db: Session, scope: dict[str, Any], params: dict[str, Any]) -> ViewResult:
        since = utcnow() - timedelta(days=7)
        rows = db.scalars(select(PolicyItem).where(or_(PolicyItem.first_seen_at >= since,
                                                       PolicyItem.last_changed_at >= since))
                          .order_by(PolicyItem.last_changed_at.desc())).all()
        if not rows:
            return ViewResult("week", "This Week", empty="No new or changed items in the last 7 days.")
        t = [[i.last_changed_at.strftime("%Y-%m-%d"), "New" if i.first_seen_at >= since else "Changed",
              TOPIC_LABEL.get(i.topic, i.topic), i.title[:120], i.status] for i in rows]
        return ViewResult("week", "This Week", f"{len(rows)} new or changed items.",
                          table=charts.table(["Seen", "Change", "Topic", "Item", "Status"], t),
                          citations=[_pcite(db, i) for i in rows[:12]])

    # ---------------------------------------------------------------- tools
    def t_search_policy(self, db: Session, scope: dict[str, Any], topic: str = "", q: str = "", **_: Any) -> ToolResult:
        stmt = select(PolicyItem).order_by(PolicyItem.status_date.desc())
        if topic in TOPIC_LABEL:
            stmt = stmt.where(PolicyItem.topic == topic)
        items = [i for i in db.scalars(stmt) if not q or q.lower() in f"{i.title} {i.abstract}".lower()][:12]
        lines = [f"[{TOPIC_LABEL.get(i.topic)}] {i.title} — status {i.status} as of "
                 f"{i.status_date}; effective {i.effective_date or 'n/a'}; agency {i.agency}. Abstract: "
                 f"{(i.abstract or '')[:300]}" for i in items]
        return ToolResult("search_policy", "\n".join(lines) or "No matching official items.",
                          [_pcite(db, i) for i in items])

    def t_exposure(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_exposure(db, scope, {})
        rows = (v.table or {}).get("rows", [])[:15]
        return ToolResult("exposure", "Item | Topic | Status | channels... | relevance\n" +
                          "\n".join(" | ".join(map(str, r)) for r in rows), v.citations)

    def t_deadlines(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_timeline(db, scope, {})
        rows = (v.table or {}).get("rows", [])[:15]
        return ToolResult("deadlines", "\n".join(" | ".join(map(str, r)) for r in rows) or v.empty, v.citations)

    def t_trade(self, db: Session, scope: dict[str, Any], **_: Any) -> ToolResult:
        v = self.v_trade(db, scope, {})
        rows = (v.table or {}).get("rows", [])
        return ToolResult("trade", v.insight + "\n" + "\n".join(f"{a}: {b}" for a, b in rows), v.citations)
