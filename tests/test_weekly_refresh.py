"""Weekly pipeline: sequential steps, publisher-aware 'due' refresh, single-flight, zero AI tokens."""
from datetime import timedelta

from sqlalchemy import select

from app.core import db as dbmod
from app.data import weekly
from app.data.fetch import cached_json, force_refresh
from app.models import DataSource, IngestRun, SourceCache, utcnow
from tests.conftest import admin_login, csrf, ready_user


def test_pipeline_runs_all_steps_and_marks_sources_fresh(client):
    ready_user(client)
    with dbmod.session_factory()() as db:
        job = weekly.run(db)
        assert job.status == "ok", job.error
        assert job.rows_in >= 5 and job.rows_out > 100
        summary, steps = job.error.splitlines()[:2]
        assert "llm_calls=0" in summary and "mode=due" in summary
        for step in ("preflight", "approvals", "policy_feed", "baseline", "warm_scopes", "housekeeping"):
            assert f"{step}:ok" in steps
        fresh = {s.key: weekly.freshness(s) for s in db.scalars(select(DataSource))}
    for key in ("cms_catalog", "partd_prescriber_drug", "partd_spending", "openfda", "ctgov", "rxnorm", "places",
                "acs", "federal_register", "trade", "pubmed"):
        assert fresh[key] == "Fresh", (key, fresh[key])
    assert fresh["partd_formulary"] == "Manual"


def test_second_run_reuses_cache_instead_of_downloading(client):
    ready_user(client)
    with dbmod.session_factory()() as db:
        weekly.run(db)
        second = weekly.run(db)
    summary = second.error.splitlines()[0]
    downloads = int(summary.split("downloads=")[1].split()[0])
    reused = int(summary.split("reused_cache=")[1].split()[0])
    assert reused > 50 and downloads < reused / 5, summary  # only short-cadence policy queries re-download


def test_due_mode_refetches_only_stale_and_full_mode_once(client, api_mock):
    url = "https://clinicaltrials.gov/api/v2/studies"
    with dbmod.session_factory()() as db:
        cached_json(db, "ctgov", url, {"q": "x"})
        n0 = api_mock.calls.call_count
        with force_refresh("due"):
            cached_json(db, "ctgov", url, {"q": "x"})  # fresh (< 7 days) → reused
        assert api_mock.calls.call_count == n0
        row = db.scalar(select(SourceCache).where(SourceCache.source_key == "ctgov"))
        row.retrieved_at = utcnow() - timedelta(days=8)
        db.commit()
        with force_refresh("due"):
            cached_json(db, "ctgov", url, {"q": "x"})  # stale → re-downloaded
            cached_json(db, "ctgov", url, {"q": "x"})  # at most once per run
        assert api_mock.calls.call_count == n0 + 1
        with force_refresh("full"):
            cached_json(db, "ctgov", url, {"q": "x"})
        assert api_mock.calls.call_count == n0 + 2


def test_freshness_follows_each_sources_cadence(client):
    with dbmod.session_factory()() as db:
        ct = db.scalar(select(DataSource).where(DataSource.key == "ctgov"))
        acs = db.scalar(select(DataSource).where(DataSource.key == "acs"))
        ct.last_refreshed_at = acs.last_refreshed_at = utcnow() - timedelta(days=20)
        assert weekly.freshness(ct) == "Overdue"  # weekly source
        assert weekly.freshness(acs) == "Fresh"  # annual source, 90-day cadence


def test_single_flight_skips_overlapping_run(client):
    with dbmod.session_factory()() as db:
        db.add(IngestRun(source_key=weekly.JOB_KEY, status="running"))
        db.commit()
        assert weekly.run(db).status == "skipped"


def test_admin_data_page_and_buttons(client):
    admin_login(client)
    tok = csrf(client, "/admin/data")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    page = client.get("/admin/data").text
    assert "Run weekly refresh now" in page and "Full reload" in page and "0 AI tokens" in page
    with dbmod.session_factory()() as db:
        weekly.run(db)
    page = client.get("/admin/data").text
    assert "Last weekly run" in page and "llm_calls=0" in page
