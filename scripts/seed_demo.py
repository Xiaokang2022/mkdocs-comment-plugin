"""Seed demo comments so the widget can be previewed with realistic data.

Usage::

    python scripts/seed_demo.py [count] [base_url] [page]

The bodies deliberately cover the Markdown features the renderer supports
(headings, lists, quotes, tables, fenced code with quotes, bare URLs and
emoji) so a visual check exercises the whole pipeline.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import Client  # noqa: E402

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 25
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000").rstrip("/")
PAGE = sys.argv[3] if len(sys.argv) > 3 else "/"

client = Client(BASE)

AUTHORS = [
    "小明", "Alice", "山田太郎", "10.0.0.7", "DevOps 老王",
    "张伟", "Carol", "192.168.1.42", "产品经理", "Bob",
]

BODIES = [
    "**非常棒的插件！** 集成只花了两分钟。",
    "> 引用一下楼上\n\n同意，Material 风格很贴合主题。",
    '试了一下代码块，引号和尖括号都没问题：\n\n```python\nprint("hello")\nif a < b:\n    pass\n```',
    "1. 部署简单\n2. 后端无外部依赖\n3. 支持表情互动",
    "点赞功能很好用 👍 页面级和评论级分开统计很贴心。",
    "楼中楼回复的 `@提及` 处理得不错。",
    "| 项目 | 状态 |\n| --- | --- |\n| 前端 | ✅ |\n| 后端 | ✅ |",
    "请问如何配置 CORS？我参考了 https://fastapi.tiangolo.com 但还是不确定。",
    "深色模式自动适配，好评 ✨",
    "希望能增加邮件通知功能。",
]


def post(path: str, body, retries: int = 8) -> dict:
    """POST with backoff, so the seed script respects the server rate limit."""
    for attempt in range(retries):
        status, data = client.call("POST", path, body)
        if status == 429:
            wait = 5 * (attempt + 1)
            print(f"  ... 触发限流，等待 {wait}s 重试")
            time.sleep(wait)
            continue
        if status >= 400:
            raise RuntimeError(f"POST {path} -> HTTP {status}: {data}")
        return data
    raise RuntimeError(f"POST {path}: 重试 {retries} 次后仍被限流")


def existing_roots() -> list[str]:
    """Root ids already on the page, newest first."""
    listing = client.fetch_all(PAGE)
    if not listing:
        return []
    return [c["id"] for c in listing["comments"] if c["thread_id"] == c["id"]]


def main() -> None:
    root_ids: list[str] = []
    if COUNT:
        print(f"==> 向 {PAGE} 写入 {COUNT} 条演示评论 ({client.api})")
    else:
        # Reactions-only run: decorate whatever is already on the page. Handy
        # for refreshing demo state without piling up duplicate comments.
        root_ids = existing_roots()
        print(f"==> 跳过写评论，为已有 {len(root_ids)} 条根评论附加表情 ({client.api})")

    for index in range(COUNT):
        author = AUTHORS[index % len(AUTHORS)]
        body = BODIES[index % len(BODIES)]
        parent = None
        # Make every third comment a reply to an existing root.
        if index % 3 == 2 and root_ids:
            parent = root_ids[(index // 3) % len(root_ids)]

        created = post(
            "/comments",
            {"page": PAGE, "author": author, "content": body, "parent_id": parent},
        )
        comment_id = created["comment"]["id"]
        if parent is None:
            root_ids.append(comment_id)
        print(f"  [{index + 1:>3}/{COUNT}] {author:<12} {'回复' if parent else '评论'} {comment_id[:8]}")

    # Sprinkle reactions across the page and the first few comments. Each one
    # carries a nickname: that is what the hover tooltip lists, so every pill
    # ends up with two named reactors instead of an anonymous count. The names
    # deliberately mix CJK, Latin and IP-shaped values to exercise truncation.
    #
    # `visitor_id` must be ASCII: the API rejects a non-ASCII value and falls
    # back to an IP-derived id, which would collapse every reactor below into a
    # single visitor and make the POSTs cancel each other out.
    emojis = ["👍", "❤️", "🎉", "🚀"]

    def react(target_type: str, target_id: str, emoji: str, who: str, tag: str) -> None:
        index = AUTHORS.index(who)
        post(
            "/reactions",
            {
                "target_type": target_type,
                "target_id": target_id,
                "emoji": emoji,
                "visitor_id": f"seed-{tag}-{emojis.index(emoji)}-r{index}",
                "author": who,
            },
        )

    for offset, comment_id in enumerate(root_ids[:4]):
        pair = (AUTHORS[offset % len(AUTHORS)], AUTHORS[(offset + 5) % len(AUTHORS)])
        for emoji in emojis[: (offset % 3) + 1]:
            for who in pair:
                react("comment", comment_id, emoji, who, f"c{offset}")

    for index, emoji in enumerate(emojis):
        pair = (AUTHORS[index % len(AUTHORS)], AUTHORS[(index + 7) % len(AUTHORS)])
        for who in pair:
            react("page", PAGE, emoji, who, "page")

    print(f"==> 完成：{len(root_ids)} 条根评论，已附加带头像的表情")


if __name__ == "__main__":
    main()
