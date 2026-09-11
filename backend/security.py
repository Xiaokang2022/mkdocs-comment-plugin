"""Small security helpers: client IP resolution, hashing and rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from settings import Settings


def client_ip(request: Request, settings: Settings) -> str:
    """Resolve the best-effort client IP address.

    ``X-Forwarded-For`` is only honoured when ``MKC_TRUST_PROXY_HEADERS=1``
    is set, otherwise a client could spoof its own identity.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get(settings.proxy_header)
        if forwarded:
            # The first entry is the original client.
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "0.0.0.0"


def make_visitor_id(ip: str, user_agent: str) -> str:
    """Deterministic fallback identity when the client sends no visitor id."""
    digest = hashlib.sha256(f"{ip}|{user_agent}".encode("utf-8")).hexdigest()
    return f"ip-{digest[:24]}"


def new_delete_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


class RateLimiter:
    """Fixed-window in-memory rate limiter (per IP).

    In memory, and therefore **per process**: two uvicorn workers each keep
    their own counters, so the effective limit is multiplied by the number of
    workers. That is why the Dockerfile runs a single process — see the
    production notes in the README before adding ``--workers``.

    Buckets are keyed by client, and a public site sees many clients, so they
    are swept periodically: an empty bucket is one nobody has written to within
    the current window, and dropping it costs nothing because the next request
    recreates it. Without the sweep the dict would keep one entry per address
    ever seen.
    """

    # A sweep is O(keys); amortising it over this many checks keeps it cheap
    # while still bounding growth on a busy site.
    SWEEP_INTERVAL = 4096

    def __init__(self, limit: int, window: int) -> None:
        self.limit = max(limit, 1)
        self.window = max(window, 1)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._since_sweep = 0

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = int(self.window - (now - bucket[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="操作过于频繁，请稍后再试。",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            self._since_sweep += 1
            if self._since_sweep >= self.SWEEP_INTERVAL:
                self._sweep()

    def _sweep(self) -> None:
        """Drop buckets with no hits left in the window. Caller holds the lock."""
        self._since_sweep = 0
        for key in [k for k, bucket in self._hits.items() if not bucket]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._since_sweep = 0


def require_admin(token: str | None, settings: Settings) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="服务端未配置管理员令牌。",
        )
    if not token or not hmac.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员令牌无效。")
