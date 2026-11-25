"""Hourly job (Replit Scheduled Deployment): auto-approve pending users whose approval window has passed."""
from app.core.db import session_factory
from app.main import init_db
from app.services.accounts import auto_approve_due

if __name__ == "__main__":
    init_db()
    with session_factory()() as db:
        print(f"auto-approved: {auto_approve_due(db)}")
