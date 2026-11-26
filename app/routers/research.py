"""R0 — Command Center, scope, hub pages (HTMX views), research agent, pins, projects, briefs, watchlists."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import markdown as md
import nh3
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.providers import ProviderError
from app.core.db import get_db
from app.core.deps import ctx, require_user, templates, user_module_keys, verify_csrf
from app.core.security import limiter
from app.data.connectors import PLACES_MEASURES, STATES
from app.data.fetch import SourceUnavailable
from app.hubs import HUBS, get_hub
from app.hubs.policy import refresh_policy
from app.models import Agent, Brief, Finding, Notification, ResearchProject, User, Watchlist, utcnow
from app.research import agent as research_agent
from app.research import briefs
from app.research.contract import norm_scope
from app.research.scopes import PRESETS
from app.services.ai_access import NeedsModelChoice, NoAiAccess, QuotaExceeded

router = APIRouter()


def render_md(text: str) -> str:
    html = md.markdown(text or "", extensions=["tables", "sane_lists"])
    return nh3.clean(html, tags={"p", "ul", "ol", "li", "strong", "em", "code", "pre", "h1", "h2", "h3", "h4",
                                 "table", "thead", "tbody", "tr", "th", "td", "blockquote", "hr", "br", "a", "sup"},
                     attributes={"a": {"href"}})


templates.env.filters["md"] = render_md
templates.env.globals["timedelta"] = timedelta
templates.env.filters["tojson_attr"] = lambda v: json.dumps(v, default=str)


def _hubs_for(db: Session, user: User) -> list:
    keys = user_module_keys(db, user)
    return [h for h in sorted(HUBS.values(), key=lambda h: h.info.rank) if h.info.key in keys]


def _unread(db: Session, user: User) -> int:
    return len(db.scalars(select(Notification.id).where(Notification.user_id == user.id,
                                                         Notification.read_at.is_(None))).all())


def app_ctx(request: Request, db: Session, user: User, **kw: Any) -> dict[str, Any]:
    return ctx(request, user, nav_hubs=_hubs_for(db, user), unread=_unread(db, user),
               scope=norm_scope(user.scope), module_keys=user_module_keys(db, user), **kw)


# ------------------------------------------------------------------ Command Center
@router.get("/app", response_class=HTMLResponse)
def command_center(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    scope = norm_scope(user.scope)
    tiles = []
    since = utcnow() - timedelta(days=7)
    for h in _hubs_for(db, user):
        try:
            kpis = h.headline(db, scope)
        except SourceUnavailable as exc:
            kpis = []
            err = str(exc)
        else:
            err = ""
        changes = len(h.changes_since(db, scope, since))
        tiles.append({"hub": h, "kpis": kpis, "changes": changes, "error": err})
    projects = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id)
                          .order_by(ResearchProject.updated_at.desc()).limit(5)).all()
    recent = db.scalars(select(Finding).join(ResearchProject).where(ResearchProject.owner_id == user.id)
                        .order_by(Finding.created_at.desc()).limit(6)).all()
    notes = db.scalars(select(Notification).where(Notification.user_id == user.id)
                       .order_by(Notification.created_at.desc()).limit(5)).all()
    return templates.TemplateResponse(request, "app/command_center.html",
                                      app_ctx(request, db, user, tiles=tiles, projects=projects, recent=recent,
                                              notes=notes, presets=PRESETS))


@router.get("/scope", response_class=HTMLResponse)
def scope_form(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    return templates.TemplateResponse(request, "app/scope.html", app_ctx(request, db, user, presets=PRESETS,
                                                                         measures=PLACES_MEASURES, states=STATES))


@router.post("/scope", dependencies=[Depends(verify_csrf)])
async def scope_save(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    form = await request.form()
    preset = str(form.get("preset", ""))
    if preset in PRESETS and form.get("apply_preset"):
        p = PRESETS[preset]
        scope = {"preset": preset, **{k: p[k] for k in ("therapeutic_area", "indication", "drugs", "places_measure")},
                 "states": []}
    else:
        scope = {"therapeutic_area": str(form.get("therapeutic_area", ""))[:80],
                 "indication": str(form.get("indication", ""))[:120],
                 "drugs": [d.strip() for d in str(form.get("drugs", "")).replace("\n", ",").split(",")],
                 "states": [s for s in form.getlist("states") if s in STATES],
                 "places_measure": str(form.get("places_measure", "")) if form.get("places_measure") in PLACES_MEASURES
                 else "", "preset": ""}
    user.scope = norm_scope(scope)
    db.commit()
    nxt = str(form.get("next", "/app"))
    return RedirectResponse(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/app", status_code=303)


# ------------------------------------------------------------------ Hubs
def _hub_or_404(db: Session, user: User, key: str):  # noqa: ANN202
    hub = get_hub(key)
    if not hub:
        raise HTTPException(404)
    if key not in user_module_keys(db, user):
        raise HTTPException(403, detail=f"module:{key}")
    return hub


@router.get("/hubs/{key}", response_class=HTMLResponse)
def hub_page(request: Request, key: str, view: str = "", user: User = Depends(require_user),
             db: Session = Depends(get_db)):  # noqa: ANN201
    hub = _hub_or_404(db, user, key)
    view = view if view in dict(hub.info.views) else hub.info.views[0][0]
    projects = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id,
                                                        ResearchProject.status == "active")).all()
    agents_ = db.scalars(select(Agent).where((Agent.owner_id == user.id) | Agent.is_template.is_(True))).all()
    watching = {w.ref for w in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id))}
    return templates.TemplateResponse(request, "app/hub.html", app_ctx(
        request, db, user, hub=hub, view=view, projects=projects, agents=agents_, watching=watching,
        kpis=_safe_headline(hub, db, norm_scope(user.scope))))


def _safe_headline(hub, db: Session, scope: dict) -> list:  # noqa: ANN001
    try:
        return hub.headline(db, scope)
    except SourceUnavailable:
        return []


@router.get("/hubs/{key}/view/{view_id}", response_class=HTMLResponse)
def hub_view(request: Request, key: str, view_id: str, user: User = Depends(require_user),
             db: Session = Depends(get_db)):  # noqa: ANN201
    hub = _hub_or_404(db, user, key)
    params = dict(request.query_params)
    res = hub.view(db, norm_scope(user.scope), view_id, params)
    projects = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id,
                                                        ResearchProject.status == "active")).all()
    return templates.TemplateResponse(request, "app/_view.html", app_ctx(
        request, db, user, hub=hub, res=res, params=params, projects=projects,
        cites=[c.as_dict() for c in res.citations]))


@router.post("/hubs/{key}/ask", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def hub_ask(request: Request, key: str, question: str = Form(...), agent_id: str = Form(""),
            user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    _hub_or_404(db, user, key)
    if not limiter.allow(f"ask:{user.id}", 20, 60):
        return templates.TemplateResponse(request, "app/_answer.html", ctx(request, user, error="Slow down a little — "
                                                                           "max 20 questions per minute."))
    agent = None
    if agent_id.isdigit():
        agent = db.get(Agent, int(agent_id))
        if agent and not (agent.is_template or agent.owner_id == user.id):
            agent = None
    q = question.strip()[:1000]
    try:
        ans = research_agent.ask(db, user, q, norm_scope(user.scope), sorted(user_module_keys(db, user) - {"agents"}),
                                 primary_hub=key, agent=agent)
    except NeedsModelChoice as exc:
        return templates.TemplateResponse(request, "app/_answer.html", ctx(
            request, user, error=str(exc), quota=True, fix_url="/account/ai#routing", fix_label="Choose a model"))
    except QuotaExceeded as exc:
        return templates.TemplateResponse(request, "app/_answer.html", ctx(request, user, error=str(exc), quota=True))
    except NoAiAccess as exc:
        return templates.TemplateResponse(request, "app/_answer.html", ctx(request, user, error=str(exc), quota=True))
    except ProviderError as exc:
        return templates.TemplateResponse(request, "app/_answer.html", ctx(request, user, error=str(exc)))
    projects = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id,
                                                        ResearchProject.status == "active")).all()
    return templates.TemplateResponse(request, "app/_answer.html", ctx(request, user, ans=ans, question=q, hub_key=key,
                                                                       projects=projects))


@router.post("/pin", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def pin(request: Request, hub_key: str = Form(...), title: str = Form(...), body: str = Form(""),
        citations: str = Form("[]"), chart: str = Form(""), project_id: str = Form(""), new_project: str = Form(""),
        user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    if hub_key not in HUBS:
        raise HTTPException(400)
    proj = None
    if project_id.isdigit():
        proj = db.get(ResearchProject, int(project_id))
        if proj and proj.owner_id != user.id:
            proj = None
    if proj is None:
        proj = ResearchProject(owner_id=user.id, title=(new_project.strip() or "Untitled research")[:160],
                               scope=norm_scope(user.scope))
        db.add(proj)
        db.flush()
    try:
        cites = json.loads(citations)[:20]
        chart_spec = json.loads(chart) if chart else None
    except (ValueError, TypeError):
        cites, chart_spec = [], None
    if not cites:
        return HTMLResponse('<span class="pill pill-warn">A finding needs at least one source — nothing pinned.</span>')
    n = len(proj.findings)
    db.add(Finding(project_id=proj.id, hub_key=hub_key, title=title[:200], body_md=body[:6000], citations=cites,
                   chart_spec=chart_spec if chart_spec and len(json.dumps(chart_spec)) < 200_000 else None,
                   pinned_by=user.id, sort_order=n))
    proj.updated_at = utcnow()
    db.commit()
    return HTMLResponse(f'<span class="pill pill-ok"><i data-lucide="pin"></i> Pinned to '
                        f'<a href="/projects/{proj.id}">{nh3.clean_text(proj.title)}</a></span>')


@router.post("/watch", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def watch(kind: str = Form(...), ref: str = Form(...), user: User = Depends(require_user),
          db: Session = Depends(get_db)):  # noqa: ANN201
    if kind not in ("drug", "policy_topic", "competitor"):
        raise HTTPException(400)
    w = db.scalar(select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.kind == kind, Watchlist.ref == ref))
    if w:
        db.delete(w)
        db.commit()
        return HTMLResponse(f'<i data-lucide="eye"></i> Watch {nh3.clean_text(ref)}')
    db.add(Watchlist(user_id=user.id, kind=kind, ref=ref[:120]))
    db.commit()
    return HTMLResponse(f'<i data-lucide="eye-off"></i> Watching {nh3.clean_text(ref)}')


@router.post("/hubs/policy/refresh", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def policy_refresh(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    _hub_or_404(db, user, "policy")
    if not limiter.allow("policy_refresh", 1, 600) and user.role.value != "admin":
        return HTMLResponse('<span class="pill">Refreshed recently — try again in a few minutes.</span>')
    stats = refresh_policy(db)
    return HTMLResponse(f'<span class="pill pill-ok">Policy feed refreshed: {stats["new"]} new, {stats["changed"]} '
                        f'changed{", some sources unavailable" if stats["errors"] else ""}. Reload views.</span>')


# ------------------------------------------------------------------ Projects & briefs
def _own_project(db: Session, user: User, pid: int) -> ResearchProject:
    p = db.get(ResearchProject, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    return p


@router.get("/projects", response_class=HTMLResponse)
def projects(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    rows = db.scalars(select(ResearchProject).where(ResearchProject.owner_id == user.id)
                      .order_by(ResearchProject.updated_at.desc())).all()
    return templates.TemplateResponse(request, "app/projects.html", app_ctx(request, db, user, rows=rows))


@router.post("/projects", dependencies=[Depends(verify_csrf)])
def project_create(title: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    p = ResearchProject(owner_id=user.id, title=title.strip()[:160] or "Untitled research", scope=norm_scope(user.scope))
    db.add(p)
    db.commit()
    return RedirectResponse(f"/projects/{p.id}", status_code=303)


@router.get("/projects/{pid}", response_class=HTMLResponse)
def project_detail(request: Request, pid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    by_hub: dict[str, list[Finding]] = {h.info.key: [] for h in sorted(HUBS.values(), key=lambda h: h.info.rank)}
    for f in p.findings:
        by_hub.setdefault(f.hub_key, []).append(f)
    bs = db.scalars(select(Brief).where(Brief.project_id == p.id).order_by(Brief.version.desc())).all()
    return templates.TemplateResponse(request, "app/project.html", app_ctx(request, db, user, p=p, by_hub=by_hub,
                                                                           briefs=bs, hubs=HUBS))


@router.post("/projects/{pid}/notes", dependencies=[Depends(verify_csrf)])
def project_notes(pid: int, notes: str = Form(""), user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    p.notes_md = notes[:20000]
    db.commit()
    return RedirectResponse(f"/projects/{pid}", status_code=303)


@router.post("/projects/{pid}/findings/{fid}/delete", dependencies=[Depends(verify_csrf)])
def finding_delete(pid: int, fid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    f = db.get(Finding, fid)
    if f and f.project_id == p.id:
        db.delete(f)
        db.commit()
    return RedirectResponse(f"/projects/{pid}", status_code=303)


@router.post("/projects/{pid}/findings/{fid}/move", dependencies=[Depends(verify_csrf)])
def finding_move(pid: int, fid: int, direction: str = Form("up"), user: User = Depends(require_user),
                 db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    items = list(p.findings)
    idx = next((i for i, f in enumerate(items) if f.id == fid), None)
    if idx is not None:
        j = idx - 1 if direction == "up" else idx + 1
        if 0 <= j < len(items):
            items[idx], items[j] = items[j], items[idx]
            for i, f in enumerate(items):
                f.sort_order = i
            db.commit()
    return RedirectResponse(f"/projects/{pid}", status_code=303)


@router.post("/projects/{pid}/archive", dependencies=[Depends(verify_csrf)])
def project_archive(pid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    p.status = "archived" if p.status == "active" else "active"
    db.commit()
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{pid}/brief", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def brief_generate(request: Request, pid: int, mode: str = Form("ai"), user: User = Depends(require_user),
                   db: Session = Depends(get_db)):  # noqa: ANN201
    p = _own_project(db, user, pid)
    if not p.findings:
        return RedirectResponse(f"/projects/{pid}?err=empty", status_code=303)
    if mode == "manual":
        db.add(Brief(project_id=p.id, version=len(db.scalars(select(Brief.id).where(Brief.project_id == p.id)).all()) + 1,
                     content_md=briefs.manual_brief(p), generated_by_model="(no AI)"))
        db.commit()
        return RedirectResponse(f"/projects/{pid}#briefs", status_code=303)
    try:
        briefs.generate(db, user, p)
    except (QuotaExceeded, NoAiAccess, ProviderError) as exc:
        return RedirectResponse(f"/projects/{pid}?err={type(exc).__name__}", status_code=303)
    return RedirectResponse(f"/projects/{pid}#briefs", status_code=303)


def _own_brief(db: Session, user: User, bid: int) -> tuple[Brief, ResearchProject]:
    b = db.get(Brief, bid)
    if not b:
        raise HTTPException(404)
    return b, _own_project(db, user, b.project_id)


@router.post("/briefs/{bid}/edit", dependencies=[Depends(verify_csrf)])
def brief_edit(bid: int, content: str = Form(...), user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    b, p = _own_brief(db, user, bid)
    b.content_md = content[:60000]
    db.commit()
    return RedirectResponse(f"/briefs/{bid}/print", status_code=303)


@router.get("/briefs/{bid}/edit", response_class=HTMLResponse)
def brief_edit_form(request: Request, bid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    b, p = _own_brief(db, user, bid)
    return templates.TemplateResponse(request, "app/brief_edit.html", app_ctx(request, db, user, b=b, p=p))


@router.get("/briefs/{bid}/print", response_class=HTMLResponse)
def brief_print(request: Request, bid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    b, p = _own_brief(db, user, bid)
    return templates.TemplateResponse(request, "app/brief_print.html", ctx(request, user, b=b, p=p))


@router.get("/briefs/{bid}.md", response_class=PlainTextResponse)
def brief_md(bid: int, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    b, p = _own_brief(db, user, bid)
    fname = "".join(c if c.isalnum() else "-" for c in p.title)[:60] + f"-v{b.version}.md"
    return PlainTextResponse(b.content_md, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ------------------------------------------------------------------ Notifications
@router.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):  # noqa: ANN201
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.created_at.desc()).limit(100)).all()
    watch = db.scalars(select(Watchlist).where(Watchlist.user_id == user.id)).all()
    for n in rows:
        n.read_at = n.read_at or utcnow()
    db.commit()
    return templates.TemplateResponse(request, "app/notifications.html", app_ctx(request, db, user, rows=rows,
                                                                                 watch=watch))
