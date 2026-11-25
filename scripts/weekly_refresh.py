"""Weekly job (Replit Scheduled Deployment, e.g. Sundays 5:00 AM ET): refresh every public source + policy feed."""
import sys

from app.core.db import session_factory
from app.data.weekly import run
from app.main import init_db

if __name__ == "__main__":
    init_db()
    with session_factory()() as db:
        job = run(db)
        print(f"weekly refresh: status={job.status} scopes={job.rows_in} views_ok={job.rows_out}")
        if job.error:
            print(job.error)
        sys.exit(0 if job.status in ("ok", "partial") else 1)
