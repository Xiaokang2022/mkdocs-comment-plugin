"""Seed demo comments so the widget can be previewed with realistic data.

Usage::

    python scripts/seed_demo.py [count] [base_url] [page]

The bodies deliberately cover the Markdown features the renderer supports
(headings, lists, quotes, tables, fenced code with quotes, bare URLs and
emoji) so a visual check exercises the whole pipeline.

Comments go through the API. Reactions are written straight to SQLite when the
database is local, and the reason is worth stating: a reader's identity *is*
their address, so every reaction this script sends would come from one visitor
and the POSTs would toggle each other off. Seeding several named reactors means
seeding several addresses, which only the database can do.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import Client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 25
BASE = (sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000").rstrip("/")
PAGE = sys.argv[3] if len(sys.argv) > 3 else "/"

client = Client(BASE)

# Deliberately mixed CJK / Latin / digits / length so the list renders every
# kind of name a real site collects. None of them look like an address: the
# address is printed beside the name, and two IPs side by side read as a bug.
AUTHORS = [
    "小明", "Alice", "山田太郎", "QA 小周", "DevOps 老王",
    "张伟", "Carol", "Reader-42", "产品经理", "Bob",
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


EMOJIS = ["👍", "❤️", "🎉", "🚀"]


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


def local_database() -> Path | None:
    """The SQLite file this backend uses, when it is reachable from here."""
    if not ("127.0.0.1" in BASE or "localhost" in BASE):
        return None
    configured = os.getenv("MKC_DB_PATH") or "comments.db"
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = ROOT / "backend" / candidate
    return candidate if candidate.is_file() else None


class ReactionSeeder:
    """Writes reactions with distinct addresses, so tooltips list several names.

    One address is one visitor, so the API can only ever produce a single
    reaction per emoji from this machine. Demo data wants two or three names on
    a pill, which means two or three addresses, which means writing the rows
    directly. A synthetic id is fine here: the column is opaque and only ever
    compared for equality.
    """

    def __init__(self) -> None:
        self.path = local_database()
        self.rows: list[tuple] = []
        if self.path is None:
            print(
                "  [注意] 后端不在本机或数据库不可达，表情将通过 API 写入；\n"
                "        同一地址只能算一个访客，每条表情只会留下一个名字。"
            )

    def add(self, target_type: str, target_id: str, emoji: str, who: str, tag: str) -> None:
        if self.path is None:
            post(
                "/reactions",
                {"target_type": target_type, "target_id": target_id,
                 "emoji": emoji, "author": who},
            )
            return
        index = AUTHORS.index(who)
        self.rows.append(
            (
                target_type,
                target_id,
                emoji,
                f"ip-seed-{tag}-{index:02d}",
                who,
                f"192.0.2.{index + 10}",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        )

    def flush(self) -> None:
        if self.path is None or not self.rows:
            return
        conn = sqlite3.connect(self.path)
        try:
            conn.executemany(
                """
                INSERT OR IGNORE INTO reactions (
                    target_type, target_id, emoji, visitor_id, author, client_ip, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self.rows,
            )
            conn.commit()
        finally:
            conn.close()
        print(f"  ... 直接写入 {len(self.rows)} 条表情（含各自独立的访客身份）")


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
    # come from `AUTHORS`, which mixes CJK and Latin so the tooltip's single
    # line is exercised by both a wide and a narrow script.
    emojis = EMOJIS
    seeder = ReactionSeeder()

    for offset, comment_id in enumerate(root_ids[:4]):
        pair = (AUTHORS[offset % len(AUTHORS)], AUTHORS[(offset + 5) % len(AUTHORS)])
        for emoji in emojis[: (offset % 3) + 1]:
            for who in pair:
                seeder.add("comment", comment_id, emoji, who, f"c{offset}")

    for index, emoji in enumerate(emojis):
        pair = (AUTHORS[index % len(AUTHORS)], AUTHORS[(index + 7) % len(AUTHORS)])
        for who in pair:
            seeder.add("page", PAGE, emoji, who, "page")

    seeder.flush()

    print(f"==> 完成：{len(root_ids)} 条根评论，已附加带头像的表情")


if __name__ == "__main__":
    main()
