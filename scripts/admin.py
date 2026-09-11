"""Administrative maintenance for the comment backend.

Requires ``MKC_ADMIN_TOKEN`` to match the server's configuration.

Usage::

    python scripts/admin.py list [page]                 # 列出某页评论
    python scripts/admin.py purge <page>                # 清空某页全部评论
    python scripts/admin.py purge-body <text> <page>... # 按内容片段清理
    python scripts/admin.py purge-deleted <page>...     # 清掉已软删除的占位记录
    python scripts/admin.py purge-reactions <page>...   # 重置某页的表情互动

A soft delete keeps the row and clears its content, so `purge-body` can never
match it by text — `purge-deleted` removes those placeholders by id. Only the
ones that no longer hold a thread are removed; the server does the same sweep at
startup, so this command is the manual version of it.

Environment::

    MKC_ADMIN_TOKEN  与服务端一致的管理员令牌（必需）
    MKC_BASE_URL     后端地址，默认 http://127.0.0.1:8000
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import Client  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "backend" / "comments.db"

client = Client()


def fetch_all(page: str) -> dict | None:
    return client.fetch_all(page)


def delete_tree(comment_id: str) -> bool:
    """Hard-delete a thread; 404 means it was already swept with its parent."""
    status, _ = client.call("DELETE", f"/comments/{comment_id}?hard=true")
    return status in (200, 404)


def cmd_list(page: str) -> None:
    listing = fetch_all(page)
    if listing is None:
        sys.exit(f"无法读取 {page}（请检查 MKC_ADMIN_TOKEN）")
    print(f"==> {page}: {listing['total']} 条评论，浏览 {listing['stats']['views']}")
    for comment in listing["comments"]:
        kind = "回复" if comment["thread_id"] != comment["id"] else "评论"
        state = "已删除" if comment["deleted"] else "正常"
        print(f"  {comment['id'][:8]}  {kind}  {comment['author']:<18} {state}")


def cmd_purge(page: str) -> None:
    listing = fetch_all(page)
    if listing is None:
        sys.exit(f"无法读取 {page}（请检查 MKC_ADMIN_TOKEN）")
    roots = [c for c in listing["comments"] if c["thread_id"] == c["id"]]
    print(f"==> {page}: {listing['total']} 条评论（{len(roots)} 个主题）")
    for comment in roots:
        if delete_tree(comment["id"]):
            print(f"  已删除主题 {comment['id'][:8]} ({comment['author']})")
    after = fetch_all(page)
    print(f"==> 完成，剩余 {after['total'] if after else '?'} 条")


def cmd_purge_body(text: str, pages: list[str]) -> None:
    removed = 0
    for page in pages:
        listing = fetch_all(page)
        for comment in (listing or {}).get("comments", []):
            if text in (comment.get("content") or ""):
                if delete_tree(comment["id"]):
                    removed += 1
                    print(f"  已删除 {page} -> {comment['id'][:8]} ({comment['author']})")
    print(f"==> 共删除 {removed} 条")


def cmd_purge_deleted(pages: list[str]) -> None:
    """Remove the soft-deleted placeholders on these pages.

    Deliberately not ``delete_tree``: that deletes a whole ``thread_id``, and a
    tombstone shares its thread with the comments underneath it. Cleaning one up
    used to take every reply with it — and for a tombstoned *reply* it took the
    live root too. Replies are rendered by ``thread_id``, so a missing root does
    not even show up as an error: the thread simply disappears.

    A placeholder that still holds replies is therefore left alone and reported;
    the server sweeps childless ones at startup anyway.
    """
    removed = 0
    kept = 0
    with sqlite3.connect(DB_PATH) as conn:
        for page in pages:
            while True:
                rows = [
                    (row[0], row[1])
                    for row in conn.execute(
                        """
                        SELECT c.id,
                               (SELECT count(*) FROM comments k WHERE k.parent_id = c.id)
                          FROM comments c
                         WHERE c.page = ? AND c.deleted_at IS NOT NULL
                        """,
                        (page,),
                    )
                ]
                childless = [cid for cid, kids in rows if not kids]
                if not childless:
                    # Only placeholders still holding a thread remain, and those
                    # must stay. Reporting here means each one is named once.
                    for cid, kids in rows:
                        kept += 1
                        print(f"  保留 {page} -> {cid[:8]}（仍挂着 {kids} 条回复）")
                    break
                for cid in childless:
                    conn.execute(
                        "DELETE FROM reactions WHERE target_type = 'comment' AND target_id = ?",
                        (cid,),
                    )
                    if conn.execute("DELETE FROM comments WHERE id = ?", (cid,)).rowcount:
                        removed += 1
                        print(f"  已清理 {page} -> {cid[:8]}")
                # A tombstone whose only child was another tombstone becomes
                # childless in this pass, so go round again.
        conn.commit()
    print(f"==> 共清理 {removed} 条，保留 {kept} 条仍挂在主题上")


def cmd_purge_reactions(pages: list[str]) -> None:
    """Drop every reaction recorded on the given pages.

    Useful to reset demo state, or to clear rows written before reactors were
    named (which can never appear in the hover tooltip).
    """
    removed = 0
    with sqlite3.connect(DB_PATH) as conn:
        for page in pages:
            targets = [page] + [
                row[0]
                for row in conn.execute("select id from comments where page = ?", (page,))
            ]
            placeholders = ",".join("?" for _ in targets)
            cursor = conn.execute(
                f"DELETE FROM reactions WHERE target_id IN ({placeholders})", targets
            )
            removed += cursor.rowcount
            print(f"  已重置 {page}：{cursor.rowcount} 条表情")
        conn.commit()
    print(f"==> 共重置 {removed} 条表情")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in {"list", "purge", "purge-body", "purge-deleted", "purge-reactions"}:
        print(__doc__)
        return 1
    if not client.admin_token:
        sys.exit("请先设置 MKC_ADMIN_TOKEN 环境变量（需与服务端一致）。")

    command, rest = args[0], args[1:]
    if command == "list":
        cmd_list(rest[0] if rest else "/")
    elif command == "purge":
        if not rest:
            sys.exit("用法：admin.py purge <page>")
        cmd_purge(rest[0])
    elif command == "purge-deleted":
        if not rest:
            sys.exit("用法：admin.py purge-deleted <page> [page ...]")
        cmd_purge_deleted(rest)
    elif command == "purge-reactions":
        if not rest:
            sys.exit("用法：admin.py purge-reactions <page> [page ...]")
        cmd_purge_reactions(rest)
    else:
        if len(rest) < 2:
            sys.exit("用法：admin.py purge-body <text> <page> [page ...]")
        cmd_purge_body(rest[0], rest[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
