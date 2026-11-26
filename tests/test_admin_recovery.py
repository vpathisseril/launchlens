"""Admin recovery without SMTP via the ADMIN_RESET_PASSWORD secret."""
from sqlalchemy import select

from app.core import db as dbmod
from app.core.config import get_settings
from app.models import AuditLog, User
from app.services.accounts import admin_emails, apply_admin_reset
from tests.conftest import csrf, login


def test_reset_secret_restores_admin_once_and_forces_change(client):
    s = get_settings()
    s.admin_reset_password = "Recovery-Pass-2026!"
    try:
        with dbmod.session_factory()() as db:
            assert admin_emails(db) == ["admin@launchlens.app"]
            assert apply_admin_reset(db) is True
            assert apply_admin_reset(db) is False  # same secret value is applied only once
            assert db.scalar(select(AuditLog).where(AuditLog.action == "admin_password_reset_via_secret"))
        r = login(client, "admin@launchlens.app", "Recovery-Pass-2026!")
        assert r.status_code == 303
        assert client.get("/admin", follow_redirects=False).headers["location"] == "/account/password"
        tok = csrf(client, "/account/password")
        client.post("/account/password", data={"csrf_token": tok, "current": "Recovery-Pass-2026!",
                                                "new": "Brand-New-Admin-77!"})
        with dbmod.session_factory()() as db:
            assert apply_admin_reset(db) is False  # restart with the secret still set must not undo the change
            u = db.scalar(select(User).where(User.email == "admin@launchlens.app"))
            assert u.must_change_password is False
    finally:
        s.admin_reset_password = ""


def test_no_secret_no_change(client):
    with dbmod.session_factory()() as db:
        assert apply_admin_reset(db) is False
