"""M2 agent builder + M4 admin console acceptance criteria."""
from sqlalchemy import select

from app.core import db as dbmod
from app.models import Agent, AuditLog, DefaultKeyPolicy
from tests.conftest import admin_login, csrf, ready_user


def test_m2_clone_template_and_restrict_tools(client):
    ready_user(client)
    tok = csrf(client, "/agents")
    with dbmod.session_factory()() as s:
        tpl = s.scalar(select(Agent).where(Agent.is_template.is_(True), Agent.name == "Pricing Policy Analyst"))
    r = client.post(f"/agents/clone/{tpl.id}", data={"csrf_token": tok}, follow_redirects=False)
    aid = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/agents/{aid}", data={"csrf_token": tok, "name": "Mine", "tone": "concise",
                                            "tools": ["pricing.selected_drugs", "evil.tool"], "model": "not-allowed"},
                    follow_redirects=False)
    assert r.status_code == 303
    with dbmod.session_factory()() as s:
        a = s.get(Agent, aid)
        assert a.tools == ["pricing.selected_drugs"] and a.model == "" and a.tone == "concise"
    page = client.get(f"/agents/{aid}").text
    assert "Guardrail preview" in page and "Use ONLY the facts" in page


def test_m4_admin_pages_render(client):
    admin_login(client)
    for p in ("/admin", "/admin/approvals", "/admin/users", "/admin/modules", "/admin/ai", "/admin/usage",
              "/admin/data", "/admin/audit"):
        r = client.get(p)
        assert r.status_code == 200, p


def test_m4_change_default_policy_audited(client):
    admin_login(client)
    tok = csrf(client, "/admin/ai")
    with dbmod.session_factory()() as s:
        pid = s.scalar(select(DefaultKeyPolicy).where(DefaultKeyPolicy.provider == "anthropic")).id
    client.post(f"/admin/ai/policy/{pid}", data={"csrf_token": tok, "allowlist": "claude-haiku-5-5", "max_tokens": "500",
                                                  "calls_per_day": "10", "budget": "5", "enabled": "on"})
    with dbmod.session_factory()() as s:
        p = s.get(DefaultKeyPolicy, pid)
        assert p.calls_per_day == 10
        assert s.scalar(select(AuditLog).where(AuditLog.action == "default_policy_changed"))


def test_m4_csv_export_audited(client):
    admin_login(client)
    r = client.get("/admin/export/users.csv")
    assert r.status_code == 200 and "email" in r.text.splitlines()[0]
    with dbmod.session_factory()() as s:
        assert s.scalar(select(AuditLog).where(AuditLog.action == "csv_export"))


def test_m4_policy_item_requires_gov_source(client):
    admin_login(client)
    tok = csrf(client, "/admin/data")
    r = client.post("/admin/data/policy-item", data={"csrf_token": tok, "title": "X", "topic": "mfn",
                                                      "status": "Announced", "status_date": "2026-10-01",
                                                      "source_url": "https://example.com/x"})
    assert r.status_code == 400
    r = client.post("/admin/data/policy-item", data={"csrf_token": tok, "title": "MFN agreement", "topic": "mfn",
                                                      "status": "Announced", "status_date": "2026-10-01",
                                                      "source_url": "https://www.hhs.gov/press-room/x"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    assert "MFN agreement" in client.get("/hubs/pricing/view/mfn").text
