"""LaunchLens weekly pipeline — run once a week (Replit Scheduled Deployment, Sundays 5:00 AM ET).

Runs every maintenance step sequentially in one process:
preflight → approvals → policy_feed → baseline → warm_scopes → housekeeping → summary.

Usage:
    python -m scripts.weekly_pipeline            # optimal: downloads only what is due (default)
    python -m scripts.weekly_pipeline --full     # re-download everything once (after an outage or a data fix)
    python -m scripts.weekly_pipeline --dry-run  # show what would run, change nothing

Exit codes: 0 ok/partial (some sources unavailable), 1 failed, 2 skipped (another run in progress).
Uses no AI tokens. Bounded by WEEKLY_MAX_RUNTIME_S (default 1200) and WEEKLY_MAX_SCOPES (default 25).
"""
import argparse
import sys

from app.core.db import session_factory
from app.data import weekly
from app.data.fetch import REFRESH_DAYS
from app.main import init_db


def main() -> int:
    ap = argparse.ArgumentParser(description="LaunchLens weekly data pipeline")
    ap.add_argument("--full", action="store_true", help="re-download every source once (ignores cadence)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args()
    init_db()
    with session_factory()() as db:
        if args.dry_run:
            scopes = weekly.scopes_to_warm(db)
            print("steps: preflight → approvals → policy_feed → baseline → warm_scopes → housekeeping → summary")
            print(f"mode: {'full' if args.full else 'due'} · scopes: {len(scopes)} (cap {weekly.MAX_SCOPES}) · "
                  f"budget: {weekly.MAX_RUNTIME_S}s")
            print("refresh cadence (days): " + ", ".join(f"{k}={v}" for k, v in sorted(REFRESH_DAYS.items())))
            return 0
        job = weekly.run(db, mode="full" if args.full else "due")
        print(f"weekly pipeline: status={job.status}")
        if job.error:
            print(job.error)
        return {"ok": 0, "partial": 0, "skipped": 2}.get(job.status, 1)


if __name__ == "__main__":
    sys.exit(main())
