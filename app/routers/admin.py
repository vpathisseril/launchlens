"""M4 — Admin console: overview, approvals, users & access, modules, AI governance, analytics, data, policy, audit."""
from __future__ import annotations

import csv
import io
import threading
from collections import Counter, defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db, session_factory
from app.core.deps import require_admin, templates, verify_csrf
from app.data import formulary, weekly
from app.hubs.policy import refresh_policy
from app.models import (
    AgentRun,
    AuditLog,
    Brief,
    DataSource,
    DefaultKeyPolicy,
    Finding,
    IngestRun,
    ModelPrice,
    Module,
    NegotiationDrug,
    PolicyItem,
    ResearchProject,
    UsageEvent,
    User,
    UserModule,
    UserStatus,
    utcnow,
)
from app.research import charts
from app.routers.research import app_ctx
from app.services import accounts
from app.services.audit import audit

router = APIRouter(prefix="/admin")


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("", response_class=HTMLResponse)
def overview(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    now = utcnow()
    users = db.scalars(select(User)).all()
    pending = [u for u in users if u.status == UserStatus.pending]
    oldest = min((u.requested_at for u in pending), default=None)
    active7 = sum(1 for u in users if u.last_login_at and u.last_login_at > now - timedelta(days=7))
    active30 = sum(1 for u in users if u.last_login_at and u.last_login_at > now - timedelta(days=30))
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ev = db.scalars(select(UsageEvent).where(UsageEvent.at >= month)).all()
    cost_b = sum(e.est_cost_usd for e in ev if e.key_type == "byok")
    cost_d = sum(e.est_cost_usd for e in ev if e.key_type == "default")
    runs_today = sum(1 for e in ev if e.at >= now.replace(hour=0, minute=0, second=0))
    sources = db.scalars(select(DataSource)).all()
    stale = [s for s in sources if s.status in ("failed", "stale")
             or weekly.freshness(s) == "Overdue"]
    funnel = [("Signed up", len(users)), ("Approved", sum(1 for u in users if u.status == UserStatus.approved)),
              ("Onboarded AI", sum(1 for u in users if u.ai_key_status.value != "none")),
              ("Created project", len({p.owner_id for p in db.scalars(select(ResearchProject))})),
              ("Pinned finding", len({f.pinned_by for f in db.scalars(select(Finding))})),
              ("Generated brief", db.scalar(select(func.count(func.distinct(Brief.project_id)))) or 0)]
    days = [(now - timedelta(days=i)).date() for i in range(29, -1, -1)]
    by_day = Counter(e.at.date() for e in db.scalars(select(UsageEvent).where(UsageEvent.at >= now - timedelta(days=30))))
    trend = charts.line([d.strftime("%m-%d") for d in days], [("AI calls", [by_day.get(d, 0) for d in days])])
    fun = charts.bar([f for f, _ in funnel], [("Users", [n for _, n in funnel])])
    return templates.TemplateResponse(request, "admin/overview.html", app_ctx(
        request, db, admin, users=users, pending=pending, oldest=oldest, active7=active7, active30=active30,
        cost_b=cost_b, cost_d=cost_d, runs_today=runs_today, stale=stale, trend=trend, funnel=fun,
        quota_hits=sum(1 for e in ev if e.status == "quota"), mode=accounts.approval_mode(db)))


# ------------------------------------------------------------------ approvals
@router.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    accounts.auto_approve_due(db)
    pend = db.scalars(select(User).where(User.status == UserStatus.pending).order_by(User.requested_at)).all()
    window = accounts.approval_window_hours(db)
    rows = [{"u": u, "left_h": round((u.requested_at + timedelta(hours=window) - utcnow()).total_seconds() / 3600, 1)}
            for u in pend]
    return templates.TemplateResponse(request, "admin/approvals.html", app_ctx(
        request, db, admin, rows=rows, mode=accounts.approval_mode(db), window=window, modes=accounts.APPROVAL_MODES))


@router.post("/approvals/mode", dependencies=[Depends(verify_csrf)])
def set_mode(request: Request, mode: str = Form(...), window: int = Form(24), admin: User = Depends(require_admin),
             db: Session = Depends(get_db)):  # noqa: ANN201
    if mode not in accounts.APPROVAL_MODES:
        raise HTTPException(400)
    before = {"mode": accounts.approval_mode(db), "window": accounts.approval_window_hours(db)}
    accounts.set_setting(db, "approval_mode", mode)
    accounts.set_setting(db, "approval_window_hours", max(1, min(168, window)))
    audit(db, admin.id, "approval_mode", "settings", "approval", before, {"mode": mode, "window": window}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/approvals", status_code=303)


@router.post("/approvals/decide", dependencies=[Depends(verify_csrf)])
async def decide(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    form = await request.form()
    action, reason = str(form.get("action")), str(form.get("reason", ""))[:200]
    for uid in form.getlist("uid"):
        u = db.get(User, int(uid)) if str(uid).isdigit() else None
        if not u or u.status != UserStatus.pending:
            continue
        if action == "approve":
            accounts.approve(db, u, admin.id, "admin")
        elif action == "reject":
            accounts.reject(db, u, admin.id, reason)
    db.commit()
    return RedirectResponse("/admin/approvals", status_code=303)


# ------------------------------------------------------------------ users & access
@router.get("/users", response_class=HTMLResponse)
def users(request: Request, q: str = "", admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    stmt = select(User).order_by(User.requested_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(User.email).like(like) | func.lower(User.name).like(like))
    rows = db.scalars(stmt.limit(200)).all()
    mods = db.scalars(select(Module).order_by(Module.sort_order)).all()
    grants = defaultdict(set)
    for um in db.scalars(select(UserModule)):
        grants[um.user_id].add(um.module_id)
    return templates.TemplateResponse(request, "admin/users.html", app_ctx(request, db, admin, rows=rows, mods=mods,
                                                                           grants=grants, q=q))


@router.post("/users/{uid}/modules", dependencies=[Depends(verify_csrf)])
async def user_modules(request: Request, uid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404)
    form = await request.form()
    want = {int(x) for x in form.getlist("module") if str(x).isdigit()}
    have = {um.module_id: um for um in u.modules}
    before = sorted(have)
    for mid in set(have) - want:
        db.delete(have[mid])
    for mid in want - set(have):
        db.add(UserModule(user_id=u.id, module_id=mid, granted_by=admin.id))
    audit(db, admin.id, "modules_changed", "user", u.id, {"modules": before}, {"modules": sorted(want)}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{uid}/status", dependencies=[Depends(verify_csrf)])
def user_status(request: Request, uid: int, status: str = Form(...), admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):  # noqa: ANN201
    u = db.get(User, uid)
    if not u or u.id == admin.id or status not in ("approved", "suspended"):
        raise HTTPException(400)
    before = {"status": u.status.value}
    u.status = UserStatus(status)
    audit(db, admin.id, "status_changed", "user", u.id, before, {"status": status}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)


# ------------------------------------------------------------------ modules
@router.get("/modules", response_class=HTMLResponse)
def modules(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    rows = db.scalars(select(Module).order_by(Module.sort_order)).all()
    return templates.TemplateResponse(request, "admin/modules.html", app_ctx(request, db, admin, rows=rows))


@router.post("/modules/{mid}", dependencies=[Depends(verify_csrf)])
def module_save(request: Request, mid: int, name: str = Form(...), description: str = Form(""), icon: str = Form("box"),
                sort_order: int = Form(0), is_active: str = Form(""), is_default: str = Form(""),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    m = db.get(Module, mid)
    if not m:
        raise HTTPException(404)
    before = {"name": m.name, "active": m.is_active, "default": m.is_default_on_approval}
    m.name, m.description, m.icon, m.sort_order = name[:80], description[:255], icon[:40], sort_order
    m.is_active, m.is_default_on_approval = is_active == "on", is_default == "on"
    audit(db, admin.id, "module_changed", "module", m.key, before,
          {"name": m.name, "active": m.is_active, "default": m.is_default_on_approval}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/modules", status_code=303)


# ------------------------------------------------------------------ AI governance
@router.get("/ai", response_class=HTMLResponse)
def ai_gov(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    pols = db.scalars(select(DefaultKeyPolicy)).all()
    prices = db.scalars(select(ModelPrice).order_by(ModelPrice.provider, ModelPrice.tier)).all()
    month = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spend = defaultdict(float)
    for e in db.scalars(select(UsageEvent).where(UsageEvent.at >= month)):
        spend[e.user_id] += e.est_cost_usd
    top = sorted(spend.items(), key=lambda kv: -kv[1])[:10]
    names = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_([k for k, _ in top])))} if top else {}
    from app.agents.providers import PROVIDER_LABELS
    from app.services.ai_access import PROFILES, default_key_for
    state = {}
    for p in pols:
        missing = [m for m, ok in (("secret DEFAULT_" + p.provider.upper() + "_KEY", bool(default_key_for(p.provider))),
                                   ("at least one allowlisted model", bool(p.model_allowlist)),
                                   ("Enabled checkbox", p.is_enabled)) if not ok]
        state[p.provider] = ("Active", "") if not missing else ("Inactive", "Needs " + ", ".join(missing) + ".")
    return templates.TemplateResponse(request, "admin/ai.html", app_ctx(
        request, db, admin, pols=pols, prices=prices, top=[(names.get(k, k), v) for k, v in top],
        has_key={p.provider: bool(default_key_for(p.provider)) for p in pols}, state=state,
        labels=PROVIDER_LABELS, profiles=PROFILES))


@router.post("/ai/policy/{pid}", dependencies=[Depends(verify_csrf)])
def policy_save(request: Request, pid: int, allowlist: str = Form(""), max_tokens: int = Form(800),
                calls_per_day: int = Form(20), budget: float = Form(25.0), enabled: str = Form(""),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    p = db.get(DefaultKeyPolicy, pid)
    if not p:
        raise HTTPException(404)
    before = {"allow": p.model_allowlist, "max_tokens": p.max_tokens_per_call, "cpd": p.calls_per_day,
              "budget": p.monthly_budget_usd, "enabled": p.is_enabled}
    allow = [m.strip() for m in allowlist.split(",") if m.strip()][:10]
    if enabled == "on" and not allow:
        return RedirectResponse(f"/admin/ai?err=allowlist&provider={p.provider}", status_code=303)
    p.model_allowlist = allow
    p.max_tokens_per_call = max(50, min(4000, max_tokens))
    p.calls_per_day = max(0, min(1000, calls_per_day))
    p.monthly_budget_usd = max(0.0, budget)
    p.is_enabled = enabled == "on"
    audit(db, admin.id, "default_policy_changed", "default_key_policy", p.provider, before,
          {"allow": p.model_allowlist, "max_tokens": p.max_tokens_per_call, "cpd": p.calls_per_day,
           "budget": p.monthly_budget_usd, "enabled": p.is_enabled}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/ai", status_code=303)


@router.post("/ai/price", dependencies=[Depends(verify_csrf)])
def price_save(request: Request, provider: str = Form(...), model: str = Form(...), tier: str = Form("standard"),
               usd_in: float = Form(0.0), usd_out: float = Form(0.0), admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):  # noqa: ANN201
    if provider not in ("anthropic", "openai", "google") or tier not in ("economy", "standard", "premium"):
        raise HTTPException(400)
    m = db.scalar(select(ModelPrice).where(ModelPrice.provider == provider, ModelPrice.model == model.strip()))
    if not m:
        m = ModelPrice(provider=provider, model=model.strip()[:80])
        db.add(m)
    m.tier, m.usd_per_mtok_in, m.usd_per_mtok_out, m.updated_at = tier, max(0, usd_in), max(0, usd_out), utcnow()
    audit(db, admin.id, "model_price_saved", "model_price", f"{provider}/{model}", None,
          {"tier": tier, "in": usd_in, "out": usd_out}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/ai", status_code=303)


# ------------------------------------------------------------------ usage analytics & hub health
@router.get("/usage", response_class=HTMLResponse)
def usage(request: Request, days: int = 30, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    days = days if days in (7, 30, 90) else 30
    since = utcnow() - timedelta(days=days)
    ev = db.scalars(select(UsageEvent).where(UsageEvent.at >= since)).all()
    by_model = defaultdict(float)
    by_hub = Counter()
    lat = sorted(e.latency_ms for e in ev if e.latency_ms)
    for e in ev:
        by_model[f"{e.provider}/{e.model}"] += e.est_cost_usd
        by_hub[e.hub_key or "other"] += 1
    runs = db.scalars(select(AgentRun).where(AgentRun.at >= since)).all()
    hub_health = []
    for hk in ("access", "pricing", "policy", "targeting", "differentiation"):
        r = [x for x in runs if x.hub_key == hk]
        rated = [x.rating for x in r if x.rating]
        hub_health.append([hk, len(r), f"{100 * sum(x.verified for x in r) / len(r):.0f}%" if r else "—",
                           f"{sum(rated) / len(rated):.1f}" if rated else "—"])
    p50 = lat[len(lat) // 2] if lat else 0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0
    err = sum(1 for e in ev if e.status == "error")
    return templates.TemplateResponse(request, "admin/usage.html", app_ctx(
        request, db, admin, days=days, n=len(ev), p50=p50, p95=p95, err_rate=100 * err / len(ev) if ev else 0,
        cost_chart=charts.bar(list(by_model), [("USD", [round(v, 4) for v in by_model.values()])], horizontal=True),
        hub_chart=charts.bar(list(by_hub), [("Calls", list(by_hub.values()))]), hub_health=hub_health,
        byok=sum(1 for e in ev if e.key_type == "byok"), default=sum(1 for e in ev if e.key_type == "default")))


# ------------------------------------------------------------------ data sources
def _bg(fn, *args) -> None:  # noqa: ANN001
    def run() -> None:
        with session_factory()() as s:
            fn(s, *args)
    threading.Thread(target=run, daemon=True).start()


@router.get("/data", response_class=HTMLResponse)
def data(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    rows = db.scalars(select(DataSource).order_by(DataSource.hub_key, DataSource.key)).all()
    runs = db.scalars(select(IngestRun).order_by(IngestRun.started_at.desc()).limit(10)).all()
    wk = weekly.last_run(db)
    return templates.TemplateResponse(request, "admin/data.html", app_ctx(
        request, db, admin, rows=rows, runs=runs, vintages=formulary.latest_vintage(db), weekly_run=wk,
        fresh={s.key: weekly.freshness(s) for s in rows}, overdue=weekly.overdue_count(db),
        running=weekly.runs_in_progress(db),
        neg=db.scalars(select(NegotiationDrug).order_by(NegotiationDrug.ipay, NegotiationDrug.drug)).all(),
        policy_n=db.scalar(select(func.count()).select_from(PolicyItem)) or 0))


@router.post("/data/refresh-policy", dependencies=[Depends(verify_csrf)])
def data_refresh_policy(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    audit(db, admin.id, "policy_refresh", "data_source", "federal_register", ip=_ip(request))
    db.commit()
    _bg(refresh_policy)
    return RedirectResponse("/admin/data?queued=policy", status_code=303)


@router.post("/data/weekly-refresh", dependencies=[Depends(verify_csrf)])
def data_weekly_refresh(request: Request, mode: str = Form("due"), admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)):  # noqa: ANN201
    if weekly.runs_in_progress(db):
        return RedirectResponse("/admin/data?queued=already-running", status_code=303)
    mode = "full" if mode == "full" else "due"
    audit(db, admin.id, "weekly_refresh_started", "data_source", "all", None, {"mode": mode}, _ip(request))
    db.commit()
    _bg(weekly.run, mode)
    return RedirectResponse("/admin/data?queued=weekly-refresh", status_code=303)


@router.post("/data/formulary", dependencies=[Depends(verify_csrf)])
def data_formulary(request: Request, url: str = Form(...), drugs: str = Form(...), vintage: str = Form(...),
                   admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    if not url.startswith("https://") or not any(h in url for h in ("cms.gov", "data.cms.gov")):
        raise HTTPException(400, detail="Formulary file must come from an official cms.gov URL.")
    dl = [d.strip() for d in drugs.split(",") if d.strip()][:20]
    audit(db, admin.id, "formulary_ingest", "data_source", "partd_formulary", None,
          {"url": url, "drugs": dl, "vintage": vintage}, _ip(request))
    db.commit()
    _bg(formulary.run, url, dl, vintage[:40])
    return RedirectResponse("/admin/data?queued=formulary", status_code=303)


@router.post("/data/policy-item", dependencies=[Depends(verify_csrf)])
def add_policy_item(request: Request, title: str = Form(...), topic: str = Form(...), status: str = Form(...),
                    status_date: str = Form(...), source_url: str = Form(...), agency: str = Form(""),
                    summary: str = Form(""), admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    if topic not in ("mfn", "tariff", "pbm", "ira", "other") or not source_url.startswith("https://") or \
            not any(source_url.split("/")[2].endswith(d) for d in (".gov", ".mil")):
        raise HTTPException(400, detail="Policy items must cite an official .gov source.")
    try:
        sd = date.fromisoformat(status_date)
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid date") from exc
    ext = f"ADMIN-{int(utcnow().timestamp())}"
    db.add(PolicyItem(external_id=ext, title=title[:500], doc_type="agreement" if topic == "mfn" else "notice",
                      topic=topic, status=status[:20], status_date=sd, agency=agency[:300], abstract=summary[:4000],
                      source_key="negotiation_program" if topic == "ira" else "federal_register",
                      source_url=source_url[:500]))
    audit(db, admin.id, "policy_item_added", "policy_item", ext, None, {"title": title, "url": source_url}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/data", status_code=303)


@router.post("/data/negotiation/{nid}", dependencies=[Depends(verify_csrf)])
def edit_negotiation(request: Request, nid: int, mfp: str = Form(""), list_price: str = Form(""),
                     discount: str = Form(""), status: str = Form(""), source_url: str = Form(""),
                     admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    n = db.get(NegotiationDrug, nid)
    if not n:
        raise HTTPException(404)
    if source_url and not source_url.startswith("https://www.cms.gov"):
        raise HTTPException(400, detail="Negotiation data must cite cms.gov.")
    before = {"mfp": n.mfp_30d, "list": n.list_price_30d, "disc": n.discount_pct, "status": n.status}

    def f(v: str) -> float | None:
        try:
            return float(v.replace(",", "").replace("$", "")) if v.strip() else None
        except ValueError:
            return None
    n.mfp_30d = f(mfp) if mfp.strip() else n.mfp_30d
    n.list_price_30d = f(list_price) if list_price.strip() else n.list_price_30d
    n.discount_pct = f(discount) if discount.strip() else n.discount_pct
    n.status = status[:120] or n.status
    if source_url:
        n.source_url, n.retrieved_at = source_url[:500], utcnow()
    audit(db, admin.id, "negotiation_edited", "negotiation", n.id, before,
          {"mfp": n.mfp_30d, "list": n.list_price_30d, "disc": n.discount_pct, "status": n.status}, _ip(request))
    db.commit()
    return RedirectResponse("/admin/data#negotiation", status_code=303)


# ------------------------------------------------------------------ audit
@router.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request, action: str = "", admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    stmt = select(AuditLog).order_by(AuditLog.at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.scalars(stmt.limit(300)).all()
    actions = sorted(set(db.scalars(select(AuditLog.action).distinct())))
    return templates.TemplateResponse(request, "admin/audit.html", app_ctx(request, db, admin, rows=rows,
                                                                           actions=actions, action=action))


@router.get("/export/{what}.csv")
def export_csv(request: Request, what: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):  # noqa: ANN201
    buf = io.StringIO()
    w = csv.writer(buf)
    if what == "audit":
        w.writerow(["at", "actor_id", "action", "entity", "entity_id", "before", "after", "ip"])
        for r in db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(10000)):
            w.writerow([r.at, r.actor_id, r.action, r.entity, r.entity_id, r.before, r.after, r.ip])
    elif what == "users":
        w.writerow(["id", "email", "name", "org", "title", "status", "role", "requested_at", "approved_at",
                    "approval_source", "ai_key_status", "last_login_at"])
        for u in db.scalars(select(User)):
            w.writerow([u.id, u.email, u.name, u.org, u.title, u.status.value, u.role.value, u.requested_at,
                        u.approved_at, u.approval_source, u.ai_key_status.value, u.last_login_at])
    elif what == "usage":
        w.writerow(["at", "user_id", "hub", "provider", "model", "key_type", "tokens_in", "tokens_out", "est_cost_usd",
                    "latency_ms", "status", "verified"])
        for e in db.scalars(select(UsageEvent).order_by(UsageEvent.at.desc()).limit(50000)):
            w.writerow([e.at, e.user_id, e.hub_key, e.provider, e.model, e.key_type, e.tokens_in, e.tokens_out,
                        e.est_cost_usd, e.latency_ms, e.status, e.verified])
    else:
        raise HTTPException(404)
    audit(db, admin.id, "csv_export", "export", what, ip=_ip(request))
    db.commit()
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="launchlens-{what}.csv"'})
