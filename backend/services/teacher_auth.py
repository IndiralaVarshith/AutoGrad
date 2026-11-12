"""One shared teacher password, from the TEACHER_PASSWORD environment setting.

Logging in sets a signed, HttpOnly cookie, so page images and downloads work too. With no
password set, every protected route refuses: the app never runs open by accident.

Sessions belong to one run of the server. Each start makes a new secret, so starting the
app again always begins at the sign-in page, and the cookie also ends when the browser
closes.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

COOKIE_NAME = "autograde_session"
SESSION_SECONDS = 12 * 3600
# New on every start of the server: sessions from an earlier run stop working.
_RUN_SECRET = secrets.token_hex(16)


def _password() -> str:
    return os.getenv("TEACHER_PASSWORD", "")


def is_configured() -> bool:
    return bool(_password())


def password_matches(attempt: str) -> bool:
    expected = _password()
    return bool(expected) and hmac.compare_digest(attempt.encode(), expected.encode())


def _signature(expires: str) -> str:
    key = f"{_password()}:{_RUN_SECRET}".encode()
    return hmac.new(key, f"autograde-session:{expires}".encode(), hashlib.sha256).hexdigest()


def new_session(now: float | None = None) -> str:
    expires = str(int((now if now is not None else time.time()) + SESSION_SECONDS))
    return f"{expires}.{_signature(expires)}"


def session_is_valid(token: str | None, now: float | None = None) -> bool:
    if not token or not is_configured():
        return False
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or not hmac.compare_digest(signature, _signature(expires)):
        return False
    return int(expires) > (now if now is not None else time.time())
