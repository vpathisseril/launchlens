"""SMTP adapter. With no SMTP_HOST configured, messages are logged (dev/test) instead of sent."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("launchlens.mail")
outbox: list[EmailMessage] = []  # inspected by tests


def send_mail(to: str, subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body + "\n\n— LaunchLens · By Varghese Pathisseril")
    if not s.smtp_host:
        outbox.append(msg)
        log.info("mail (not sent, no SMTP configured) to=%s subject=%s", to, subject)
        return
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("mail send failed: %s", exc)
