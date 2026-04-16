"""Authentication helpers — single-user session-based auth.

Environment variables:
- QBR_AUTH_USER: username (default: "director")
- QBR_AUTH_PASSWORD_HASH: bcrypt hash of the password
- QBR_SESSION_SECRET: secret for signing session cookies

If QBR_AUTH_PASSWORD_HASH is not set, auth is DISABLED (dev mode).
"""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict

import bcrypt

# Rate limiting: {ip: [(timestamp, ...), ...]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes


def auth_enabled() -> bool:
    """Auth is enabled only if QBR_AUTH_PASSWORD_HASH env var is set."""
    return bool(os.getenv("QBR_AUTH_PASSWORD_HASH"))


def get_session_secret() -> str:
    """Get session secret from env, or generate a random one (dev only)."""
    secret = os.getenv("QBR_SESSION_SECRET")
    if not secret:
        # Dev mode: stable random secret per-process
        secret = secrets.token_urlsafe(32)
    return secret


def hash_password(plaintext: str) -> str:
    """Generate a bcrypt hash for the given plaintext password."""
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_credentials(username: str, password: str) -> bool:
    """Check username/password against env vars. Returns True if valid."""
    expected_user = os.getenv("QBR_AUTH_USER", "director")
    expected_hash = os.getenv("QBR_AUTH_PASSWORD_HASH", "")

    if not expected_hash:
        return False  # Auth not configured
    if username != expected_user:
        return False
    try:
        return bcrypt.checkpw(password.encode(), expected_hash.encode())
    except ValueError:
        return False


def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is under the rate limit (allowed)."""
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
    return len(_login_attempts[ip]) < RATE_LIMIT_MAX


def record_login_attempt(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _login_attempts[ip].append(time.monotonic())


# Paths that don't require authentication
PUBLIC_PATHS = {"/login", "/logout", "/healthz"}
PUBLIC_PREFIXES = ("/static/",)


def is_public_path(path: str) -> bool:
    """Check if a path is publicly accessible (no auth required)."""
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)
