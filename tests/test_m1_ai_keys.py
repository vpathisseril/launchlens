"""M1 acceptance criteria — forced onboarding, BYOK encryption & hygiene, default-key policy limits."""
from sqlalchemy import select

from app.core import db as dbmod
from app.models import AiKeyStatus, DefaultKeyPolicy, User, UserApiKey
from tests.conftest import approve_now, csrf, login, ready_user, signup


def _onboard_user(client):
    signup(client)
    approve_now("ana@example.com")
    login(client, "ana@example.com", "Str0ng-Passw0rd!")


def test_m1_byok_valid_key_stored_encrypted(client):
    ready_user(client)
    with dbmod.session_factory()() as s:
        k = s.scalar(select(UserApiKey))
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        assert k.key_last4 == "1234" and "sk-ant-good" not in k.key_ciphertext
        assert u.ai_key_status == AiKeyStatus.byok_valid
    page = client.get("/account/ai").text
    assert "sk-ant-good-key-1234" not in page and "••••1234" in page


def test_m1_invalid_key_not_stored(client):
    _onboard_user(client)
    tok = csrf(client, "/onboarding/ai-keys")
    r = client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                      "api_key": "sk-ant-bad-key-0000", "back": "/app"})
    assert r.status_code == 400 and "rejected this key" in r.text and "sk-ant-api03-" in r.text
    with dbmod.session_factory()() as s:
        assert s.scalar(select(UserApiKey)) is None


def test_m1_default_key_requires_consent(client):
    _onboard_user(client)
    tok = csrf(client, "/onboarding/ai-keys")
    r = client.post("/onboarding/ai-keys/default", data={"csrf_token": tok}, follow_redirects=False)
    assert "err=default" in r.headers["location"]
    r = client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"}, follow_redirects=False)
    assert r.headers["location"] == "/app"


def test_m1_default_key_daily_cap(client):
    _onboard_user(client)
    tok = csrf(client, "/onboarding/ai-keys")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    client.post("/scope", data={"csrf_token": tok, "preset": "oncology-cdk46", "apply_preset": "1"})
    with dbmod.session_factory()() as s:
        p = s.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == "anthropic"))
        p.calls_per_day = 1
        s.commit()
    r1 = client.post("/hubs/pricing/ask", data={"csrf_token": tok, "question": "Which IPAY includes palbociclib?"})
    assert "Citations verified" in r1.text or "Verify" in r1.text
    r2 = client.post("/hubs/pricing/ask", data={"csrf_token": tok, "question": "Again?"})
    assert "shared-key calls for today" in r2.text and "Add your own key" in r2.text


def test_m1_default_key_downgrades_disallowed_model(client):
    from app.services.ai_access import resolve_access
    _onboard_user(client)
    tok = csrf(client, "/onboarding/ai-keys")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        acc = resolve_access(s, u, "brief", requested_model="claude-opus-5-5", max_tokens=4000)
        assert acc.model == "claude-haiku-5-5" and acc.key_type == "default" and acc.max_tokens == 800
        assert "switched" in acc.notice


def test_m1_byok_is_unlimited(client):
    from app.services.ai_access import resolve_access
    ready_user(client)
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        acc = resolve_access(s, u, "brief", max_tokens=4000)
        assert acc.key_type == "byok" and acc.max_tokens == 4000
