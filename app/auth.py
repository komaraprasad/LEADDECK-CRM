"""Password hashing (stdlib scrypt), admin seeding, and a small in-memory login rate limiter."""
from __future__ import annotations
import hashlib
import hmac
import os
import re
import time
from collections import defaultdict, deque
from app.db import Database, now_iso

_N, _R, _P = 2 ** 14, 8, 1
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def create_user(db: Database, email: str, password: str, role: str = "user") -> int:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("invalid email")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if role not in ("admin", "user"):
        raise ValueError("invalid role")
    if db.one("SELECT 1 AS x FROM users WHERE email=?", (email,)):
        raise ValueError("user already exists")
    return db.insert("users", {"email": email, "password_hash": hash_password(password), "role": role, "created_at": now_iso()})


def seed_admin(db: Database, email: str, password: str) -> bool:
    """Create the first admin from environment settings, only when no users exist."""
    if db.one("SELECT COUNT(*) AS n FROM users")["n"] > 0 or not email or not password:
        return False
    create_user(db, email, password, "admin")
    return True


def authenticate(db: Database, email: str, password: str) -> dict | None:
    u = db.one("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),))
    # verify against a dummy hash when the user is missing, so timing does not reveal valid emails
    ok = verify_password(password or "", u["password_hash"] if u else _DUMMY)
    return u if (u and ok) else None


_DUMMY = hash_password("not-a-real-password")


class RateLimiter:
    def __init__(self, limit=5, window=60):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
