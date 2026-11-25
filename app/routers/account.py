"""M1 — forced AI-key onboarding, AI access settings, password change, theme."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.providers import PROVIDER_LABELS, PROVIDERS, ProviderError
from app.core.db import get_db
from app.core.deps import ctx, require_login, templates, verify_csrf
from app.core.security import hash_password, password_strength_ok, verify_password
from app.models import AiKeyStatus, DefaultKeyPolicy, ModelPrice, User, UserApiKey
from app.services import ai_access
from app.services.audit import audit

router = APIRouter()
THEMES = [("clinical-clean", "Clinical Clean", "Beginner · light"), ("executive-slate", "Executive Slate",
          "Intermediate · boardroom"), ("precision-pharma", "Precision Pharma", "Advanced · gradient"),
          ("midnight-analytics", "Midnight Analytics", "Advanced · dark")]


def _ai_ctx(db: Session, user: User) -> dict:
    keys = {k.provider: k for k in db.scalars(select(UserApiKey).where(UserApiKey.user_id == user.id))}
    pols = {p.provider: p for p in db.scalars(select(DefaultKeyPolicy))}
    avail = [p.provider for p in ai_access.default_providers_available(db)]
    models = {p: [m.model for m in db.scalars(select(ModelPrice).where(ModelPrice.provider == p))] for p in PROVIDERS}
    return {"keys": keys, "policies": pols, "default_available": avail, "providers": PROVIDERS,
            "labels": PROVIDER_LABELS, "calls_today": ai_access.calls_today(db, user), "models": models}


@router.get("/onboarding/ai-keys", response_class=HTMLResponse)
def onboarding(request: Request, user: User = Depends(require_login), db: Session = Depends(get_db)):  # noqa: ANN201
    if user.must_change_password:
        return RedirectResponse("/account/password", status_code=303)
    return templates.TemplateResponse(request, "account/onboarding.html", ctx(request, user, **_ai_ctx(db, user)))


@router.post("/onboarding/ai-keys/byok", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def save_key(request: Request, provider: str = Form(...), api_key: str = Form(...), label: str = Form(""),
             back: str = Form("/app"), user: User = Depends(require_login), db: Session = Depends(get_db)):  # noqa: ANN201
    tpl = "account/onboarding.html" if back == "/app" else "account/ai_access.html"
    if provider not in PROVIDERS or len(api_key.strip()) < 12:
        return templates.TemplateResponse(request, tpl, ctx(request, user, error="Choose a provider and paste a key.",
                                                            **_ai_ctx(db, user)), 400)
    try:
        ai_access.save_byok(db, user, provider, api_key, label)
    except ProviderError:
        return templates.TemplateResponse(request, tpl, ctx(request, user, error="Key could not be validated. Nothing "
                                                            "was stored.", **_ai_ctx(db, user)), 400)
    return RedirectResponse("/account/ai?saved=1" if back != "/app" else "/account/ai?welcome=1", status_code=303)


@router.post("/onboarding/ai-keys/default", dependencies=[Depends(verify_csrf)])
def use_default(request: Request, understand: str = Form(""), user: User = Depends(require_login),
                db: Session = Depends(get_db)):  # noqa: ANN201
    if understand != "on" or not ai_access.default_providers_available(db):
        return RedirectResponse("/onboarding/ai-keys?err=default", status_code=303)
    ai_access.accept_default(db, user)
    return RedirectResponse("/app", status_code=303)


@router.get("/account/ai", response_class=HTMLResponse)
def ai_settings(request: Request, user: User = Depends(require_login), db: Session = Depends(get_db)):  # noqa: ANN201
    if user.ai_key_status == AiKeyStatus.none:
        return RedirectResponse("/onboarding/ai-keys", status_code=303)
    return templates.TemplateResponse(request, "account/ai_access.html", ctx(request, user, **_ai_ctx(db, user)))


@router.post("/account/ai/models", dependencies=[Depends(verify_csrf)])
async def save_models(request: Request, user: User = Depends(require_login), db: Session = Depends(get_db)):  # noqa: ANN201
    form = await request.form()
    prefs = dict(user.preferred_models or {})
    for p in PROVIDERS:
        cur = dict(prefs.get(p, {}))
        for task in ai_access.TASKS:
            v = str(form.get(f"{p}_{task}", "")).strip()[:80]
            if v:
                cur[task] = v
        prefs[p] = cur
    prov = str(form.get("preferred_provider", user.preferred_provider))
    if prov in PROVIDERS:
        user.preferred_provider = prov
    user.preferred_models = prefs
    db.commit()
    return RedirectResponse("/account/ai?saved=1", status_code=303)


@router.post("/account/ai/delete", dependencies=[Depends(verify_csrf)])
def delete_key(provider: str = Form(...), user: User = Depends(require_login), db: Session = Depends(get_db)):  # noqa: ANN201
    ai_access.delete_byok(db, user, provider)
    return RedirectResponse("/account/ai" if user.ai_key_status != AiKeyStatus.none else "/onboarding/ai-keys",
                            status_code=303)


@router.get("/account/password", response_class=HTMLResponse)
def pw_form(request: Request, user: User = Depends(require_login)):  # noqa: ANN201
    return templates.TemplateResponse(request, "account/password.html", ctx(request, user))


@router.post("/account/password", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def pw_change(request: Request, current: str = Form(...), new: str = Form(...), user: User = Depends(require_login),
              db: Session = Depends(get_db)):  # noqa: ANN201
    ok, msg = password_strength_ok(new)
    if not verify_password(current, user.password_hash):
        ok, msg = False, "Current password is incorrect."
    if not ok:
        return templates.TemplateResponse(request, "account/password.html", ctx(request, user, error=msg), 400)
    user.password_hash = hash_password(new)
    user.must_change_password = False
    audit(db, user.id, "password_change", "user", user.id)
    db.commit()
    return RedirectResponse("/app", status_code=303)


@router.post("/account/theme", dependencies=[Depends(verify_csrf)])
def set_theme(request: Request, theme: str = Form(...), user: User = Depends(require_login),
              db: Session = Depends(get_db)):  # noqa: ANN201
    if theme in {t[0] for t in THEMES}:
        user.theme = theme
        db.commit()
    from urllib.parse import urlparse
    path = urlparse(request.headers.get("referer", "")).path or "/app"
    return RedirectResponse(path if path.startswith("/") and not path.startswith("//") else "/app", status_code=303)


@router.get("/account/themes", response_class=HTMLResponse)
def themes(request: Request, user: User = Depends(require_login)):  # noqa: ANN201
    return templates.TemplateResponse(request, "account/themes.html", ctx(request, user, themes=THEMES))
