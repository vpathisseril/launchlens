"""SMTP adapter. With no SMTP_HOST configured, messages are logged (dev/test) instead of sent."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("launchlens.mail")
outbox: list[EmailMessage] = []  # inspected by tests


def smtp_status() -> dict[str, str | bool]:
    s = get_settings()
    return {"configured": bool(s.smtp_host and s.smtp_user and s.smtp_password), "host": s.smtp_host,
            "port": str(s.smtp_port), "user": s.smtp_user, "from": s.smtp_from,
            "notify": s.admin_notify_email or s.admin_email}


def send_mail(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Returns (sent, detail). Never raises: email problems must not break sign-up or approvals."""
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body + "\n\n— LaunchLens · By Varghese Pathisseril")
    if not s.smtp_host:
        outbox.append(msg)
        log.info("mail (not sent, no SMTP configured) to=%s subject=%s", to, subject)
        return False, "SMTP is not configured (SMTP_HOST is empty)."
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True, f"Sent to {to}."
    except smtplib.SMTPAuthenticationError:
        log.warning("mail auth failed for %s", s.smtp_user)
        return False, ("The mail server rejected the username/password. For Gmail, SMTP_PASSWORD must be a 16-character "
                       "App Password (Google Account → Security → 2-Step Verification → App passwords), not your normal "
                       "Gmail password.")
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("mail send failed: %s", exc)
        return False, f"Couldn't send: {type(exc).__name__}. Check SMTP_HOST ({s.smtp_host}) and SMTP_PORT ({s.smtp_port})."
