# Weekly Data Pipeline — Setup & Operations
By Varghese Pathisseril · VP+AI

One command keeps every public data source current, once a week, with bounded resources and **zero AI tokens**:

```
python -m scripts.weekly_pipeline
```

## What it runs (sequentially, in one process)
| # | Step | What it does | Typical time |
|---|------|--------------|--------------|
| 1 | preflight | Checks the database; notes missing optional keys (Census, openFDA, Regulations.gov) | <1 s |
| 2 | approvals | Auto-approves sign-ups past their 24 h window (backs up the hourly job) | <1 s |
| 3 | policy_feed | Federal Register (+ Regulations.gov if keyed) → policy items, watchlist alerts | 5–15 s |
| 4 | baseline | CMS catalog, Part D/B spending, Census ACS, Census trade | 10–40 s |
| 5 | warm_scopes | Every hub view for the 5 starter scopes + every saved user scope (max 25), one at a time | 1–3 min |
| 6 | housekeeping | Deletes source-cache rows older than 180 days, AI-response cache older than 7 days, read alerts older than 90 days | <1 s |
| 7 | summary | Writes the run record shown in Admin → Data sources | <1 s |

A failing step is recorded and the pipeline continues. Exit codes: `0` ok/partial · `1` failed · `2` skipped (another run in progress).

## Why it's optimal
- **Downloads only what is due.** Each source has a refresh cadence that matches its publisher. Anything fresher is reused from the cache:

  | Cadence | Sources |
  |---|---|
  | 7 days | Federal Register, Regulations.gov, ClinicalTrials.gov, openFDA, CMS catalog |
  | 30 days | RxNorm, PubMed, Census trade, CMS Part D/B spending |
  | 90 days | CDC PLACES, Census ACS, CMS Part D prescribers |

  A new CMS data year has a **new dataset URL**, which the weekly catalog check finds immediately, so old years are never re-downloaded.
- **At most one download per request per run.** PubMed and openFDA requests are paced to stay within their rate limits; CMS row caps still apply.
- **Bounded.** A single-flight lock prevents overlapping runs. There's a 20-minute time budget (`WEEKLY_MAX_RUNTIME_S`) and a 25-scope cap (`WEEKLY_MAX_SCOPES`). Memory is released between scopes.
- **No AI tokens.** The pipeline never calls an LLM. Each run records `llm_calls=0` in its summary line.
- **After the first run, later weeks mostly reuse the cache.** Only the short-cadence policy and trial queries re-download, so a weekly run is typically under a minute.

Options: `--full` re-downloads everything once (after an outage or a data fix) · `--dry-run` prints the plan without changing anything.

---

## Step-by-step setup on Replit (one time, about 10 minutes)

### Step 1 — Add the Census key (2 min)
1. Request a free key at **https://api.census.gov/data/key_signup.html**. It arrives by email, usually within minutes.
2. In Replit, open the **LaunchLens** app → **Tools → Secrets → New secret**.
3. Key: `CENSUS_API_KEY`, Value: the key from the email → **Add secret**.
4. Optional, same way: `OPENFDA_API_KEY` (https://open.fda.gov/apis/authentication/) and `REGULATIONS_API_KEY` (https://api.data.gov/signup/).

### Step 2 — Republish the web app (1 min)
1. Click **Deploy / Publish** at the top right → **Republish**, so the live app picks up the new secrets and code.
2. Wait for **Status: Live**, then open `https://svelte-haunting-technology.replit.app/health`. It should show `{"status":"ok"}`.

### Step 3 — Create the weekly Scheduled Deployment (3 min)
1. In the Replit workspace, open **Deployments** (rocket icon) → **New deployment** (or **+** / **Add deployment**) → choose **Scheduled**.
   Create it **alongside** the existing Autoscale web deployment. Don't replace it.
2. Enter:

   | Field | Value |
   |---|---|
   | Name | `LaunchLens weekly data pipeline` |
   | Schedule | `Every Sunday at 5:00 AM`, or cron `0 5 * * 0` |
   | Time zone | `America/New_York` |
   | Build command | `pip install -r requirements.txt` |
   | Run command | `python -m scripts.weekly_pipeline` |
   | Timeout | 30 minutes |
   | Machine | The smallest option is enough (the job is light) |

3. Make sure the deployment uses the **same Secrets** as the web app (`DATABASE_URL`, `SECRET_KEY`, `FERNET_KEY`, `CENSUS_API_KEY`, …).
4. Click **Deploy**.

### Step 4 — Run it once now and verify (3 min)
1. Open the live app → sign in as admin → **Admin → Data sources** → **Run weekly refresh now**.
2. After about 2–3 minutes, refresh the page and check:
   - **Last weekly run**: status `ok` (or `partial` if a public API was briefly down).
   - The summary line shows `llm_calls=0` and the `downloads=` / `reused_cache=` counts.
   - Every automated source shows a **Fresh** badge. The formulary and negotiation sources show **Manual**.
3. In Replit → Deployments → the scheduled deployment → **Logs**, the next Sunday's run should end with
   `weekly pipeline: status=ok`.

### Step 5 — Optional hourly approval sweep
Add a second Scheduled deployment: run command `python -m scripts.approval_sweep`, schedule `every hour`. The weekly
pipeline also sweeps approvals, so this is only needed for faster auto-approval.

---

## Manual data (not automated, by design)
| Data | When | Where |
|---|---|---|
| Part D formulary (Access hub) | Quarterly, after CMS posts a new file | Admin → Data sources → Load Part D formulary (official cms.gov ZIP URL) |
| Medicare negotiation lists and prices | After CMS announcements (~Feb 1 selected list; ~Nov 30 negotiated prices) | Admin → Data sources → Medicare negotiation table |
| CDC PLACES / ACS release IDs | Once a year when a new release ships | Secrets `PLACES_DATASET_ID`, `ACS_YEAR` |

## Troubleshooting
| Symptom | Fix |
|---|---|
| `status=skipped` | Another run is still in progress. Wait for it to finish. |
| `acs`/`trade` errors mentioning a missing key | Add `CENSUS_API_KEY` (Step 1) |
| `warm_scopes: time budget reached` | Raise `WEEKLY_MAX_RUNTIME_S` or lower `WEEKLY_MAX_SCOPES` |
| A source shows **Overdue** | Click **Run weekly refresh now**; if it keeps failing, see the error under "Last run notes" |
| Data looks wrong after a publisher fix | **Full reload** (or `--full`) once |
