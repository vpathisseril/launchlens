"""SMTP status + admin test email (Gmail App Password guidance on auth failure)."""
import smtplib

from app.core.config import get_settings
from tests.conftest import admin_login, csrf


class FakeSMTP:
    fail_auth = False
    sent: list = []

    def __init__(self, host, port, timeout=15):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, pw):
        if FakeSMTP.fail_auth:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def _configure(monkeypatch):
    s = get_settings()
    for k, v in dict(smtp_host="smtp.gmail.com", smtp_port=587, smtp_user="vpathisseril@gmail.com",
                     smtp_password="abcd efgh ijkl mnop", smtp_from="vpathisseril@gmail.com",
                     admin_notify_email="vpathisseril@gmail.com").items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


def test_admin_test_email_success(client, monkeypatch):
    _configure(monkeypatch)
    FakeSMTP.fail_auth, FakeSMTP.sent = False, []
    admin_login(client)
    tok = csrf(client, "/account/password")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    page = client.get("/admin").text
    assert "Configured" in page and "smtp.gmail.com:587" in page
    page = client.post("/admin/test-email", data={"csrf_token": tok}).text
    assert "Sent to vpathisseril@gmail.com" in page and FakeSMTP.sent[-1]["To"] == "vpathisseril@gmail.com"


def test_admin_test_email_auth_failure_explains_app_password(client, monkeypatch):
    _configure(monkeypatch)
    FakeSMTP.fail_auth = True
    admin_login(client)
    tok = csrf(client, "/account/password")
    client.post("/onboarding/ai-keys/default", data={"csrf_token": tok, "understand": "on"})
    page = client.post("/admin/test-email", data={"csrf_token": tok}).text
    assert "App Password" in page
    FakeSMTP.fail_auth = False
