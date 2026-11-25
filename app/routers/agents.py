"""M2 — Agent Builder: clone templates, configure persona/model/tone/tools, test with the guardrailed runtime."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.providers import PROVIDER_LABELS, PROVIDERS
from app.core.db import get_db
from app.core.deps import require_module, templates, user_module_keys, verify_csrf
from app.models import Agent, AgentRun, DefaultKeyPolicy, ModelPrice, User, UserApiKey
from app.research.agent import FORMAT, GUARDRAIL, TONE, available_tools
from app.routers.research import app_ctx

router = APIRouter(prefix="/agents")


def _own(db: Session, user: User, aid: int) -> Agent:
    a = db.get(Agent, aid)
    if not a or (a.owner_id != user.id):
        raise HTTPException(404)
    return a


def _model_choices(db: Session, user: User) -> dict[str, list[str]]:
    byok = {k.provider for k in db.scalars(select(UserApiKey).where(UserApiKey.user_id == user.id,
                                                                    UserApiKey.is_valid.is_(True)))}
    out: dict[str, list[str]] = {}
    for p in PROVIDERS:
        if p in byok:
            avail = (user.preferred_models or {}).get("available_" + p) or []
            reg = [m.model for m in db.scalars(select(ModelPrice).where(ModelPrice.provider == p))]
            out[p] = sorted(set(avail) | set(reg))
        else:
            pol = db.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == p))
            if pol and pol.is_enabled and pol.model_allowlist:
                out[p] = list(pol.model_allowlist)
    return out


@router.get("", response_class=HTMLResponse)
def agents_home(request: Request, user: User = Depends(require_module("agents")), db: Session = Depends(get_db)):  # noqa: ANN201
    tpls = db.scalars(select(Agent).where(Agent.is_template.is_(True))).all()
    mine = db.scalars(select(Agent).where(Agent.owner_id == user.id).order_by(Agent.updated_at.desc())).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.at.desc()).limit(10)).all()
    return templates.TemplateResponse(request, "agents/index.html", app_ctx(request, db, user, tpls=tpls, mine=mine,
                                                                            runs=runs))


@router.post("/clone/{aid}", dependencies=[Depends(verify_csrf)])
def clone(aid: int, user: User = Depends(require_module("agents")), db: Session = Depends(get_db)):  # noqa: ANN201
    src = db.get(Agent, aid)
    if not src or not (src.is_template or src.owner_id == user.id):
        raise HTTPException(404)
    a = Agent(owner_id=user.id, name=f"My {src.name}"[:80], icon=src.icon, hub_key=src.hub_key, persona=src.persona,
              goal=src.goal, provider=user.preferred_provider or src.provider, model="", tone=src.tone,
              output_format=src.output_format, tools=list(src.tools), temperature=src.temperature,
              max_tokens=src.max_tokens)
    db.add(a)
    db.commit()
    return RedirectResponse(f"/agents/{a.id}", status_code=303)


@router.get("/{aid}", response_class=HTMLResponse)
def edit(request: Request, aid: int, user: User = Depends(require_module("agents")), db: Session = Depends(get_db)):  # noqa: ANN201
    a = _own(db, user, aid)
    hubs = sorted(user_module_keys(db, user) - {"agents"})
    tools = available_tools(hubs)
    eff = GUARDRAIL + f"\n\nPersona: {a.persona}\nGoal: {a.goal}\nTone: {TONE.get(a.tone, '')}\n" \
                      f"Format: {FORMAT.get(a.output_format, '')}"
    return templates.TemplateResponse(request, "agents/edit.html", app_ctx(
        request, db, user, a=a, tools=tools, models=_model_choices(db, user), labels=PROVIDER_LABELS, tones=TONE,
        formats=FORMAT, effective=eff, est_tokens=len(eff) // 4 + 1200))


@router.post("/{aid}", dependencies=[Depends(verify_csrf)])
async def save(request: Request, aid: int, user: User = Depends(require_module("agents")),
               db: Session = Depends(get_db)):  # noqa: ANN201
    a = _own(db, user, aid)
    f = await request.form()
    a.name = str(f.get("name", a.name))[:80]
    a.persona = str(f.get("persona", ""))[:2000]
    a.goal = str(f.get("goal", ""))[:1000]
    a.tone = str(f.get("tone")) if f.get("tone") in TONE else a.tone
    a.output_format = str(f.get("output_format")) if f.get("output_format") in FORMAT else a.output_format
    prov = str(f.get("provider", a.provider))
    a.provider = prov if prov in PROVIDERS else a.provider
    allowed = _model_choices(db, user).get(a.provider, [])
    m = str(f.get("model", ""))
    a.model = m if m in allowed else ""
    try:
        a.temperature = max(0.0, min(1.0, float(str(f.get("temperature", a.temperature)))))
        a.max_tokens = max(100, min(4000, int(str(f.get("max_tokens", a.max_tokens)))))
    except ValueError:
        pass
    hubs = sorted(user_module_keys(db, user) - {"agents"})
    valid = set(available_tools(hubs))
    a.tools = [t for t in f.getlist("tools") if t in valid]
    hk = str(f.get("hub_key", ""))
    a.hub_key = hk if hk in hubs else ""
    db.commit()
    return RedirectResponse(f"/agents/{aid}?saved=1", status_code=303)


@router.post("/{aid}/delete", dependencies=[Depends(verify_csrf)])
def delete(aid: int, user: User = Depends(require_module("agents")), db: Session = Depends(get_db)):  # noqa: ANN201
    db.delete(_own(db, user, aid))
    db.commit()
    return RedirectResponse("/agents", status_code=303)
