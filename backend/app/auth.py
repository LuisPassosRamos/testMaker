"""School access code.

Once the site is on the internet it must not be open to anyone: the secretary sets a
school code (``ACCESS_CODE``) and shares it with the teachers. Typing it once gives the
device a signed session cookie for ``SESSION_DAYS``. Changing the code signs everyone
out. Deliberately simple — no accounts or passwords to manage for non-technical users.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import unicodedata
from collections import defaultdict, deque

from app.config import Settings

COOKIE = "pp_sessao"
PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/logout"}


def normalise_code(code: str) -> str:
    """Accept the code regardless of case, accents or spaces (easier on a phone)."""
    text = unicodedata.normalize("NFKD", code or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(text.lower().split())


def _secret(settings: Settings) -> bytes:
    code = settings.access_code.get_secret_value() if settings.access_code else ""
    return hashlib.sha256(f"prova-pronta|{normalise_code(code)}".encode()).digest()


def code_matches(settings: Settings, attempt: str) -> bool:
    expected = settings.access_code.get_secret_value() if settings.access_code else ""
    return hmac.compare_digest(normalise_code(attempt).encode(), normalise_code(expected).encode())


def make_token(settings: Settings) -> str:
    expires = int(time.time()) + settings.session_days * 86400
    message = f"{expires}.{secrets.token_hex(8)}"
    signature = hmac.new(_secret(settings), message.encode(), hashlib.sha256).hexdigest()
    return f"{message}.{signature}"


def token_valid(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    try:
        expires, nonce, signature = token.split(".")
        expected = hmac.new(_secret(settings), f"{expires}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expires) > time.time()
    except ValueError:
        return False


class RateLimiter:
    """In-memory sliding window, enough for a single-instance school deployment."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, window_s: float) -> deque[float]:
        hits = self._hits[key]
        now = time.time()
        while hits and now - hits[0] > window_s:
            hits.popleft()
        return hits

    def blocked(self, key: str, limit: int, window_s: float) -> bool:
        with self._lock:
            return len(self._recent(key, window_s)) >= limit

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.time())

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        """Record an attempt and say whether it is within the limit."""
        with self._lock:
            hits = self._recent(key, window_s)
            if len(hits) >= limit:
                return False
            hits.append(time.time())
            return True
