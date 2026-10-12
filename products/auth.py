"""Cookie-based UI authentication.

The UI gate used to live in the reverse proxy (Caddy ``basic_auth``): a browser
dialog on every device, no way to sign out, and unusable inside an iframe. It
now lives here as a plain login form:

* ``UI_PASSWORD`` is the only secret; it is the credential *and* the signature
  key, so rotating it invalidates every existing session.
* A successful login sets one cookie holding its own expiry, signed with an
  HMAC over that expiry. No server-side session store, no extra dependency.
* ``/mcp`` keeps its own Bearer token, ``/health`` stays public, and
  everything else redirects to ``/login`` when the cookie is missing, expired
  or forged.
"""

from __future__ import annotations

import hashlib
import hmac
import time

COOKIE_NAME = "products_session"
LOGIN_PATH = "/login"
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60

_EXEMPT_PATHS = frozenset({"/login", "/logout", "/favicon.ico", "/health", "/health/ready"})
_EXEMPT_PREFIXES = ("/mcp", "/static")


def is_exempt(path: str) -> bool:
    """True when ``path`` never needs a UI session (login form, static, MCP, health)."""
    return path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES)


def _signature(secret: str, expires: int) -> str:
    return hmac.new(secret.encode(), str(expires).encode(), hashlib.sha256).hexdigest()


def issue_token(secret: str, *, now: float | None = None) -> str:
    """Mint a session token: its expiry, signed with the configured password."""
    expires = int(time.time() if now is None else now) + SESSION_TTL_SECONDS
    return f"{expires}.{_signature(secret, expires)}"


def token_is_valid(secret: str, token: str | None, *, now: float | None = None) -> bool:
    """True when ``token`` is a signature-valid, not yet expired token for ``secret``."""
    if not secret or not token:
        return False
    expires_text, separator, signature = token.partition(".")
    if not separator or not expires_text.isdigit() or not signature:
        return False
    if not hmac.compare_digest(_signature(secret, int(expires_text)), signature):
        return False
    return int(expires_text) > int(time.time() if now is None else now)


def password_matches(secret: str, candidate: str) -> bool:
    """Constant-time password check; an unconfigured password never matches."""
    if not secret:
        return False
    return hmac.compare_digest(secret, candidate)


def safe_next(target: str | None) -> str:
    """Where a fresh login continues: a local path, never an absolute URL."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/cards"
