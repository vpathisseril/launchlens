"""R0–R5 acceptance criteria — hub views render with lineage, agent citations verified, pin → project → brief."""
import json
import re

from sqlalchemy import select

from app.core import db as dbmod
from app.models import Brief, Finding, PolicyItem
from app.research.agent import verify
from tests.conftest import csrf, ready_user

HUB_VIEWS = {
    "pricing": ["tracker", "candidates", "exposure", "class_impact", "mfn"],
    "policy": ["radar", "timeline", "exposure", "trade", "week"],
    "targeting": ["deciles", "whitespace", "segments", "ramp", "scenario", "model"],
    "differentiation": ["market", "competitors", "matrix", "pipeline", "approvals", "evidence"],
    "access": ["scorecard", "heatmap", "geo", "quadrant", "trend"],
}


def test_command_center_shows_five_hubs(client):
    ready_user(client)
    html = client.get("/app").text
    for short in ("Access", "Pricing", "Policy", "Targeting", "Differentiation"):
        assert f"<h3>{short}</h3>" in html


def test_every_view_renders(client):
    ready_user(client)
    tok = csrf(client, "/app")
    client.post("/hubs/policy/refresh", data={"csrf_token": tok})
    for hub, views in HUB_VIEWS.items():
        assert client.get(f"/hubs/{hub}").status_code == 200
        for v in views:
            r = client.get(f"/hubs/{hub}/view/{v}")
            assert r.status_code == 200, (hub, v, r.text[:500])
            assert "Traceback" not in r.text


def test_r2_tracker_marks_scope_drugs_and_cites_cms(client):
    ready_user(client)
    html = client.get("/hubs/pricing/view/tracker").text
    assert "★ Ibrance" in html and "★ Kisqali" in html and "★ Verzenio" in html
    assert "cms.gov" in html and "$7,871.00" in html


def test_r2_candidate_score_is_labeled_estimate(client):
    ready_user(client)
    html = client.get("/hubs/pricing/view/candidates").text
    assert "ESTIMATE" in html and "Bigdrug" in html and "Generico" not in html


def test_r3_policy_status_integrity(client):
    ready_user(client)
    tok = csrf(client, "/app")
    client.post("/hubs/policy/refresh", data={"csrf_token": tok})
    with dbmod.session_factory()() as s:
        items = {i.title: i for i in s.scalars(select(PolicyItem))}
    pbm = items["Pharmacy Benefit Manager Transparency for Part D"]
    assert pbm.status == "Proposed" and pbm.topic == "pbm" and "Part D" in pbm.affected_channels
    assert "Deadlines" in client.get("/hubs/policy").text
    assert "2027-01-09" in client.get("/hubs/policy/view/timeline").text


def test_r4_deciles_and_suppression_banner(client):
    ready_user(client)
    html = client.get("/hubs/targeting/view/deciles").text
    assert "Decile 10 prescribers" in html and "&lt;11 claims" in html or "<11 claims" in html
    w = client.get("/hubs/targeting/view/deciles?w_volume=60&w_growth=0&w_need=0&w_access=0&w_vulnerability=40").text
    assert "Class volume 60%" in w


def test_r4_backtest_lift(client):
    ready_user(client)
    html = client.get("/hubs/targeting/view/model").text
    assert "lift" in html and "Model Card" in html


def test_r5_label_matrix_cites_sections_and_faers_caveat(client):
    ready_user(client)
    html = client.get("/hubs/differentiation/view/matrix").text
    assert "set_id set-ribociclib" in html and "do not establish causation" in html


def test_r1_access_empty_state_until_formulary_loaded(client):
    ready_user(client)
    assert "Formulary data has not been ingested" in client.get("/hubs/access/view/scorecard").text


def test_r1_access_metrics_with_ingested_formulary(client, tmp_path):
    import io
    import zipfile

    from app.data.formulary import ingest_zip
    ready_user(client)
    plans = "CONTRACT_ID|PLAN_ID|SEGMENT_ID|FORMULARY_ID|STATE|PLAN_SUPPRESSED_YN\nS1234|001|0|F1|MA|N\nH5555|002|0|F2|MA|N\n"
    form = ("FORMULARY_ID|RXCUI|TIER_LEVEL_VALUE|QUANTITY_LIMIT_YN|PRIOR_AUTHORIZATION_YN|STEP_THERAPY_YN\n"
            "F1|1601380|5|N|Y|N\n")
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("plan information.txt", plans)
        z.writestr("basic drugs formulary.txt", form)
    p = tmp_path / "f.zip"
    p.write_bytes(inner.getvalue())
    with dbmod.session_factory()() as s:
        n_plans, n_rows = ingest_zip(s, p, ["palbociclib"], "2026-Q3")
    assert n_plans == 2 and n_rows == 1
    html = client.get("/hubs/access/view/scorecard").text
    assert "50.0%" in html  # covered by 1 of 2 plans
    assert "100.0%" in html  # PA among covering plans


def test_agent_answer_is_citation_verified_and_pinnable(client):
    ready_user(client)
    tok = csrf(client, "/app")
    r = client.post("/hubs/pricing/ask", data={"csrf_token": tok, "question": "Which IPAY includes palbociclib?"})
    assert "Citations verified" in r.text
    m = re.search(r'name="citations" value="([^"]+)"', r.text)
    cites = json.loads(m.group(1).replace("&#34;", '"').replace("&quot;", '"'))
    assert cites and cites[0]["url"].startswith("https://www.cms.gov")


def test_verifier_flags_fabricated_numbers():
    ok, notes = verify("Coverage is 87.5% [1].", "coverage 50.0% of plans", "q", 1)
    assert not ok and "87.5%" in notes
    ok, notes = verify("Coverage is 50.0% [1].", "coverage 50.0% of plans", "q", 1)
    assert ok
    ok, notes = verify("Coverage is 50.0% [4].", "coverage 50.0%", "q", 1)
    assert not ok and "without a source" in notes


def test_pin_requires_citation_then_brief(client):
    ready_user(client)
    tok = csrf(client, "/app")
    r = client.post("/pin", data={"csrf_token": tok, "hub_key": "pricing", "title": "No source", "citations": "[]"})
    assert "needs at least one source" in r.text
    cite = [{"source_key": "negotiation_program", "dataset": "CMS", "vintage": "2027", "url": "https://www.cms.gov/x",
             "retrieved_at": "now", "locator": "IPAY 2027"}]
    r = client.post("/pin", data={"csrf_token": tok, "hub_key": "pricing", "title": "Ibrance MFP",
                                  "body": "MFP $7,871 [1]", "citations": json.dumps(cite), "new_project": "CDK launch"})
    assert "Pinned to" in r.text
    with dbmod.session_factory()() as s:
        f = s.scalar(select(Finding))
        pid = f.project_id
    r = client.post(f"/projects/{pid}/brief", data={"csrf_token": tok, "mode": "ai"}, follow_redirects=False)
    assert r.status_code == 303 and "err" not in r.headers["location"]
    with dbmod.session_factory()() as s:
        b = s.scalar(select(Brief))
        assert "By Varghese Pathisseril" in b.content_md and "Research prototype" in b.content_md
        assert "## Sources" in b.content_md
    assert client.get(f"/briefs/{b.id}.md").status_code == 200
    assert "Print / Save as PDF" in client.get(f"/briefs/{b.id}/print").text


def test_project_isolation(client):
    ready_user(client)
    tok = csrf(client, "/app")
    client.post("/projects", data={"csrf_token": tok, "title": "Mine"})
    client.cookies.clear()
    ready_user(client, "eve@example.com")
    assert client.get("/projects/1").status_code == 404


def test_module_gating(client):
    from app.models import Module, User, UserModule
    ready_user(client)
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == "ana@example.com"))
        m = s.scalar(select(Module).where(Module.key == "pricing"))
        s.delete(s.get(UserModule, (u.id, m.id)))
        s.commit()
    r = client.get("/hubs/pricing")
    assert r.status_code == 403 and "isn't enabled" in r.text
    tok = csrf(client, "/app")
    r = client.post("/hubs/access/ask", data={"csrf_token": tok, "question": "Which IPAY includes palbociclib?"})
    assert "pricing." not in r.text.split("tools:")[-1][:200]
