"""Tests for the security helpers.

Three things a deployment depends on, none of which are visible from the docs:

* ``client_ip`` must ignore ``X-Forwarded-For`` unless the operator opted in —
  otherwise any visitor can choose their own identity, and with it their rate
  limit and their ``ip-…`` visitor id;
* the limiter must stay bounded in memory, because it keeps one bucket per
  client address and it runs for months;
* delete tokens are compared by hash, and an empty token or an empty stored hash
  must never authorise anything.

Run directly (``python scripts/test_security.py``) or with pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi import HTTPException  # noqa: E402

from security import (  # noqa: E402
    RateLimiter,
    client_ip,
    hash_token,
    make_visitor_id,
    verify_token,
)
from settings import Settings  # noqa: E402

passed = 0
failed = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


class FakeRequest:
    """Just enough of a Starlette request for `client_ip`."""

    def __init__(self, headers: dict | None = None, peer: str | None = "10.0.0.9") -> None:
        self.headers = headers or {}
        self.client = type("Peer", (), {"host": peer})() if peer else None


def test_forwarded_headers_need_opt_in() -> None:
    print("--- X-Forwarded-For 默认不可信")
    spoofed = {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}

    off = Settings(trust_proxy_headers=False)
    check("未开启时忽略 XFF", client_ip(FakeRequest(spoofed), off) == "10.0.0.9",
          client_ip(FakeRequest(spoofed), off))
    check("未开启时忽略 X-Real-IP", client_ip(FakeRequest({"X-Real-IP": "5.6.7.8"}), off) == "10.0.0.9")

    on = Settings(trust_proxy_headers=True)
    check("开启时取 XFF 第一段", client_ip(FakeRequest(spoofed), on) == "1.2.3.4",
          client_ip(FakeRequest(spoofed), on))
    check(
        "多级代理取最左侧的原始客户端",
        client_ip(FakeRequest({"X-Forwarded-For": "1.2.3.4, 10.1.1.1, 10.2.2.2"}), on) == "1.2.3.4",
    )
    check("XFF 缺失时回落 X-Real-IP", client_ip(FakeRequest({"X-Real-IP": "5.6.7.8"}), on) == "5.6.7.8")
    check("两者都缺失时用 socket 对端", client_ip(FakeRequest({}), on) == "10.0.0.9")

    custom = Settings(trust_proxy_headers=True, proxy_header="CF-Connecting-IP")
    check("代理头名称可配置",
          client_ip(FakeRequest({"CF-Connecting-IP": "9.9.9.9"}), custom) == "9.9.9.9")
    check("没有对端信息时兜底", client_ip(FakeRequest({}, peer=None), on) == "0.0.0.0")


def test_visitor_id_is_stable_and_shared() -> None:
    print("--- 访客 ID 的推导")
    first = make_visitor_id("10.0.0.9", "Mozilla/5.0")
    check("同一 IP + UA 得到同一 ID", make_visitor_id("10.0.0.9", "Mozilla/5.0") == first)
    check("UA 变化后 ID 变化", make_visitor_id("10.0.0.9", "curl/8") != first)
    check("IP 变化后 ID 变化", make_visitor_id("10.0.0.10", "Mozilla/5.0") != first)
    # The prefix is load-bearing: `Database._remember_visitor` refuses to store a
    # nickname against such an id, precisely because it is not per-browser.
    check("带 ip- 前缀，便于识别共享身份", first.startswith("ip-"), first)
    check("长度固定", len(first) == 3 + 24, str(len(first)))


def test_delete_tokens() -> None:
    print("--- 删除令牌比较")
    token = "s3cret-token"
    digest = hash_token(token)
    check("正确令牌通过", verify_token(token, digest) is True)
    check("错误令牌被拒", verify_token("other", digest) is False)
    check("空令牌被拒", verify_token("", digest) is False)
    check("空哈希被拒", verify_token(token, "") is False)
    check("两者都空被拒", verify_token("", "") is False)
    check("存储的是哈希而非明文", token not in digest and len(digest) == 64)
    check("同一令牌哈希稳定", hash_token(token) == digest)


def test_limiter_counts_and_recovers() -> None:
    print("--- 限流计数与恢复")
    limiter = RateLimiter(2, 60)
    limiter.check("ip-a")
    limiter.check("ip-a")
    try:
        limiter.check("ip-a")
        check("超过上限抛 429", False)
    except HTTPException as exc:
        check("超过上限抛 429", exc.status_code == 429)
        check("带 Retry-After", int(exc.headers.get("Retry-After", 0)) >= 1,
              str(exc.headers))
    check("其它来源不受影响", limiter.check("ip-b") is None)

    short = RateLimiter(1, 1)
    short.check("ip-a")
    try:
        short.check("ip-a")
        check("窗口内第二次仍被拒", False)
    except HTTPException:
        check("窗口内第二次仍被拒", True)
    # Real time, because the window is a wall clock: 1s is the smallest useful one.
    import time

    time.sleep(1.05)
    try:
        short.check("ip-a")
        check("窗口过后恢复", True)
    except HTTPException:
        check("窗口过后恢复", False)


def test_limiter_memory_is_bounded() -> None:
    print("--- 限流器内存有界")
    limiter = RateLimiter(10_000, 60)
    limiter.SWEEP_INTERVAL = 10
    for i in range(500):
        limiter.check(f"ip-{i}")
    check("先积累 500 个来源", len(limiter._hits) == 500, str(len(limiter._hits)))

    # Simulate the window elapsing: every bucket ages out with no new hits.
    for bucket in limiter._hits.values():
        bucket.clear()
    for _ in range(15):
        limiter.check("active")
    check("空闲来源被回收，只留活跃的", len(limiter._hits) == 1, str(len(limiter._hits)))
    check("活跃来源仍在计数", "active" in limiter._hits)
    check("计数器已归零", limiter._since_sweep < limiter.SWEEP_INTERVAL)

    limiter.reset()
    check("reset 清空全部", len(limiter._hits) == 0)

    # A bucket is only dropped once it is empty, i.e. once the client is no
    # longer inside any window — an in-flight limit must never be forgotten.
    keeper = RateLimiter(3, 600)
    keeper.SWEEP_INTERVAL = 1
    keeper.check("ip-x")
    keeper.check("ip-y")
    check("仍有命中的来源不会被回收", "ip-x" in keeper._hits and "ip-y" in keeper._hits)


def main() -> int:
    test_forwarded_headers_need_opt_in()
    print()
    test_visitor_id_is_stable_and_shared()
    print()
    test_delete_tokens()
    print()
    test_limiter_counts_and_recovers()
    print()
    test_limiter_memory_is_bounded()

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
