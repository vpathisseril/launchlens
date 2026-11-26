"""BYOK model resolution — ask the user only when nothing usable is configured (bug: OpenAI/Gemini keys + 'default')."""
import pytest
from sqlalchemy import select

from app.core import db as dbmod
from app.core.security import encrypt_secret
from app.models import AiKeyStatus, User, UserApiKey
from app.services.ai_access import NeedsModelChoice, effective_models, pick_from_available, resolve_access
from tests.conftest import approve_now, csrf, login, signup

OPENAI = ["gpt-4o-2024-08-06", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini",
          "text-embedding-3-large", "gpt-4o-realtime-preview", "o3"]
GOOGLE = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash-001",
          "embedding-001", "gemini-2.5-flash-preview-tts", "gemma-3-27b-it"]


def test_pick_from_available_prefers_current_stable_models():
    assert pick_from_available(OPENAI, "answer") == "gpt-5-mini"
    assert pick_from_available(OPENAI, "brief") == "gpt-5"
    assert pick_from_available(GOOGLE, "answer") == "gemini-2.5-flash"
    assert pick_from_available(GOOGLE, "brief") == "gemini-2.5-pro"
    assert pick_from_available([], "answer") is None


def _user_with_keys(client, providers: dict[str, list[str]], preferred: str) -> int:
    signup(client)
    approve_now("ana@example.com")
    login(client, "ana@example.com", "Str0ng-Passw0rd!")
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        prefs = {}
        for p, models in providers.items():
            s.add(UserApiKey(user_id=u.id, provider=p, key_ciphertext=encrypt_secret(f"key-{p}-1234"), key_last4="1234",
                             is_valid=True))
            prefs["available_" + p] = models
        u.preferred_models, u.preferred_provider, u.ai_key_status = prefs, preferred, AiKeyStatus.byok_valid
        s.commit()
        return u.id


def _resolve(uid, **kw):
    with dbmod.session_factory()() as s:
        return resolve_access(s, s.get(User, uid), **kw)


def test_openai_default_uses_auto_model_instead_of_asking(client):
    uid = _user_with_keys(client, {"anthropic": [], "openai": OPENAI, "google": GOOGLE}, "openai")
    acc = _resolve(uid, task="answer")
    assert (acc.provider, acc.model, acc.key_type) == ("openai", "gpt-5-mini", "byok")
    assert _resolve(uid, task="brief").model == "gpt-5"


def test_falls_back_to_another_valid_key_when_preferred_has_no_model(client):
    uid = _user_with_keys(client, {"openai": [], "anthropic": []}, "openai")
    acc = _resolve(uid)
    assert acc.provider == "anthropic" and acc.model == "claude-sonnet-5-5" and "Using your anthropic key" in acc.notice


def test_agent_requesting_missing_provider_uses_users_key(client):
    uid = _user_with_keys(client, {"google": GOOGLE}, "google")
    acc = _resolve(uid, requested_provider="anthropic", requested_model="claude-opus-5-5")
    assert acc.provider == "google" and acc.model == "gemini-2.5-flash"


def test_asks_only_when_nothing_usable(client):
    uid = _user_with_keys(client, {"openai": []}, "openai")
    with pytest.raises(NeedsModelChoice):
        _resolve(uid)


def test_settings_show_ready_state_and_hub_error_points_to_routing(client):
    uid = _user_with_keys(client, {"openai": OPENAI}, "openai")
    page = client.get("/account/ai").text
    assert "Ready:" in page and "gpt-5-mini" in page and "Auto · gpt-5-mini" in page
    with dbmod.session_factory()() as s:
        assert effective_models(s, s.get(User, uid))["openai"] == {"answer": "gpt-5-mini", "brief": "gpt-5"}
        u = s.get(User, uid)
        u.preferred_models = {"available_openai": []}
        s.commit()
    tok = csrf(client, "/app")
    client.post("/scope", data={"csrf_token": tok, "preset": "oncology-cdk46", "apply_preset": "1"})
    r = client.post("/hubs/pricing/ask", data={"csrf_token": tok, "question": "Which IPAY includes palbociclib?"})
    assert "Choose a model" in r.text and "Add your own key" not in r.text


def test_choosing_auto_clears_previous_choice(client):
    uid = _user_with_keys(client, {"openai": OPENAI}, "openai")
    tok = csrf(client, "/account/ai")
    client.post("/account/ai/models", data={"csrf_token": tok, "preferred_provider": "openai", "openai_answer": "gpt-4.1",
                                            "openai_brief": ""})
    assert _resolve(uid).model == "gpt-4.1"
    client.post("/account/ai/models", data={"csrf_token": tok, "preferred_provider": "openai", "openai_answer": "",
                                            "openai_brief": ""})
    assert _resolve(uid).model == "gpt-5-mini"


def test_rank_models_keeps_newest_even_with_cap():
    from app.services.ai_access import rank_models
    many = [f"gpt-3.5-turbo-{i:04d}" for i in range(100)] + ["gpt-5-mini", "gpt-5"]
    ranked = rank_models("openai", many, cap=10)
    assert ranked[:2] == ["gpt-5", "gpt-5-mini"]


def test_refresh_model_lists_endpoint(client, api_mock):
    import httpx
    api_mock.get("https://api.openai.com/v1/models").respond(200, json={"data": [{"id": m} for m in OPENAI]})
    uid = _user_with_keys(client, {"openai": []}, "openai")
    tok = csrf(client, "/account/ai")
    r = client.post("/account/ai/refresh-models", data={"csrf_token": tok}, follow_redirects=False)
    assert "saved=1" in r.headers["location"]
    assert _resolve(uid).model == "gpt-5-mini"
    assert isinstance(httpx, object)
