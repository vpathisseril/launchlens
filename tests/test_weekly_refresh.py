"""Weekly refresh job: re-fetches every source once, warms all scopes, records status + freshness."""
from datetime import timedelta

from sqlalchemy import select

from app.core import db as dbmod
from app.data import weekly
from app.data.fetch import cached_json, force_refresh
from app.models import DataSource, IngestRun, SourceCache, utcnow
from tests.conftest import admin_login, csrf, ready_user


def test_weekly_refresh_warms_all_presets_and_marks_sources_fresh(client):
    ready_user(client)
    with dbmod.session_factory()() as db:
        job = weekly.run(db)
        assert job.status == "ok", job.error
        assert job.rows_in >= 5 and job.rows_out > 100  # 5 presets × ~27 views + policy feed
        fresh = {s.key: weekly.freshness(s) for s in db.scalars(select(DataSource))}
    for key in ("cms_catalog", "partd_prescriber_drug", "partd_spending", "openfda", "ctgov", "rxnorm", "places",
                "acs", "federal_register", "trade", "pubmed"):
        assert fresh[key] == "Fresh", (key, fresh[key])
    assert fresh["partd_formulary"] == "Manual" and fresh["negotiation_program"] == "Manual"


def test_force_refresh_refetches_each_request_once(client, api_mock):
    ready_user(client)
    url = "https://clinicaltrials.gov/api/v2/studies"
    with dbmod.session_factory()() as db:
        cached_json(db, "ctgov", url, {"q": "x"})
        before = api_mock.calls.call_count
        cached_json(db, "ctgov", url, {"q": "x"})  # fresh cache → no call
        assert api_mock.calls.call_count == before
        with force_refresh():
            cached_json(db, "ctgov", url, {"q": "x"})
            cached_json(db, "ctgov", url, {"q": "x"})  # only once per run
        assert api_mock.calls.call_count == before + 1


def test_overdue_after_eight_days(client):
    with dbmod.session_factory()() as db:
        src = db.scalar(select(DataSource).where(DataSource.key == "ctgov"))
        src.last_refreshed_at = utcnow() - timedelta(days=9)
        assert weekly.freshness(src) == "Overdue"
        src.last_refreshed_at = utcnow() - timedelta(days=2)
        assert weekly.freshness(src) == "Fresh"


def test_admin_data_page_shows_schedule_and_runs_job(client):
    admin_login(client)
    tok = csrf(client, "/admin/data")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    page = client.get("/admin/data").text
    assert "Run weekly refresh now" in page and "Freshness" in page and "Never" in page
    with dbmod.session_factory()() as db:
        weekly.run(db)
        assert db.scalar(select(IngestRun).where(IngestRun.source_key == "weekly_refresh")).status == "ok"
        assert db.scalar(select(SourceCache.id).limit(1))
    page = client.get("/admin/data").text
    assert "Last weekly run" in page and ">ok<" in page
