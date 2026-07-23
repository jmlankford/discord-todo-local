"""
Auth for the TaskBot web UI.

- Shared password from APP_PASSWORD (env). Constant-time comparison.
- Login state lives in Flask's signed session cookie (HttpOnly, Secure,
  SameSite=Lax) — see app.py config. SESSION_SECRET signs it.
- Per-IP failed-login rate limiting with lockout after MAX_FAILS.

Nothing here logs, echoes, or returns the password or the secret.
"""

import hmac
import os
import threading
import time
from functools import wraps

from flask import redirect, request, session, url_for

# ── Config from env (never hardcoded) ─────────────────────────────────────────
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# ── Rate limiting ─────────────────────────────────────────────────────────────
MAX_FAILS = 5              # failures allowed before lockout
LOCKOUT_SECONDS = 15 * 60  # how long an IP stays locked after MAX_FAILS
WINDOW_SECONDS = 15 * 60   # failures older than this are forgotten

_lock = threading.Lock()
# ip -> {"fails": [timestamps], "locked_until": epoch}
_attempts = {}


def _now():
    return time.time()


def client_ip():
    """
    Real client IP. Behind NPM + Cloudflare Tunnel, request.remote_addr is the
    proxy, so we trust the forwarded chain (ProxyFix is applied in app.py) and
    fall back to remote_addr.
    """
    return request.remote_addr or "unknown"


def lockout_remaining(ip=None):
    """Seconds remaining on an IP's lockout, or 0 if not locked."""
    ip = ip or client_ip()
    with _lock:
        rec = _attempts.get(ip)
        if not rec:
            return 0
        remaining = rec.get("locked_until", 0) - _now()
        return int(remaining) if remaining > 0 else 0


def record_failure(ip=None):
    """Register a failed login. Returns seconds locked (0 if not yet locked)."""
    ip = ip or client_ip()
    now = _now()
    with _lock:
        rec = _attempts.setdefault(ip, {"fails": [], "locked_until": 0})
        # Drop failures outside the sliding window
        rec["fails"] = [t for t in rec["fails"] if now - t < WINDOW_SECONDS]
        rec["fails"].append(now)
        if len(rec["fails"]) >= MAX_FAILS:
            rec["locked_until"] = now + LOCKOUT_SECONDS
            rec["fails"] = []
            return LOCKOUT_SECONDS
    return 0


def record_success(ip=None):
    """Clear an IP's failure history on successful login."""
    ip = ip or client_ip()
    with _lock:
        _attempts.pop(ip, None)


def check_password(provided: str) -> bool:
    """Constant-time comparison against APP_PASSWORD."""
    if not APP_PASSWORD or provided is None:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), APP_PASSWORD.encode("utf-8"))


def is_authed() -> bool:
    return bool(session.get("authed"))


def login_required(view):
    """Redirect unauthenticated requests to the login page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authed():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped
