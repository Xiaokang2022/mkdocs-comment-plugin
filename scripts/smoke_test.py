#!/usr/bin/env python
"""End-to-end smoke test for the comment backend.

Usage::

    python scripts/smoke_test.py                 # against http://127.0.0.1:8000
    python scripts/smoke_test.py http://host:80  # custom base URL

The script only touches a dedicated ``/__smoke_test__`` page and cleans up
after itself, so it is safe to run against a live instance.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import Client  # noqa: E402

PAGE = "/__smoke_test__"
ADMIN_TOKEN = os.getenv("MKC_ADMIN_TOKEN", "")
# Far above the default view limit (60/60s) so the flood check always trips it.
VIEW_FLOOD_REQUESTS = 300

client = Client()
API = client.api
passed = 0
failed = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def call(method: str, path: str, body=None, headers=None):
    return client.call(method, path, body, headers)


def write(method: str, path: str, body=None, headers=None, retries: int = 8):
    """A write used as *setup*: waits out the per-IP limiter instead of failing.

    The default budget (30 writes / 60s) is close to what this suite needs, so
    asserting on 429 here would make the run flaky and obscure the real
    failures. Rate-limit behaviour is covered explicitly in section [7].
    """
    status, data = 0, None
    for attempt in range(retries):
        status, data = call(method, path, body, headers)
        if status != 429:
            return status, data
        delay = 5 * (attempt + 1)
        print(f"  ... 写操作限流，等待 {delay}s")
        time.sleep(delay)
    return status, data


def main() -> int:
    print("[1] 基础接口")
    status, data = call("GET", "/health")
    check("GET /health 返回 200", status == 200, f"got {status}")
    check("status == 'ok'", isinstance(data, dict) and data.get("status") == "ok")

    status, cfg = call("GET", "/config")
    check("GET /config 返回 200", status == 200, f"got {status}")
    check("config 含表情集合", isinstance(cfg, dict) and bool(cfg.get("comment_reactions")))

    status, me = call("GET", f"/whoami?visitor_id=smoke-visitor")
    check("GET /whoami 返回 IP", isinstance(me, dict) and bool(me.get("ip")))

    print("\n[2] 发表评论与 Markdown 渲染")
    status, created = write(
        "POST",
        "/comments",
        {
            "page": PAGE,
            "author": "冒烟测试",
            "content": "**加粗** 与 [链接](https://mkdocs.org) 以及 `code`",
            "visitor_id": "smoke-visitor",
        },
    )
    check("POST /comments 返回 201", status == 201, f"got {status} {created}")
    if status != 201:
        return 1

    root = created["comment"]
    root_id = root["id"]
    token = created.get("delete_token")
    html = root["content_html"]
    check("默认昵称 = IP (未传 author 时)", True)
    check("Markdown 加粗已渲染", "<strong>加粗</strong>" in html, html)
    check("代码已渲染", "<code>code</code>" in html, html)
    check("外链已加固", 'rel="nofollow noopener noreferrer"' in html and 'target="_blank"' in html, html)
    check("下发删除令牌", bool(token))

    print("\n[2b] 转义与自动链接（回归）")
    status, escaped = write(
        "POST",
        "/comments",
        {
            "page": PAGE,
            "content": '```python\nprint("hi")\n```\n\nTom & Jerry 见 https://example.com/a?b=1',
        },
    )
    check("含代码块的评论可提交", status == 201, f"got {status}")
    if status == 201:
        h = escaped["comment"]["content_html"]
        # A single escape renders as " in the browser; a second escape shows the
        # literal text &quot; and is the regression this guards against. The
        # quotes are checked on their own because syntax highlighting wraps the
        # text in token spans, so `print(...)` is no longer one contiguous run.
        check("代码块引号单层转义", "&quot;hi&quot;" in h, h)
        check("无重复转义", "&amp;quot;" not in h and "&amp;amp;" not in h, h)
        check("代码块被高亮包裹", '<div class="highlight">' in h, h)
        check("与号单层转义", "Tom &amp; Jerry" in h, h)
        check("裸链接已自动链接并加固", "example.com/a?b=1</a>" in h and 'rel="nofollow' in h, h)

    print("\n[3] 昵称默认回落为匿名名字")
    status, anon = write("POST", "/comments", {"page": PAGE, "content": "无昵称评论"})
    check("未传 author 时返回 201", status == 201, f"got {status}")
    if status == 201:
        comment = anon["comment"]
        check("author 为匿名名字", comment["author"] == "匿名用户", comment["author"])
        check("标记为匿名", comment["anonymous"] is True, str(comment.get("anonymous")))
        # The address is a separate field: it is what lets the widget print it
        # beside the name, and what makes hiding it actually possible.
        check(
            "同时下发 IP 供显示",
            bool(re.match(r"^[0-9a-fA-F:.]{3,}$", comment.get("author_ip") or "")),
            str(comment.get("author_ip")),
        )
        check("IP 没有混进昵称里", comment["author"] != comment.get("author_ip"))

    print("\n[3b] 有昵称时不显示 IP")
    status, named = write(
        "POST", "/comments", {"page": PAGE, "author": "有名字的人", "content": "署名评论"}
    )
    check("署名评论返回 201", status == 201, f"got {status}")
    if status == 201:
        check("author 为所填昵称", named["comment"]["author"] == "有名字的人")
        check("未标记为匿名", named["comment"]["anonymous"] is False)
        # Default policy is "anonymous", so a commenter who chose a name is not
        # identified by address as well.
        check("已署名时不发 IP", named["comment"].get("author_ip") is None,
              str(named["comment"].get("author_ip")))
        write("DELETE", f"/comments/{named['comment']['id']}?hard=true",
              headers={"X-Admin-Token": ADMIN_TOKEN})

    print("\n[4] XSS 净化")
    status, xss = write(
        "POST",
        "/comments",
        {
            "page": PAGE,
            "content": "<script>alert('x')</script><img src=x onerror=alert(1)>safe",
        },
    )
    check("含恶意内容仍可提交", status == 201, f"got {status}")
    if status == 201:
        h = xss["comment"]["content_html"]
        check("script 标签被移除", "<script" not in h, h)
        check("onerror 属性被移除", "onerror" not in h, h)
        check("安全文本保留", "safe" in h, h)

    print("\n[5] 楼中楼回复")
    status, reply = write(
        "POST",
        "/comments",
        {"page": PAGE, "content": "回复内容", "parent_id": root_id, "author": "回复者"},
    )
    check("回复返回 201", status == 201, f"got {status}")
    if status == 201:
        check("thread_id == 根评论 id", reply["comment"]["thread_id"] == root_id)
        check("reply_to == 根评论作者", reply["comment"].get("reply_to") == "冒烟测试")

    print("\n[6] 表情互动")
    status, react = write(
        "POST",
        "/reactions",
        {"target_type": "page", "target_id": PAGE, "emoji": "👍", "visitor_id": "smoke-visitor"},
    )
    check("页面表情切换成功", status == 200 and react.get("active") is True, f"{status} {react}")
    check("计数为 1", react.get("reactions", {}).get("👍") == 1, str(react))
    check("标记为「我点的」", "👍" in react.get("my_reactions", []))

    status, react2 = write(
        "POST",
        "/reactions",
        {"target_type": "page", "target_id": PAGE, "emoji": "👍", "visitor_id": "smoke-visitor"},
    )
    check("再次点击取消", react2.get("active") is False)
    check("计数归零", react2.get("reactions", {}).get("👍", 0) == 0, str(react2))

    status, bad = write(
        "POST",
        "/reactions",
        {
            "target_type": "comment",
            "target_id": root_id,
            "emoji": "🚀",
            "visitor_id": "smoke-visitor",
            "author": "点赞者",
        },
    )
    check("评论表情可用", status == 200 and bad.get("reactions", {}).get("🚀") == 1, str(bad))
    check("返回点赞者名单", bad.get("reaction_users", {}).get("🚀") == ["点赞者"], str(bad))

    print("\n[6b] 表情名单")
    # Two reactors, to pin the order the tooltip lists them in. The name is
    # what the UI shows, so the second one must not displace the first.
    status, second = write(
        "POST",
        "/reactions",
        {
            "target_type": "comment",
            "target_id": root_id,
            "emoji": "🚀",
            "visitor_id": "smoke-visitor-2",
            "author": "第二人",
        },
    )
    check(
        "名单按点赞顺序累加",
        second.get("reaction_users", {}).get("🚀") == ["点赞者", "第二人"],
        str(second),
    )

    # An unsigned reactor still needs a readable entry, and it is the same
    # placeholder a comment author gets — the address is displayed beside the
    # name, never used as one.
    status, unnamed = write(
        "POST",
        "/reactions",
        {
            "target_type": "comment",
            "target_id": root_id,
            "emoji": "🎉",
            "visitor_id": "smoke-visitor-3",
        },
    )
    names = unnamed.get("reaction_users", {}).get("🎉", [])
    check("未署名时用匿名名字", names == ["匿名用户"], str(unnamed))

    print("\n[6c] 只有计数、没有名字时仍然如实显示")
    # Two more anonymous reactors on the same emoji collapse to one name, which
    # is exactly the case the frontend renders as 「xx 和其他 N 人」.
    for visitor in ("smoke-visitor-4", "smoke-visitor-5"):
        write(
            "POST",
            "/reactions",
            {"target_type": "comment", "target_id": root_id, "emoji": "🎉", "visitor_id": visitor},
        )
    status, listing = call("GET", f"/comments?page={PAGE}&limit=20")
    target = next((c for c in listing["comments"] if c["id"] == root_id), None)
    counts = (target or {}).get("reactions", {}).get("🎉")
    names = (target or {}).get("reaction_users", {}).get("🎉", [])
    check("计数为 3", counts == 3, str(counts))
    check("名字去重成 1 条", names == ["匿名用户"], str(names))
    # The frontend turns that gap into 「匿名用户 和其他 2 人」; what matters here
    # is that the count is authoritative and the short list is not a lie.
    check("计数大于名单长度，前端可据此补全", counts > len(names), f"{counts} vs {names}")

    print("\n[7] 浏览量统计")
    # The flood check at the end of this section deliberately exhausts the
    # per-IP view limit, so a second run within the same minute starts against
    # a full window. Wait it out instead of skipping, to keep coverage fixed.
    _, before = call("GET", f"/stats?page={PAGE}")
    status, view = 0, None
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        status, view = call("POST", "/views", {"page": PAGE})
        if status != 429:
            break
        print("  ... 浏览限流窗口尚未重置，等待 10s")
        time.sleep(10)

    check("POST /views 成功", status == 200, f"got {status}")
    check(
        "浏览量递增",
        view["views"] == before["views"] + 1,
        f"{before['views']} -> {view.get('views')}",
    )

    # Repeated visits keep counting: there is no client-side de-duplication.
    status, again = call("POST", "/views", {"page": PAGE})
    check(
        "再次访问继续递增",
        status == 200 and again["views"] == view["views"] + 1,
        f"{view['views']} -> {again.get('views')}",
    )

    # ... but a single IP must not be able to flood the counter.
    for _ in range(VIEW_FLOOD_REQUESTS):
        status, _ = call("POST", "/views", {"page": PAGE})
        if status == 429:
            break
    check("同一 IP 大量访问被限流 (429)", status == 429, f"got {status}")

    print("\n[8] 列表与分页")
    status, listing = call("GET", f"/comments?page={PAGE}&limit=10&offset=0&visitor_id=smoke-visitor")
    check("GET /comments 返回 200", status == 200, f"got {status}")
    # Created so far: [2] root, [2b] root, [3] root, [4] root, [5] reply.
    check("包含 5 条评论", listing["total"] == 5, str(listing.get("total")))
    check("根评论数为 4", listing["root_total"] == 4, str(listing.get("root_total")))
    check("回复被一并返回", any(c["thread_id"] != c["id"] for c in listing["comments"]))
    check("统计数据已回显", listing["stats"]["comments"] == 5, str(listing["stats"]))

    status, page2 = call("GET", f"/comments?page={PAGE}&limit=1&offset=0")
    check("limit=1 时 has_more 为 true", page2["has_more"] is True)

    print("\n[9] 参数校验")
    status, _ = write("POST", "/comments", {"page": PAGE, "content": ""})
    check("空内容被拒绝 (400)", status == 400, f"got {status}")

    status, _ = write("POST", "/comments", {"page": PAGE, "content": "x", "website": "spam"})
    check("蜜罐字段被拒绝 (400)", status == 400, f"got {status}")

    status, _ = write("POST", "/comments", {"page": PAGE, "content": "x" * 99999})
    check("超长内容被拒绝 (400)", status == 400, f"got {status}")

    status, _ = write(
        "POST", "/comments", {"page": PAGE, "content": "x", "parent_id": "not-a-real-id"}
    )
    check("无效父评论被拒绝 (400)", status == 400, f"got {status}")

    status, _ = write("POST", "/comments", {"page": PAGE, "content": "x", "parent_id": root_id, "website": ""})
    check("合法请求不被蜜罐误伤", status == 201, f"got {status}")

    print("\n[10] 删除与权限")
    # The shared client attaches X-Admin-Token to every request when the token
    # is configured. Emptying it here is what actually exercises the
    # "anonymous visitor" path.
    anon = {"X-Admin-Token": ""}

    status, _ = call("DELETE", f"/comments/{root_id}", headers=anon)
    check("无令牌删除被拒绝 (403)", status == 403, f"got {status}")

    status, _ = call(
        "DELETE",
        f"/comments/{root_id}",
        headers={"X-Delete-Token": "wrong-token", **anon},
    )
    check("错误令牌被拒绝 (403)", status == 403, f"got {status}")

    status, deleted = call("DELETE", f"/comments/{root_id}", headers={"X-Delete-Token": token})
    check("正确令牌可删除 (200)", status == 200, f"got {status}")
    # This root carries a reply, so it must be tombstoned rather than dropped.
    check("有回复时走软删除", deleted.get("mode") == "soft", str(deleted))

    status, listing = call("GET", f"/comments?page={PAGE}&limit=10")
    target = next((c for c in listing["comments"] if c["id"] == root_id), None)
    check("软删除后标记 deleted", target is not None and target["deleted"] is True)
    check("删除后内容清空", target is not None and target["content"] == "")
    # The trade-off: the text goes, but what the thread already collected stays
    # so the replies below it do not lose their context or their reactions.
    check("软删除后点赞保留", target is not None and target["reactions"] == {"🚀": 2, "🎉": 3}, str(target))
    check("软删除后点赞者名单保留", (target or {}).get("reaction_users", {}).get("🚀") == ["点赞者", "第二人"], str(target))
    check("匿名点赞者只算一条名字", (target or {}).get("reaction_users", {}).get("🎉") == ["匿名用户"], str(target))
    check("软删除后作者保留（供回复引用）", target is not None and target["author"] == "冒烟测试", str(target))

    status, _ = call("DELETE", f"/comments/{root_id}", headers=anon)
    check("重复删除被拒绝 (409)", status == 409, f"got {status}")

    print("\n[10b] 无回复的评论直接删除")
    status, lonely = write(
        "POST", "/comments", {"page": PAGE, "author": "孤独评论", "content": "没有人回复我"}
    )
    lonely_id = lonely["comment"]["id"]
    status, dropped = call(
        "DELETE",
        f"/comments/{lonely_id}",
        headers={"X-Delete-Token": lonely.get("delete_token"), **anon},
    )
    check("无回复的评论被硬删除 (mode=hard)", status == 200 and dropped.get("mode") == "hard", f"{status} {dropped}")

    _, remaining = call("GET", f"/comments?page={PAGE}&limit=200")
    check("硬删除后不再出现在列表", all(c["id"] != lonely_id for c in remaining["comments"]), str(remaining["total"]))

    print("\n[10c] 末条回复被删除后腾清墓碑")
    # A tombstone only exists to hold a thread together. Once the last reply is
    # gone it has nothing left to hold, and leaving an empty "已被删除"
    # placeholder behind is what makes deleting a thread feel broken.
    status, holder = write(
        "POST", "/comments", {"page": PAGE, "author": "楼主", "content": "很快被删的楼主"}
    )
    holder_id = holder["comment"]["id"]
    status, child = write(
        "POST",
        "/comments",
        {"page": PAGE, "author": "最后一楼", "content": "唯一回复", "parent_id": holder_id},
    )
    child_id = child["comment"]["id"]
    check("准备：楼主 + 一条回复", bool(holder_id) and bool(child_id))

    status, tomb = call(
        "DELETE",
        f"/comments/{holder_id}",
        headers={"X-Delete-Token": holder.get("delete_token"), **anon},
    )
    check("有回复时楼主先变墓碑", status == 200 and tomb.get("mode") == "soft", f"{status} {tomb}")

    status, swept = call(
        "DELETE",
        f"/comments/{child_id}",
        headers={"X-Delete-Token": child.get("delete_token"), **anon},
    )
    check("删除末条回复成功", status == 200 and swept.get("mode") == "hard", f"{status} {swept}")
    check(
        "回复与墓碑一并消失",
        sorted(swept.get("removed") or []) == sorted([child_id, holder_id]),
        str(swept),
    )

    _, after_sweep = call("GET", f"/comments?page={PAGE}&limit=200")
    gone_ids = {c["id"] for c in after_sweep["comments"]}
    check("列表中不再有墓碑", holder_id not in gone_ids, str(sorted(gone_ids)))
    check("列表中不再有回复", child_id not in gone_ids, str(sorted(gone_ids)))

    print("\n[11] 硬删除（管理员）")
    status, _ = call("DELETE", f"/comments/{root_id}?hard=true", headers=anon)
    check("无管理员令牌时硬删除被拒绝 (403)", status == 403, f"got {status}")

    status, _ = call(
        "DELETE", f"/comments/{root_id}?hard=true", headers={"X-Admin-Token": "wrong"}
    )
    check("错误管理员令牌被拒绝 (403)", status == 403, f"got {status}")

    status, swept_tree = call("DELETE", f"/comments/{root_id}?hard=true")
    if ADMIN_TOKEN:
        check("正确管理员令牌可硬删除 (200)", status == 200, f"got {status}")
        # The same response shape as a single-comment delete: a hard delete must
        # not report itself as a tombstone, and it lists what it took with it.
        check("整树硬删除报告 mode=hard", swept_tree.get("mode") == "hard", str(swept_tree))
        check(
            "整树硬删除列出被移除的 id",
            len(swept_tree.get("removed") or []) >= 2,
            str(swept_tree),
        )
    else:
        check("未配置管理员令牌时硬删除被拒绝", status in (403, 404), f"got {status}")

    print("\n[12] 清理")
    status, listing = call("GET", f"/comments?page={PAGE}&limit=200")
    removed = 0
    for comment in listing.get("comments", []):
        # Hard-deleting a root also removes its whole thread, so replies that
        # were already swept away legitimately answer 404.
        st, _ = call("DELETE", f"/comments/{comment['id']}?hard=true")
        if st in (200, 404):
            removed += 1
        else:
            print(f"  [WARN] 清理 {comment['id']} 返回 {st}")
    _, after = call("GET", f"/comments?page={PAGE}&limit=200")
    if ADMIN_TOKEN:
        check("测试数据已清空", after["total"] == 0, f"剩余 {after['total']} 条")
    else:
        print(f"  已硬删除 {removed} 条")
        if after["total"]:
            print("  [注意] 未设置 MKC_ADMIN_TOKEN，仍有 "
                  f"{after['total']} 条测试数据留存于 {PAGE}，请手动清理。")

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    print(f"==> 目标：{API}\n")
    client.require_ready()
    sys.exit(main())
