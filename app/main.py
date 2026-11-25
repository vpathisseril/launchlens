"""LaunchLens app factory: middleware (sessions, security headers + CSP nonce), routers, error handling."""
from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.db import Base, get_engine, session_factory
from app.core.deps import RedirectTo, ctx, templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def init_db() -> None:
    import app.models  # noqa: F401 — register models
    from app.core.schema_upgrade import upgrade
    from app.data.seed import seed
    from app.services.accounts import ensure_admin

    Base.metadata.create_all(get_engine())
    upgrade(get_engine())
    with session_factory()() as db:
        seed(db)
        ensure_admin(db)


def create_app() -> FastAPI:
    s = get_settings()
    if s.is_prod and (s.secret_key.startswith("dev-") or not s.fernet_key):
        raise RuntimeError("SECRET_KEY and FERNET_KEY must be set in production (Replit → Secrets).")
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN202
        init_db()
        yield

    app = FastAPI(title="LaunchLens", docs_url=None if s.is_prod else "/docs", redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.nonce = secrets.token_urlsafe(16)
        response = await call_next(request)
        n = request.state.nonce
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self' 'nonce-{n}'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
            "form-action 'self'")
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if s.is_prod:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie="ll_session", max_age=60 * 60 * 12,
                       same_site="lax", https_only=s.is_prod)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.routers import account, admin, agents, public, research
    for r in (public.router, account.router, research.router, agents.router, admin.router):
        app.include_router(r)

    @app.exception_handler(RedirectTo)
    async def _redir(request: Request, exc: RedirectTo):  # noqa: ANN202
        if request.headers.get("HX-Request"):
            return HTMLResponse("", headers={"HX-Redirect": exc.url})
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException):  # noqa: ANN202
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        detail = str(exc.detail or "")
        module = detail.split(":", 1)[1] if detail.startswith("module:") else ""
        tpl = {403: "errors/403.html", 404: "errors/404.html"}.get(exc.status_code, "errors/error.html")
        return templates.TemplateResponse(request, tpl, ctx(request, None, module=module, detail=detail,
                                                            status=exc.status_code), status_code=exc.status_code)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
