"""Test harness: isolated SQLite DB, mocked public APIs + LLM providers (respx), helpers for auth flows.

Fixture payloads below are hand-built TEST DOUBLES that mimic each API's response shape. They are never used by the app.
"""
from __future__ import annotations

import os
import re
from datetime import timedelta

os.environ.setdefault("ENV", "test")
os.environ.setdefault("ADMIN_PASSWORD", "Admin-Pass-12345!")
os.environ.setdefault("DEFAULT_ANTHROPIC_KEY", "sk-ant-test-default-0000")

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import String, event
from sqlalchemy.orm import Session

from app.core import db as dbmod
from app.core.security import limiter


@event.listens_for(Session, "before_flush")
def _enforce_varchar_lengths(session, _ctx, _instances):  # noqa: ANN001
    """SQLite ignores VARCHAR(n); PostgreSQL rejects overflow. Fail tests the way production would."""
    for obj in list(session.new) + list(session.dirty):
        table = getattr(obj, "__table__", None)
        if table is None:
            continue
        for col in table.columns:
            if isinstance(col.type, String) and col.type.length:
                val = getattr(obj, col.key, None)
                if isinstance(val, str) and len(val) > col.type.length:
                    raise AssertionError(f"{table.name}.{col.name}: {len(val)} chars > VARCHAR({col.type.length}): {val[:60]!r}")

CMS_PRESC = "https://data.cms.gov/data-api/v1/dataset/presc-2023/data"
CMS_PRESC_PREV = "https://data.cms.gov/data-api/v1/dataset/presc-2022/data"
CMS_SPEND_D = "https://data.cms.gov/data-api/v1/dataset/spend-d/data"
CMS_SPEND_B = "https://data.cms.gov/data-api/v1/dataset/spend-b/data"

CATALOG = {"@type": "dcat:Catalog", "dataset": [  # 2026 shape: one dataset per year, dated title, temporal list
    {"title": "Medicare Part D Prescribers - by Provider and Drug : 2023-12-01", "distribution": [
        {"format": "API", "accessURL": CMS_PRESC, "temporal": [{"@type": "PeriodOfTime", "startDate": "2023-01-01",
                                                                "endDate": "2023-12-31"}]},
        {"mediaType": "text/csv", "downloadURL": "https://data.cms.gov/x.csv"}]},
    {"title": "Medicare Part D Prescribers - by Provider and Drug : 2022-12-01", "distribution": [
        {"format": "API", "accessURL": CMS_PRESC_PREV, "temporal": [{"startDate": "2022-01-01"}]}]},
    {"title": "Medicare Part D Spending by Drug : 2023-01-01", "distribution": [
        {"format": "API", "accessURL": CMS_SPEND_D, "temporal": [{"startDate": "2023-01-01"}]}]},
    {"title": "Medicare Part B Spending by Drug : 2023-01-01", "distribution": [
        {"accessURL": CMS_SPEND_B}]},  # no temporal → year from title suffix
    {"title": "Medicare Part D Prescribers - by Provider", "distribution": [  # different dataset, must not match
        {"accessURL": "https://data.cms.gov/data-api/v1/dataset/other/data"}]},
]}


def presc_rows(year: int) -> list[dict]:
    base = [("1000000001", "Smith", "Ana", "Boston", "MA", "Hematology-Oncology"),
            ("1000000002", "Lee", "Ben", "Austin", "TX", "Medical Oncology"),
            ("1000000003", "Patel", "Cy", "Miami", "FL", "Medical Oncology"),
            ("1000000004", "Gomez", "Di", "Denver", "CO", "Internal Medicine"),
            ("1000000005", "Kim", "Ed", "Seattle", "WA", "Hematology-Oncology")]
    rows = []
    for i, (npi, last, first, city, st, spec) in enumerate(base):
        for j, drug in enumerate(["Palbociclib", "Ribociclib", "Abemaciclib"]):
            clms = (40 + 10 * i + 5 * j) + (8 * i if year == 2023 else 0)
            rows.append({"Prscrbr_NPI": npi, "Prscrbr_Last_Org_Name": last, "Prscrbr_First_Name": first,
                         "Prscrbr_City": city, "Prscrbr_State_Abrvtn": st, "Prscrbr_Type": spec, "Gnrc_Name": drug,
                         "Brnd_Name": drug.upper(), "Tot_Clms": str(clms), "Tot_Benes": "" if i == 0 else "12",
                         "Tot_Drug_Cst": str(clms * 13000)})
    return rows


def spend_rows() -> list[dict]:
    def r(b, g, mftr, vals):
        d = {"Brnd_Name": b, "Gnrc_Name": g, "Tot_Mftr": str(mftr), "Mftr_Name": "Overall"}
        for y, v in zip(range(2019, 2024), vals, strict=True):
            d[f"Tot_Spndng_{y}"] = str(v)
        return d
    return [r("Ibrance", "Palbociclib", 1, [1.0e9, 1.4e9, 1.7e9, 1.9e9, 2.0e9]),
            r("Kisqali", "Ribociclib", 1, [0.2e9, 0.3e9, 0.5e9, 0.8e9, 1.1e9]),
            r("Verzenio", "Abemaciclib", 1, [0.3e9, 0.5e9, 0.8e9, 1.1e9, 1.4e9]),
            r("Bigdrug", "Bigdrugmab", 1, [3e9, 4e9, 5e9, 6e9, 7e9]),
            r("Generico", "Generico", 12, [1e9, 1e9, 1e9, 1e9, 1e9]),
            {"Brnd_Name": "Ibrance", "Gnrc_Name": "Palbociclib", "Mftr_Name": "Pfizer", "Tot_Spndng_2023": "2e9"}]


def label(generic: str, brand: str) -> dict:
    return {"set_id": f"set-{generic}", "effective_time": "20250101",
            "openfda": {"generic_name": [generic.upper()], "brand_name": [brand], "route": ["ORAL"],
                        "manufacturer_name": [f"{brand} Labs"], "product_type": ["HUMAN PRESCRIPTION DRUG"]},
            "indications_and_usage": [f"{brand} is indicated for HR-positive, HER2-negative advanced breast cancer."],
            "dosage_and_administration": ["125 mg orally once daily for 21 days followed by 7 days off."],
            "boxed_warning": [] if generic != "ribociclib" else ["QT prolongation"]}


def _api_routes(mock: respx.MockRouter) -> None:
    mock.get("https://data.cms.gov/data.json").respond(200, json=CATALOG)

    def presc(request: httpx.Request, year: int) -> httpx.Response:
        g = request.url.params.get("filter[Gnrc_Name]", "")
        st = request.url.params.get("filter[Prscrbr_State_Abrvtn]")
        off = int(request.url.params.get("offset", 0))
        rows = [r for r in presc_rows(year) if r["Gnrc_Name"] == g and (not st or r["Prscrbr_State_Abrvtn"] == st)]
        return httpx.Response(200, json=rows if off == 0 else [])
    mock.get(url__startswith=CMS_PRESC).mock(side_effect=lambda req: presc(req, 2023))
    mock.get(url__startswith=CMS_PRESC_PREV).mock(side_effect=lambda req: presc(req, 2022))
    mock.get(url__startswith=CMS_SPEND_D).mock(
        side_effect=lambda req: httpx.Response(200, json=spend_rows() if req.url.params.get("offset") == "0" else []))
    mock.get(url__startswith=CMS_SPEND_B).respond(200, json=[])

    def fda(request: httpx.Request) -> httpx.Response:
        path, search = request.url.path, request.url.params.get("search", "")
        if path.endswith("/label.json"):
            if "indications_and_usage" in search:
                return httpx.Response(200, json={"results": [label("palbociclib", "Ibrance"),
                                                             label("ribociclib", "Kisqali")]})
            m = re.search(r'generic_name:"([^"]+)"', search)
            g = (m.group(1) if m else "x").lower()
            return httpx.Response(200, json={"results": [label(g, g.title())]})
        if path.endswith("/drugsfda.json"):
            return httpx.Response(200, json={"results": [{"application_number": "NDA207103", "sponsor_name": "PFIZER",
                                                          "submissions": [{"submission_type": "ORIG",
                                                                           "submission_status": "AP",
                                                                           "submission_status_date": "20150203"}]}]})
        if path.endswith("/event.json"):
            return httpx.Response(200, json={"meta": {"results": {"total": 4321}}, "results": [{}]})
        return httpx.Response(404)
    mock.get(url__startswith="https://api.fda.gov/drug/").mock(side_effect=fda)

    mock.get(url__startswith="https://clinicaltrials.gov/api/v2/studies").respond(200, json={"studies": [
        {"protocolSection": {"identificationModule": {"nctId": "NCT00000001", "briefTitle": "Phase 3 of drug X"},
                             "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": "2024-03"},
                                              "primaryCompletionDateStruct": {"date": "2027-06"}},
                             "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 600}},
                             "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Bio"}},
                             "armsInterventionsModule": {"interventions": [{"name": "Camizestrant"}, {"name": "Placebo"}]},
                             "outcomesModule": {"primaryOutcomes": [{"measure": "Progression-free survival"}]}}}]})
    mock.get(url__startswith="https://rxnav.nlm.nih.gov/REST/rxcui.json").respond(
        200, json={"idGroup": {"rxnormId": ["1601374"]}})
    mock.get(url__regex=r"https://rxnav\.nlm\.nih\.gov/REST/rxcui/\d+/related\.json.*").respond(
        200, json={"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"rxcui": "1601380"}]}]}})
    mock.get(url__startswith="https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json").respond(200, json={
        "rxclassDrugInfoList": {"rxclassDrugInfo": [{"rxclassMinConceptItem": {"classId": "L01EF",
                                                                               "className": "CDK inhibitors"}}]}})
    mock.get(url__startswith="https://rxnav.nlm.nih.gov/REST/rxclass/classMembers.json").respond(200, json={
        "drugMemberGroup": {"drugMember": [{"minConcept": {"name": "palbociclib"}},
                                           {"minConcept": {"name": "ribociclib"}},
                                           {"minConcept": {"name": "abemaciclib"}}]}})
    mock.get(url__startswith="https://data.cdc.gov/resource/").respond(200, json=[
        {"stateabbr": st, "data_value": str(5 + i), "totalpopulation": "100000", "year": "2022"}
        for i, st in enumerate(["MA", "TX", "FL", "CO", "WA"])])

    def acs(request: httpx.Request) -> httpx.Response:
        if "subject" in request.url.path:
            return httpx.Response(200, json=[["NAME", "S2701_C05_001E", "state"], ["Massachusetts", "2.5", "25"],
                                             ["Texas", "16.6", "48"], ["Florida", "11.2", "12"], ["Colorado", "7.5", "08"],
                                             ["Washington", "6.1", "53"]])
        hdr = ["NAME", "B01001_001E"] + [f"B01001_{i:03d}E" for i in list(range(20, 26)) + list(range(44, 50))] + ["state"]
        rows = [hdr]
        for name, fips, pop in (("Massachusetts", "25", 7000000), ("Texas", "48", 30000000), ("Florida", "12", 22000000),
                                ("Colorado", "08", 5800000), ("Washington", "53", 7800000)):
            rows.append([name, str(pop)] + [str(pop // 70)] * 12 + [fips])
        return httpx.Response(200, json=rows)
    mock.get(url__startswith="https://api.census.gov/data/2023/acs/").mock(side_effect=acs)
    mock.get(url__startswith="https://api.census.gov/data/timeseries/intltrade/").respond(200, json=[
        ["CTY_CODE", "CTY_NAME", "GEN_VAL_YR", "I_COMMODITY", "COMM_LVL", "time"],
        ["-", "TOTAL FOR ALL COUNTRIES", "200000000000", "30", "HS2", "2025-12"],
        ["4190", "IRELAND", "50000000000", "30", "HS2", "2025-12"],
        ["4280", "GERMANY", "30000000000", "30", "HS2", "2025-12"],
        ["4419", "SWITZERLAND", "20000000000", "30", "HS2", "2025-12"]])
    mock.get(url__startswith="https://www.federalregister.gov/api/v1/documents.json").respond(200, json={"results": [
        {"title": "Pharmacy Benefit Manager Transparency for Part D", "type": "Proposed Rule",
         "abstract": "Proposes disclosure requirements for pharmacy benefit managers in Medicare Part D plans.",
         "document_number": "2026-01234", "html_url": "https://www.federalregister.gov/d/2026-01234",
         "publication_date": "2026-11-10", "agencies": [{"name": "Centers for Medicare & Medicaid Services"}],
         "effective_on": None, "comments_close_on": "2027-01-09"},
        {"title": "Adjusting Imports of Pharmaceuticals (Section 232)", "type": "Presidential Document",
         "abstract": "Tariff action on pharmaceutical imports.", "document_number": "2026-00999",
         "html_url": "https://www.federalregister.gov/d/2026-00999", "publication_date": "2026-04-02",
         "agencies": [{"name": "Executive Office of the President"}], "effective_on": None,
         "comments_close_on": None}]})
    mock.get(url__startswith="https://eutils.ncbi.nlm.nih.gov/").respond(200, json={"esearchresult": {"count": "42"}})

    # LLM providers
    mock.get("https://api.anthropic.com/v1/models").mock(side_effect=lambda req: httpx.Response(
        200 if req.headers.get("x-api-key", "").startswith("sk-ant-good") or
        req.headers.get("x-api-key") == "sk-ant-test-default-0000" else 401,
        json={"data": [{"id": "claude-haiku-5-5"}, {"id": "claude-sonnet-5-5"}]}))
    mock.post("https://api.anthropic.com/v1/messages").mock(side_effect=_fake_claude)
    mock.get("https://api.openai.com/v1/models").mock(side_effect=lambda req: httpx.Response(
        200 if req.headers.get("authorization", "").startswith("Bearer sk-proj-good") else 401,
        json={"data": [{"id": "gpt-5"}, {"id": "gpt-5-mini"}, {"id": "text-embedding-3-small"}]}))
    mock.post("https://api.openai.com/v1/chat/completions").mock(side_effect=_fake_openai_chat)


# Test switch: set PROVIDER_FAIL["anthropic"] = (status, body) to simulate a provider error on the next calls.
PROVIDER_FAIL: dict[str, tuple[int, dict] | None] = {"anthropic": None, "openai": None}


def _fake_openai_chat(request: httpx.Request) -> httpx.Response:
    import json
    if PROVIDER_FAIL["openai"]:
        st, body = PROVIDER_FAIL["openai"]
        return httpx.Response(st, json=body)
    msg = json.loads(request.content)["messages"][-1]["content"]
    nums = re.findall(r"IPAY (\d{4})", msg)
    text = f"OpenAI answer: {nums[0]} [1]." if nums else "This is not in the available data."
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                     "usage": {"prompt_tokens": 900, "completion_tokens": 40}})


def _fake_claude(request: httpx.Request) -> httpx.Response:
    import json
    if PROVIDER_FAIL["anthropic"]:
        st, body = PROVIDER_FAIL["anthropic"]
        return httpx.Response(st, json=body)
    body = json.loads(request.content)
    if "temperature" in body:  # like the real API for current Claude models
        return httpx.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "`temperature` is deprecated for this model."}})
    msg = body["messages"][0]["content"]
    if "Executive Launch Brief" in msg or "executive brief" in msg:
        text = "## Situation\nThe class is growing [1].\n## Risks\nNo pinned evidence yet."
    else:
        nums = re.findall(r"coverage ([\d.]+)%", msg) or re.findall(r"IPAY (\d{4})", msg)
        text = (f"Answer: the figure is {nums[0]} [1].\n- Supported by the sources [1]." if nums
                else "This is not in the available data.")
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}],
                                     "usage": {"input_tokens": 1200, "output_tokens": 80}})


@pytest.fixture()
def api_mock():
    PROVIDER_FAIL.update(anthropic=None, openai=None)
    with respx.mock(assert_all_called=False) as mock:
        _api_routes(mock)
        yield mock


@pytest.fixture()
def client(tmp_path, api_mock):
    pg = os.environ.get("TEST_DATABASE_URL")
    if pg:  # CI PostgreSQL job: fresh schema per test
        import app.models  # noqa: F401
        dbmod.reset_engine(pg)
        dbmod.Base.metadata.drop_all(dbmod.get_engine())
    else:
        dbmod.reset_engine(f"sqlite:///{tmp_path / 'test.db'}")
    limiter.reset()
    from app.hubs import targeting
    targeting._cache.clear()
    from app.main import app
    with TestClient(app) as c:
        yield c


def csrf(c: TestClient, path: str = "/login") -> str:
    html = c.get(path).text
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert m, "csrf meta missing"
    return m.group(1)


def signup(c: TestClient, email: str = "ana@example.com") -> None:
    tok = csrf(c, "/signup")
    r = c.post("/signup", data={"csrf_token": tok, "name": "Ana Analyst", "email": email, "org": "Acme",
                                "title": "Director", "use_case": "Launch strategy", "password": "Str0ng-Passw0rd!",
                                "terms": "on"}, follow_redirects=False)
    assert r.status_code == 303, r.text


def login(c: TestClient, email: str, password: str):  # noqa: ANN201
    tok = csrf(c, "/login")
    return c.post("/login", data={"csrf_token": tok, "email": email, "password": password}, follow_redirects=False)


def approve_now(email: str) -> None:
    from sqlalchemy import select

    from app.models import User, utcnow
    with dbmod.session_factory()() as s:
        u = s.scalar(select(User).where(User.email == email))
        u.requested_at = utcnow() - timedelta(hours=25)
        s.commit()


def admin_login(c: TestClient) -> None:
    r = login(c, "admin@launchlens.app", "Admin-Pass-12345!")
    assert r.status_code == 303
    tok = csrf(c, "/account/password")
    r = c.post("/account/password", data={"csrf_token": tok, "current": "Admin-Pass-12345!",
                                           "new": "New-Admin-Pass-999!"}, follow_redirects=False)
    assert r.status_code == 303


def ready_user(c: TestClient, email: str = "ana@example.com") -> None:
    """Signed up, auto-approved, logged in, BYOK saved, CDK4/6 scope applied."""
    signup(c, email)
    approve_now(email)
    assert login(c, email, "Str0ng-Passw0rd!").status_code == 303
    tok = csrf(c, "/onboarding/ai-keys")
    r = c.post("/onboarding/ai-keys/byok", data={"csrf_token": tok, "provider": "anthropic",
                                                 "api_key": "sk-ant-good-key-1234", "back": "/app"},
               follow_redirects=False)
    assert r.status_code == 303
    r = c.post("/scope", data={"csrf_token": tok, "preset": "oncology-cdk46", "apply_preset": "1"},
               follow_redirects=False)
    assert r.status_code == 303
