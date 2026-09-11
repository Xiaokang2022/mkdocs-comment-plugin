"""Minimal JSON client shared by the maintenance and test scripts.

Centralising this removes four near-identical copies of the request helper and,
more importantly, gives every script a way to cope with the server's per-IP
rate limit. Scripts share one window with each other and with real users, so a
test run that follows ``seed_demo.py`` would otherwise open with a ``429``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000"
# An intentionally invalid comment returns 400 when the write budget is free
# and 429 when it is exhausted. Nothing is ever persisted by the probe.
QUOTA_PROBE = {"page": "/__quota_probe__", "content": ""}


def resolve_base(argv_index: int = 1) -> str:
    """API host from argv, then ``MKC_BASE_URL``, then the local default.

    Only an argument that actually looks like a URL is accepted. Scripts such
    as ``admin.py`` and ``test_rendering_live.py`` put a subcommand in
    ``sys.argv[1]``, and treating that as a host produced URLs like
    ``purge-body/api/v1/comments``.
    """
    if len(sys.argv) > argv_index:
        candidate = sys.argv[argv_index].rstrip("/")
        if candidate.startswith(("http://", "https://")):
            return candidate
    return os.getenv("MKC_BASE_URL", DEFAULT_BASE).rstrip("/")


class Client:
    """Thin wrapper over ``urllib`` returning ``(status, payload)`` tuples."""

    def __init__(self, base: str | None = None, admin_token: str | None = None) -> None:
        self.base = (base or resolve_base()).rstrip("/")
        self.api = f"{self.base}/api/v1"
        self.admin_token = (
            admin_token if admin_token is not None else os.getenv("MKC_ADMIN_TOKEN", "")
        )

    # ------------------------------------------------------------------ #
    # requests
    # ------------------------------------------------------------------ #
    def call(self, method: str, path: str, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        url = path if path.startswith("http") else self.api + path
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if self.admin_token:
            request.add_header("X-Admin-Token", self.admin_token)
        if data:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw
        except urllib.error.URLError as exc:
            return 0, {"detail": f"无法连接后端：{exc.reason}"}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def require_ready(self, max_wait: int = 75) -> bool:
        """Wait until the backend answers and the write budget is available."""
        status, _ = self.call("GET", "/health")
        if status != 200:
            print(f"  [错误] 后端不可用（{self.base}），请先启动服务。")
            return False

        deadline = time.monotonic() + max_wait
        announced = False
        while time.monotonic() < deadline:
            status, _ = self.call("POST", "/comments", QUOTA_PROBE)
            if status != 429:
                return True
            if not announced:
                print("  ... 检测到写操作限流，等待窗口重置")
                announced = True
            time.sleep(5)
        print("  [警告] 等待限流窗口超时，继续执行。")
        return False

    def fetch_all(self, page: str, page_size: int = 200) -> dict | None:
        """Fetch every comment on ``page``, following pagination."""
        first: dict | None = None
        comments: list[dict] = []
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {"page": page, "limit": page_size, "offset": offset}
            )
            status, chunk = self.call("GET", f"/comments?{query}")
            if status != 200 or chunk is None:
                return None
            if first is None:
                first = chunk
            comments.extend(chunk["comments"])
            if not chunk["has_more"]:
                break
            offset += page_size
        if first is not None:
            first["comments"] = comments
        return first
