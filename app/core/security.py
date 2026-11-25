"""Password hashing (argon2id), API-key encryption (Fernet), CSRF tokens and a small in-memory rate limiter."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_ph = PasswordHasher()  # argon2id defaults


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_strength_ok(pw: str) -> tuple[bool, str]:
    if len(pw) < 12:
        return False, "Use at least 12 characters."
    classes = sum([any(c.islower() for c in pw), any(c.isupper() for c in pw),
                   any(c.isdigit() for c in pw), any(not c.isalnum() for c in pw)])
    if classes < 3:
        return False, "Mix at least three of: lowercase, uppercase, numbers, symbols."
    return True, ""


_dev_fernet_key = Fernet.generate_key()


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        if get_settings().is_prod:
            raise RuntimeError("FERNET_KEY must be set in production")
        return Fernet(_dev_fernet_key)
    return Fernet(key.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


def mask_key(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) >= 4 else "••••"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def csrf_valid(session_token: str | None, submitted: str | None) -> bool:
    return bool(session_token and submitted and secrets.compare_digest(session_token, submitted))


class RateLimiter:
    """Sliding-window limiter keyed by (bucket, client). Good enough for a single-instance prototype (ADR-005)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
