"""M0 — signup, approval (manual / immediate / after window), module grants, password reset."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, new_token, sha256
from app.models import Module, PasswordReset, Role, Setting, User, UserModule, UserStatus, utcnow
from app.services.audit import audit
from app.services.mailer import send_mail

APPROVAL_MODES = ("MANUAL", "AUTO_IMMEDIATE", "AUTO_AFTER_24H")


def get_setting(db: Session, key: str, default: Any) -> Any:
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def approval_mode(db: Session) -> str:
    return str(get_setting(db, "approval_mode", "AUTO_AFTER_24H"))


def approval_window_hours(db: Session) -> int:
    return int(get_setting(db, "approval_window_hours", 24))


def create_signup(db: Session, *, email: str, name: str, org: str, title: str, use_case: str, password: str,
                  ip: str = "") -> User | None:
    """Returns None when the email already exists (caller shows a generic message)."""
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        return None
    user = User(email=email, name=name.strip(), org=org.strip(), title=title.strip(), use_case=use_case,
                password_hash=hash_password(password))
    db.add(user)
    db.flush()
    audit(db, None, "signup", "user", user.id, after={"email": email}, ip=ip)
    if approval_mode(db) == "AUTO_IMMEDIATE":
        approve(db, user, actor_id=None, source="auto")
    else:
        notify = get_settings().admin_notify_email or get_settings().admin_email
        send_mail(notify, "LaunchLens: new access request",
                  f"{name} <{email}> ({title}, {org}) requested access. Review: {get_settings().base_url}/admin/approvals")
    db.commit()
    return user


def grant_default_modules(db: Session, user: User, actor_id: int | None) -> None:
    have = {um.module_id for um in user.modules}
    for m in db.scalars(select(Module).where(Module.is_default_on_approval.is_(True), Module.is_active.is_(True))):
        if m.id not in have:
            db.add(UserModule(user_id=user.id, module_id=m.id, granted_by=actor_id))


def approve(db: Session, user: User, actor_id: int | None, source: str) -> None:
    before = {"status": user.status.value}
    user.status = UserStatus.approved
    user.approved_at = utcnow()
    user.approved_by = actor_id
    user.approval_source = source
    grant_default_modules(db, user, actor_id)
    audit(db, actor_id, "approve", "user", user.id, before=before, after={"status": "approved", "source": source})
    send_mail(user.email, "Your LaunchLens access is approved",
              f"Hi {user.name}, your access is approved. Sign in: {get_settings().base_url}/login")


def reject(db: Session, user: User, actor_id: int, reason: str) -> None:
    before = {"status": user.status.value}
    user.status = UserStatus.rejected
    audit(db, actor_id, "reject", "user", user.id, before=before, after={"status": "rejected", "reason": reason})


def auto_approve_due(db: Session, user: User | None = None) -> int:
    """Approves pending users older than the window when mode is AUTO_AFTER_24H. Idempotent."""
    if approval_mode(db) != "AUTO_AFTER_24H":
        return 0
    cutoff = utcnow() - timedelta(hours=approval_window_hours(db))
    q = select(User).where(User.status == UserStatus.pending, User.requested_at <= cutoff)
    if user is not None:
        q = q.where(User.id == user.id)
    n = 0
    for u in db.scalars(q).all():
        approve(db, u, actor_id=None, source="auto")
        n += 1
    if n:
        db.commit()
    return n


def decision_due_at(db: Session, user: User):  # noqa: ANN201
    return user.requested_at + timedelta(hours=approval_window_hours(db))


def ensure_admin(db: Session) -> None:
    s = get_settings()
    if db.scalar(select(User).where(User.role == Role.admin)):
        return
    pw = s.admin_password or ("Dev-Admin-Pass-123!" if not s.is_prod else "")
    if not pw:
        return
    admin = User(email=s.admin_email.lower(), name="Administrator", password_hash=hash_password(pw), role=Role.admin,
                 status=UserStatus.approved, approved_at=utcnow(), approval_source="admin",
                 must_change_password=True)
    db.add(admin)
    db.commit()


def start_password_reset(db: Session, email: str) -> None:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user:
        return
    token = new_token()
    db.add(PasswordReset(user_id=user.id, token_hash=sha256(token), expires_at=utcnow() + timedelta(minutes=30)))
    db.commit()
    send_mail(user.email, "Reset your LaunchLens password",
              f"Use this link within 30 minutes: {get_settings().base_url}/reset/{token}")


def complete_password_reset(db: Session, token: str, new_password: str) -> bool:
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == sha256(token)))
    if not row or row.used or row.expires_at < utcnow():
        return False
    user = db.get(User, row.user_id)
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    row.used = True
    audit(db, user.id, "password_reset", "user", user.id)
    db.commit()
    return True
