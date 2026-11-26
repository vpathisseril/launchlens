"""Admin recovery without email (run in the Replit Shell).

    python -m scripts.admin_recovery --list            # show admin sign-in emails
    python -m scripts.admin_recovery --reset           # apply ADMIN_RESET_PASSWORD now (no restart needed)

The new password is read from the ADMIN_RESET_PASSWORD secret, never typed on the command line or printed.
Note: this changes the database the Shell is connected to. For the live (published) app, set the secret and
republish instead; the app applies it on start.
"""
import argparse
import sys

from app.core.config import get_settings
from app.core.db import session_factory
from app.main import init_db
from app.services.accounts import admin_emails, apply_admin_reset


def main() -> int:
    ap = argparse.ArgumentParser(description="LaunchLens admin recovery")
    ap.add_argument("--list", action="store_true", help="list admin emails")
    ap.add_argument("--reset", action="store_true", help="apply ADMIN_RESET_PASSWORD to ADMIN_EMAIL")
    a = ap.parse_args()
    init_db()  # init_db already applies a new ADMIN_RESET_PASSWORD once
    with session_factory()() as db:
        if a.list or not a.reset:
            print("Admin accounts:", ", ".join(admin_emails(db)) or "(none)")
            print("ADMIN_EMAIL secret:", get_settings().admin_email)
        if a.reset:
            if not get_settings().admin_reset_password:
                print("Set the ADMIN_RESET_PASSWORD secret first.")
                return 1
            applied = apply_admin_reset(db)
            print("Password reset for", get_settings().admin_email,
                  "(applied now)" if applied else "(already applied for this secret value)")
            print("Sign in, choose a new password when asked, then delete ADMIN_RESET_PASSWORD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
