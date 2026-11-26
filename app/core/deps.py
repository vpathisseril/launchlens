"""Request dependencies: current user, authorization gates, CSRF, templates."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import csrf_valid, new_token
from app.models import AiKeyStatus, Module, Role, User, UserModule, UserStatus

templates = Jinja2Templates(directory="app/templates")


def _site_globals() -> None:
    from app.content import about
    templates.env.globals["site"] = {"name": about.NAME, "signature": about.SIGNATURE, "disclaimer": about.DISCLAIMER,
                                     "terms": about.TERMS, "version": about.VERSION}


_site_globals()


class RedirectTo(Exception):  # noqa: N818 — control-flow signal, handled in main
    def __init__(self, url: str) -> None:
        self.url = url


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = new_token()
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    submitted = request.headers.get("X-CSRF-Token")
    if not submitted:
        form = await request.form()
        val = form.get("csrf_token")
        submitted = val if isinstance(val, str) else None
    if not csrf_valid(request.session.get("csrf"), submitted):
        raise HTTPException(status_code=403, detail="Your session expired. Refresh the page and try again.")


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if not user or user.status != UserStatus.approved:
        request.session.clear()
        return None
    return user


def require_login(request: Request, user: User | None = Depends(optional_user)) -> User:
    if not user:
        raise RedirectTo("/login?next=" + request.url.path)
    return user


def require_user(request: Request, user: User = Depends(require_login)) -> User:
    """Logged in AND past forced AI-key onboarding AND past forced password change."""
    if user.must_change_password:
        raise RedirectTo("/account/password")
    if user.ai_key_status == AiKeyStatus.none:
        raise RedirectTo("/onboarding/ai-keys")
    return user


def require_admin(user: User = Depends(require_login)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=404)  # do not reveal the admin surface
    if user.must_change_password:
        raise RedirectTo("/account/password")
    return user


def user_module_keys(db: Session, user: User) -> set[str]:
    if user.role == Role.admin:
        return set(db.scalars(select(Module.key).where(Module.is_active.is_(True))))
    rows = db.scalars(
        select(Module.key).join(UserModule, UserModule.module_id == Module.id)
        .where(UserModule.user_id == user.id, Module.is_active.is_(True))
    )
    return set(rows)


def require_module(key: str):  # noqa: ANN201 — dependency factory
    def _dep(user: User = Depends(require_user), db: Session = Depends(get_db)) -> User:
        if key not in user_module_keys(db, user):
            raise HTTPException(status_code=403, detail=f"module:{key}")
        return user
    return _dep


def ctx(request: Request, user: User | None = None, **extra: Any) -> dict[str, Any]:
    """Base template context."""
    base = {"request": request, "user": user, "csrf_token": csrf_token(request),
            "theme": (user.theme if user else request.cookies.get("theme", "clinical-clean"))}
    base.update(extra)
    return base
