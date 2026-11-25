"""Daily job (Replit Scheduled Deployment): refresh the policy feed and raise watchlist alerts."""
from app.core.db import session_factory
from app.hubs.policy import refresh_policy
from app.main import init_db

if __name__ == "__main__":
    init_db()
    with session_factory()() as db:
        print("policy refresh:", refresh_policy(db))
