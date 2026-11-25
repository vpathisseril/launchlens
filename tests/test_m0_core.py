"""M0 acceptance criteria — signup, approval modes, login gating, module access, security headers."""
from sqlalchemy import select

from app.core import db as dbmod
from app.models import AuditLog, User, UserStatus
from app.services.mailer import outbox
from tests.conftest import admin_login, approve_now, csrf, login, signup


def _user(email):
    with dbmod.session_factory()() as s:
        return s.scalar(select(User).where(User.email == email))


def test_m0_s1_signup_creates_pending_and_notifies(client):
    outbox.clear()
    signup(client)
    u = _user("ana@example.com")
    assert u.status == UserStatus.pending
    assert any("new access request" in m["Subject"] for m in outbox)
    assert "Decision by" in client.get("/pending").text


def test_m0_s1_duplicate_email_is_generic(client):
    signup(client)
    tok = csrf(client, "/signup")
    r = client.post("/signup", data={"csrf_token": tok, "name": "X", "email": "ana@example.com",
                                     "password": "Str0ng-Passw0rd!", "terms": "on"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pending"


def test_m0_s1_weak_password_rejected(client):
    tok = csrf(client, "/signup")
    r = client.post("/signup", data={"csrf_token": tok, "name": "X", "email": "x@example.com", "password": "short",
                                     "terms": "on"})
    assert r.status_code == 400 and "12 characters" in r.text


def test_csrf_required(client):
    r = client.post("/signup", data={"name": "X", "email": "x@example.com", "password": "Str0ng-Passw0rd!"})
    assert r.status_code == 403


def test_m0_s3_pending_user_cannot_log_in(client):
    signup(client)
    r = login(client, "ana@example.com", "Str0ng-Passw0rd!")
    assert r.headers["location"] == "/pending"
    assert client.get("/app", follow_redirects=False).status_code == 303


def test_m0_s2_auto_approval_after_window_on_login(client):
    signup(client)
    approve_now("ana@example.com")
    r = login(client, "ana@example.com", "Str0ng-Passw0rd!")
    assert r.status_code == 303
    u = _user("ana@example.com")
    assert u.status == UserStatus.approved and u.approval_source == "auto"


def test_m0_s2_admin_approves_with_audit(client):
    signup(client)
    client.cookies.clear()
    admin_login(client)
    uid = _user("ana@example.com").id
    tok = csrf(client, "/admin/approvals")
    r = client.post("/admin/approvals/decide", data={"csrf_token": tok, "action": "approve", "uid": str(uid)},
                    follow_redirects=False)
    assert r.status_code == 303
    u = _user("ana@example.com")
    assert u.status == UserStatus.approved and u.approval_source == "admin"
    with dbmod.session_factory()() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "approve"))


def test_m0_s2_immediate_mode(client):
    admin_login(client)
    tok = csrf(client, "/admin/approvals")
    client.post("/admin/approvals/mode", data={"csrf_token": tok, "mode": "AUTO_IMMEDIATE", "window": "24"})
    client.cookies.clear()
    signup(client, "bo@example.com")
    assert _user("bo@example.com").status == UserStatus.approved


def test_m0_s3_first_login_forced_to_ai_onboarding(client):
    signup(client)
    approve_now("ana@example.com")
    login(client, "ana@example.com", "Str0ng-Passw0rd!")
    r = client.get("/app", follow_redirects=False)
    assert r.headers["location"] == "/onboarding/ai-keys"


def test_admin_surface_hidden_from_users(client):
    signup(client)
    approve_now("ana@example.com")
    login(client, "ana@example.com", "Str0ng-Passw0rd!")
    assert client.get("/admin", follow_redirects=False).status_code == 404


def test_admin_forced_password_change(client):
    login(client, "admin@launchlens.app", "Admin-Pass-12345!")
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/account/password"


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "unsafe-eval" not in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, "nobody@example.com", "wrong-password")
    assert login(client, "nobody@example.com", "wrong-password").status_code == 429
