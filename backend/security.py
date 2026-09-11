"""Small security helpers: client IP resolution, identity, hashing and rate limiting."""

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

# Marks an id this module derived from an address rather than one a client
# supplied. Purely diagnostic now that the derivation is mandatory — it is what
# makes a value in the database readable at a glance — but it also lets a test
# state the rule as "the id depends on the address and nothing else".
IP_VISITOR_PREFIX = "ip-"


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


def make_visitor_id(ip: str) -> str:
    """The identity of a reader, derived from their address and nothing else.

    One address is one visitor. That is the whole point: the same person on a
    phone, a laptop and a private window is one visitor, and a reader who clears
    their storage keeps the reactions they already left. The mirror image is
    that everyone behind one address — an office NAT, a campus network, a VPN —
    is also one visitor, so they share their reaction slots and their view of
    "which ones are mine".

    Deliberately not salted with the user agent, and deliberately not combined
    with a client-supplied id: either one would let a single address present
    several identities, which is the behaviour this replaced.
    """
    digest = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    return f"{IP_VISITOR_PREFIX}{digest[:24]}"


def owns_row(row: Dict[str, str], visitor: str) -> bool:
    """Whether a stored row was written by this visitor.

    Ownership is an address comparison and nothing else — the same rule that
    decides whose reaction counts as "mine". The nickname is deliberately not
    consulted: names are self-declared, nothing stops two readers from choosing
    the same one, and a name that granted deletion would hand the right to
    anyone who read it off the page.

    Lives here rather than in the request layer so the rule can be tested
    without an HTTP request, which is also how "the name is irrelevant" gets
    pinned down.
    """
    written_from = (row or {}).get("client_ip") or ""
    if not written_from or not visitor:
        return False
    return visitor == make_visitor_id(written_from)


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
