"""End-to-end AI audit: every AI screen × shared default key and personal keys, normal + restricted + error paths,
and the key-update workflow. Screens: hub research agent (all 5 hubs), executive brief, AI access settings,
onboarding, agent builder, admin AI governance, About.
"""
import pytest
from sqlalchemy import select

from app.core import db as dbmod
from app.models import AiKeyStatus, DefaultKeyPolicy, User, UserApiKey
from tests.conftest import PROVIDER_FAIL, admin_login, approve_now, csrf, login, ready_user, signup

Q = "Which IPAY includes palbociclib?"
HUBS = ["access", "pricing", "policy", "targeting", "differentiation"]


def _new_user(client, email="ana@example.com"):
    signup(client, email)
    approve_now(email)
    login(client, email, "Str0ng-Passw0rd!")
    return csrf(client, "/onboarding/ai-keys")


def _scope(client, tok):
    client.post("/scope", data={"csrf_token": tok, "preset": "oncology-cdk46", "apply_preset": "1"})


def _ask(client, tok, hub="pricing", q=Q):
    return client.post(f"/hubs/{hub}/ask", data={"csrf_token": tok, "question": q}).text


def _set_policy(**kw):
    with dbmod.session_factory()() as s:
        p = s.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == "anthropic"))
        for k, v in kw.items():
            setattr(p, k, v)
        s.commit()


def _user(email="ana@example.com"):
    with dbmod.session_factory()() as s:
        return s.scalar(select(User).where(User.email == email))


# ------------------------------------------------------------------ shared default key
def test_default_key_normal_answers_on_every_hub(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    _scope(client, tok)
    for hub in HUBS:
        html = _ask(client, tok, hub)
        assert "shared key" in html and ("Citations verified" in html or "Verify:" in html), hub
        assert "alert" not in html.split("prose")[0], hub


def test_default_key_daily_limit_message_and_fix(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    _scope(client, tok)
    _set_policy(calls_per_day=1)
    _ask(client, tok)
    html = _ask(client, tok, q="Another question about access")
    assert "Shared-key limit reached" in html and "Settings → AI access" in html and "Add your own key" in html


def test_default_key_monthly_budget_message(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    _scope(client, tok)
    from app.models import UsageEvent
    with dbmod.session_factory()() as s:
        s.add(UsageEvent(user_id=_user().id, provider="anthropic", model="claude-haiku-5-5", key_type="default",
                         est_cost_usd=30.0))
        s.commit()
    html = _ask(client, tok)
    assert "monthly budget" in html and "Add your own key" in html


def test_default_key_restrictions_lifted_by_adding_personal_key(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    _scope(client, tok)
    _set_policy(calls_per_day=1)
    _ask(client, tok)
    assert "Shared-key limit reached" in _ask(client, tok, q="Second")
    client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                  "api_key": "sk-ant-good-key-1234", "back": "/account/ai"})
    for i in range(3):  # beyond the shared-key cap: personal keys are never limited
        html = _ask(client, tok, q=f"{Q} #{i}")
        assert "own key" in html and "limit" not in html.lower().split("prose")[0]


def test_default_key_inactive_hides_option_on_onboarding(client):
    _set_policy(is_enabled=False)
    tok = _new_user(client)
    page = client.get("/onboarding/ai-keys").text
    assert "hasn't turned on a shared key" in page
    r = client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"}, follow_redirects=False)
    assert "err=default" in r.headers["location"]


# ------------------------------------------------------------------ personal keys
def test_personal_key_answers_on_every_hub_with_flagship_model(client):
    ready_user(client)
    tok = csrf(client, "/app")
    for hub in HUBS:
        html = _ask(client, tok, hub)
        assert "own key" in html and "claude-sonnet-5-5" in html, hub  # accuracy-first: standard tier for answers


@pytest.mark.parametrize("status,body,expect", [
    (401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}, "key was rejected"),
    (429, {"error": {"type": "rate_limit_error", "message": "Rate limited"}}, "not a LaunchLens limit"),
    (400, {"error": {"message": "Your credit balance is too low to access the Anthropic API."}}, "needs credits"),
    (404, {"error": {"type": "not_found_error", "message": "model: claude-x"}}, "Model not available"),
    (529, {"error": {"type": "overloaded_error", "message": "Overloaded"}}, "temporarily unavailable"),
])
def test_personal_key_provider_errors_are_friendly(client, status, body, expect):
    ready_user(client)
    tok = csrf(client, "/app")
    PROVIDER_FAIL["anthropic"] = (status, body)
    html = _ask(client, tok)
    assert expect in html and 'href="/account/ai' in html  # clear message + a direct fix link
    assert "Traceback" not in html and "HTTP" not in html.split("<strong>")[1][:80]


def test_rejected_key_is_marked_and_onboarding_resumes(client):
    ready_user(client)
    tok = csrf(client, "/app")
    PROVIDER_FAIL["anthropic"] = (401, {"error": {"message": "invalid x-api-key"}})
    assert "Update your key" in _ask(client, tok)
    with dbmod.session_factory()() as s:
        assert s.scalar(select(UserApiKey)).is_valid is False
    assert _user().ai_key_status == AiKeyStatus.none
    assert client.get("/app", follow_redirects=False).headers["location"] == "/onboarding/ai-keys"
    page = client.get("/onboarding/ai-keys").text
    assert "Use my own API key" in page


def test_rejected_key_falls_back_to_other_valid_key(client):
    ready_user(client)
    tok = csrf(client, "/app")
    client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "openai",
                                                  "api_key": "sk-proj-good-5678", "back": "/account/ai"})
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        u.preferred_provider = "anthropic"
        s.commit()
    PROVIDER_FAIL["anthropic"] = (401, {"error": {"message": "invalid x-api-key"}})
    html = _ask(client, tok)
    assert "OpenAI answer" in html and "gpt-5" in html  # transparent retry with the user's other key
    page = client.get("/account/ai").text
    assert "Rejected · ••••1234" in page and "Validate & replace" in page


def test_out_of_credits_key_falls_back_to_other_key(client):
    ready_user(client)
    tok = csrf(client, "/app")
    client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                  "api_key": "sk-ant-good-1234", "back": "/account/ai"})
    client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "openai",
                                                  "api_key": "sk-proj-good-5678", "back": "/account/ai"})
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        u.preferred_provider = "openai"
        s.commit()
    PROVIDER_FAIL["openai"] = (429, {"error": {"code": "insufficient_quota",
                                               "message": "You exceeded your current quota"}})
    html = _ask(client, tok)
    assert "openai account is out of credits" in html and "Citations verified" in html
    with dbmod.session_factory()() as s:  # the key is not marked rejected — it only needs credits
        assert all(k.is_valid for k in s.scalars(select(UserApiKey)))


def test_out_of_credits_single_key_shows_billing_guidance(client):
    ready_user(client)
    tok = csrf(client, "/app")
    PROVIDER_FAIL["anthropic"] = (400, {"error": {"message": "Your credit balance is too low"}})
    html = _ask(client, tok)
    assert "credits" in html and 'href="/account/ai' in html


def test_key_update_workflow_after_rejection(client):
    ready_user(client)
    tok = csrf(client, "/app")
    PROVIDER_FAIL["anthropic"] = (401, {"error": {"message": "invalid x-api-key"}})
    _ask(client, tok)
    PROVIDER_FAIL["anthropic"] = None
    r = client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                      "api_key": "sk-ant-good-new-9999", "back": "/app"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _user().ai_key_status == AiKeyStatus.byok_valid
    assert "Citations verified" in _ask(client, tok)


def test_invalid_key_message_explains_formats(client):
    tok = _new_user(client)
    r = client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "openai",
                                                      "api_key": "sk-proj-bad-0000", "back": "/app"})
    assert r.status_code == 400 and "OpenAI rejected this key" in r.text and "platform.openai.com" in r.text


# ------------------------------------------------------------------ briefs (second AI screen)
def _pin(client, tok):
    import json
    cite = [{"source_key": "negotiation_program", "dataset": "CMS", "vintage": "2027", "url": "https://www.cms.gov/x",
             "retrieved_at": "now", "locator": "IPAY 2027"}]
    client.post("/pin", data={"csrf_token": tok, "hub_key": "pricing", "title": "Ibrance MFP", "body": "MFP [1]",
                              "citations": json.dumps(cite), "new_project": "Audit"})
    return 1


def test_brief_with_personal_key_uses_premium_model(client):
    ready_user(client)
    tok = csrf(client, "/app")
    pid = _pin(client, tok)
    client.post(f"/projects/{pid}/brief", data={"csrf_token": tok, "mode": "ai"})
    page = client.get(f"/projects/{pid}").text
    assert "claude-opus-5-5" in page


def test_brief_errors_are_friendly_and_offer_no_ai_summary(client):
    ready_user(client)
    tok = csrf(client, "/app")
    pid = _pin(client, tok)
    PROVIDER_FAIL["anthropic"] = (429, {"error": {"message": "Rate limited"}})
    r = client.post(f"/projects/{pid}/brief", data={"csrf_token": tok, "mode": "ai"})
    assert "rate limit" in r.text and "Summary (no AI)" in r.text


def test_brief_with_shared_key_quota(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    _scope(client, tok)
    pid = _pin(client, tok)
    _set_policy(calls_per_day=0)
    r = client.post(f"/projects/{pid}/brief", data={"csrf_token": tok, "mode": "ai"})
    assert "Shared-key limit reached" in r.text


# ------------------------------------------------------------------ agents, admin, about
def test_agent_builder_models_follow_key_type(client):
    tok = _new_user(client)
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    from app.models import Agent
    with dbmod.session_factory()() as s:
        tpl = s.scalar(select(Agent).where(Agent.is_template.is_(True)))
    r = client.post(f"/agents/clone/{tpl.id}", data={"csrf_token": tok}, follow_redirects=False)
    page = client.get(r.headers["location"]).text
    assert "claude-haiku-5-5" in page and "claude-opus-5-5" not in page  # shared key → allowlist only
    client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                  "api_key": "sk-ant-good-key-1234", "back": "/account/ai"})
    page = client.get(r.headers["location"]).text
    assert "claude-opus-5-5" in page  # own key → all models


def test_admin_cannot_enable_policy_without_models_and_sees_status(client):
    admin_login(client)
    tok = csrf(client, "/admin/ai")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    with dbmod.session_factory()() as s:
        gid = s.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == "google")).id
    r = client.post(f"/admin/ai/policy/{gid}", data={"csrf_token": tok, "allowlist": "", "max_tokens": "800",
                                                      "calls_per_day": "20", "budget": "25", "enabled": "on"},
                    follow_redirects=False)
    assert "err=allowlist" in r.headers["location"]
    page = client.get("/admin/ai").text
    assert "Active" in page and "Inactive" in page and "Google Gemini" in page


def test_about_is_single_source_and_reflects_live_settings(client):
    _set_policy(calls_per_day=7, max_tokens_per_call=650)
    page = client.get("/about").text
    assert "7 research calls per user per day" in page and "650 output tokens" in page
    assert "Healthcare accuracy-first (recommended)</strong> (default)" in page
    from app.content.about import DISCLAIMER
    assert page.count(DISCLAIMER) == 2  # About body + footer, identical wording
    landing = client.get("/").text
    assert "within 24 hours" in landing and DISCLAIMER in landing
    for name in ("Federal Register API", "ClinicalTrials.gov API v2", "CDC PLACES county estimates"):
        assert name in page and name in landing


def test_settings_pages_keep_full_navigation(client):
    ready_user(client)
    for path in ("/account/ai", "/account/themes"):
        page = client.get(path).text
        for hub in ("Access", "Pricing", "Policy", "Targeting", "Differentiation"):
            assert f'href="/hubs/{hub.lower()}"' in page, (path, hub)
        assert 'href="/agents"' in page
    tok = csrf(client, "/account/ai")
    r = client.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "openai",
                                                      "api_key": "sk-proj-bad-0000", "back": "/account/ai"})
    assert r.status_code == 400 and 'href="/hubs/access"' in r.text and "OpenAI rejected this key" in r.text
