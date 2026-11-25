"""M0 — landing, signup, pending, login/logout, password reset."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import ctx, optional_user, templates, verify_csrf
from app.core.security import limiter, password_strength_ok, verify_password
from app.hubs import HUBS
from app.models import User, UserStatus, utcnow
from app.services import accounts
from app.services.audit import audit

router = APIRouter()
USE_CASES = ["Launch strategy", "Market access & pricing", "Commercial analytics", "Competitive intelligence",
             "Policy / government affairs", "Consulting", "Learning / portfolio review", "Other"]


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, user: User | None = Depends(optional_user)):  # noqa: ANN201
    return templates.TemplateResponse(request, "public/landing.html",
                                      ctx(request, user, hubs=sorted(HUBS.values(), key=lambda h: h.info.rank)))


@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):  # noqa: ANN201
    return templates.TemplateResponse(request, "public/signup.html", ctx(request, use_cases=USE_CASES, form={}))


@router.post("/signup", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def signup(request: Request, name: str = Form(...), email: str = Form(...), org: str = Form(""),
           title: str = Form(""), use_case: str = Form(""), password: str = Form(...),
           terms: str = Form(""), db: Session = Depends(get_db)):  # noqa: ANN201
    form = {"name": name, "email": email, "org": org, "title": title, "use_case": use_case}
    err = None
    if not limiter.allow(f"signup:{_ip(request)}", 3, 3600):
        err = "Too many requests from your network. Please try again later."
    elif "@" not in email or len(email) > 254 or not name.strip():
        err = "Enter your name and a valid work email."
    elif terms != "on":
        err = "Please accept the research-prototype terms."
    else:
        ok, msg = password_strength_ok(password)
        if not ok:
            err = msg
    if err:
        return templates.TemplateResponse(request, "public/signup.html",
                                          ctx(request, use_cases=USE_CASES, form=form, error=err), status_code=400)
    user = accounts.create_signup(db, email=email, name=name, org=org, title=title, use_case=use_case,
                                  password=password, ip=_ip(request))
    request.session["pending_email"] = email.strip().lower()
    if user is None:  # duplicate — generic, non-enumerating response
        return RedirectResponse("/pending", status_code=303)
    return RedirectResponse("/pending", status_code=303)


@router.get("/pending", response_class=HTMLResponse)
def pending(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    email = request.session.get("pending_email")
    user = db.scalar(select(User).where(User.email == email)) if email else None
    due, mode = None, accounts.approval_mode(db)
    if user:
        accounts.auto_approve_due(db, user)
        db.refresh(user)
        due = accounts.decision_due_at(db, user)
    return templates.TemplateResponse(request, "public/pending.html",
                                      ctx(request, pending_user=user, due=due, mode=mode,
                                          window=accounts.approval_window_hours(db)))


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/app"):  # noqa: ANN201, A002
    return templates.TemplateResponse(request, "public/login.html", ctx(request, next=next))


@router.post("/login", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/app"),  # noqa: A002
          db: Session = Depends(get_db)):  # noqa: ANN201
    ip = _ip(request)
    if not limiter.allow(f"login:{ip}", 5, 60):
        return templates.TemplateResponse(request, "public/login.html",
                                          ctx(request, next=next, error="Too many attempts. Wait a minute."), 429)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user and user.status == UserStatus.pending:
        accounts.auto_approve_due(db, user)
        db.refresh(user)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(request, "public/login.html",
                                          ctx(request, next=next, error="Email or password is incorrect."), 401)
    if user.status == UserStatus.pending:
        request.session["pending_email"] = user.email
        return RedirectResponse("/pending", status_code=303)
    if user.status != UserStatus.approved:
        return templates.TemplateResponse(request, "public/login.html",
                                          ctx(request, next=next, error="This account is not active."), 403)
    request.session.clear()  # session rotation
    request.session["uid"] = user.id
    user.last_login_at = utcnow()
    audit(db, user.id, "login", "user", user.id, ip=ip)
    db.commit()
    dest = next if next.startswith("/") and not next.startswith("//") else "/app"
    return RedirectResponse(dest, status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request):  # noqa: ANN201
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request):  # noqa: ANN201
    return templates.TemplateResponse(request, "public/forgot.html", ctx(request))


@router.post("/forgot", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def forgot(request: Request, email: str = Form(...), db: Session = Depends(get_db)):  # noqa: ANN201
    if limiter.allow(f"forgot:{_ip(request)}", 3, 3600):
        accounts.start_password_reset(db, email)
    return templates.TemplateResponse(request, "public/forgot.html", ctx(request, sent=True))


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str):  # noqa: ANN201
    return templates.TemplateResponse(request, "public/reset.html", ctx(request, token=token))


@router.post("/reset/{token}", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
def reset(request: Request, token: str, password: str = Form(...), db: Session = Depends(get_db)):  # noqa: ANN201
    ok, msg = password_strength_ok(password)
    if ok and accounts.complete_password_reset(db, token, password):
        return RedirectResponse("/login?reset=1", status_code=303)
    return templates.TemplateResponse(request, "public/reset.html",
                                      ctx(request, token=token, error=msg or "This link is invalid or expired."), 400)
