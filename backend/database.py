"""SQLite persistence layer.

The schema is intentionally simple and dependency free (``sqlite3`` from the
standard library). Comments are nested exactly one level deep, which is what
the frontend renders:

* a **root** comment has ``thread_id == id`` and ``parent_id IS NULL``
* a **reply**  has ``thread_id`` = root id and ``parent_id`` = comment it
  answers (the root itself, or another reply for the ``@mention`` hint)
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

logger = logging.getLogger("mkdocs-comment")

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id                TEXT PRIMARY KEY,
    page              TEXT NOT NULL,
    parent_id         TEXT,
    thread_id         TEXT NOT NULL,
    author            TEXT NOT NULL,
    content           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    deleted_at        TEXT,
    delete_token_hash TEXT,
    client_ip         TEXT,
    visitor_id        TEXT,
    user_agent        TEXT
);

CREATE INDEX IF NOT EXISTS idx_comments_page
    ON comments (page, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_thread
    ON comments (thread_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_comments_parent
    ON comments (parent_id);

CREATE TABLE IF NOT EXISTS reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    emoji       TEXT NOT NULL,
    visitor_id  TEXT NOT NULL,
    author      TEXT,
    client_ip   TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (target_type, target_id, emoji, visitor_id)
);

CREATE INDEX IF NOT EXISTS idx_reactions_target
    ON reactions (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_reactions_visitor
    ON reactions (target_type, visitor_id);

CREATE TABLE IF NOT EXISTS page_stats (
    page       TEXT PRIMARY KEY,
    views      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

VALID_TARGETS = {"comment", "page"}

# Older databases may still hold a `visitors` table mapping a visitor id to a
# display name. Nothing reads or writes it any more: an identity is now derived
# from the address, so a per-visitor name would be attributed to everyone behind
# that address, and the name to show is already recorded on each row. The table
# is left in place rather than dropped — it is derived data from an older
# release, and a migration should not delete rows on its own initiative.

# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` silently
# skips an existing table, so an upgraded deployment would otherwise never see
# these; `_ensure_columns` applies them instead.
MIGRATIONS: Dict[str, Dict[str, str]] = {
    "comments": {
        "visitor_id": "TEXT",
    },
    "reactions": {
        "author": "TEXT",
        "client_ip": "TEXT",
    },
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    """Thin wrapper around a SQLite file."""

    def __init__(self, path: str, anonymous_name: str = "") -> None:
        """
        ``anonymous_name`` is the placeholder a blank nickname resolves to, when
        the deployment uses one. Knowing it lets startup recognise rows that an
        older release published under an address instead. Pass an empty string
        to opt out, which is what ``default_author: ip`` amounts to.
        """
        self.path = path
        self.anonymous_name = (anonymous_name or "").strip()
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)
            # Re-label rows an older release published under the visitor's
            # address. Idempotent, and skipped in `default_author: ip` mode.
            if self.anonymous_name:
                renamed = self._relabel_address_authors(conn, self.anonymous_name)
                if renamed:
                    logger.info(
                        "已将 %d 条以 IP 为昵称的历史记录改为「%s」",
                        renamed,
                        self.anonymous_name,
                    )

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        """Bring an existing database up to the current schema.

        SQLite's ``ALTER TABLE ADD COLUMN`` is cheap and, once guarded by
        ``PRAGMA table_info``, idempotent — much simpler than a migration
        framework for a schema this small.
        """
        for table, columns in MIGRATIONS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @staticmethod
    def _relabel_address_authors(conn: sqlite3.Connection, anonymous_name: str) -> int:
        """Rename rows published under the visitor's address. See the public wrapper."""
        total = 0
        for table in ("comments", "reactions"):
            cursor = conn.execute(
                f"""
                UPDATE {table} SET author = ?
                 WHERE client_ip IS NOT NULL AND TRIM(client_ip) <> ''
                   AND author IS NOT NULL AND TRIM(author) = TRIM(client_ip)
                """,
                (anonymous_name,),
            )
            total += cursor.rowcount
        return total

    def normalize_anonymous_authors(self, anonymous_name: str) -> int:
        """Re-label rows that were published under the visitor's address.

        Older versions used the address *as* the name, so leaving the nickname
        box empty published you as ``192.0.2.7``. Now the address is shown
        beside the name instead, which makes such a stored name both ugly and
        redundant — and the row is indistinguishable from one where the address
        is the fallback either way. Re-labelling loses nothing: the address
        itself stays in ``client_ip``.

        Runs from :meth:`init_schema` when a placeholder name was configured,
        and is idempotent either way.
        """
        with self.connect() as conn:
            return self._relabel_address_authors(conn, anonymous_name)

    # ------------------------------------------------------------------ #
    # comments
    # ------------------------------------------------------------------ #
    def create_comment(
        self,
        *,
        page: str,
        author: str,
        content: str,
        parent_id: Optional[str],
        delete_token_hash: str,
        client_ip: str,
        user_agent: str,
        visitor_id: str = "",
    ) -> Dict[str, Any]:
        now = utcnow()
        comment_id = new_id()
        thread_id = comment_id

        with self.connect() as conn:
            if parent_id:
                row = conn.execute(
                    "SELECT id, thread_id, page FROM comments WHERE id = ?",
                    (parent_id,),
                ).fetchone()
                if row is None or row["page"] != page:
                    raise ValueError("父评论不存在")
                thread_id = row["thread_id"]

            conn.execute(
                """
                INSERT INTO comments (
                    id, page, parent_id, thread_id, author, content,
                    created_at, updated_at, deleted_at, delete_token_hash,
                    client_ip, visitor_id, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    comment_id, page, parent_id, thread_id, author, content,
                    now, delete_token_hash, client_ip, visitor_id, user_agent[:512],
                ),
            )

        return self.get_comment(comment_id)  # type: ignore[return-value]

    def get_comment(self, comment_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single comment, including its parent's author for `@mention`."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT c.*, p.author AS parent_author
                FROM comments c
                LEFT JOIN comments p ON p.id = c.parent_id
                WHERE c.id = ?
                """,
                (comment_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_root_comments(
        self, page: str, limit: int, offset: int
    ) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, p.author AS parent_author
                FROM comments c
                LEFT JOIN comments p ON p.id = c.parent_id
                WHERE c.page = ? AND c.thread_id = c.id
                ORDER BY c.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (page, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_replies(self, thread_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not thread_ids:
            return []
        placeholders = ",".join("?" for _ in thread_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.*, p.author AS parent_author
                FROM comments c
                LEFT JOIN comments p ON p.id = c.parent_id
                WHERE c.thread_id IN ({placeholders}) AND c.thread_id != c.id
                ORDER BY c.created_at ASC
                """,
                tuple(thread_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_comments(self, page: str) -> Dict[str, int]:
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM comments WHERE page = ?", (page,)
            ).fetchone()["n"]
            roots = conn.execute(
                "SELECT COUNT(*) AS n FROM comments WHERE page = ? AND thread_id = id",
                (page,),
            ).fetchone()["n"]
        return {"total": int(total), "root_total": int(roots)}

    def get_delete_token_hash(self, comment_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT delete_token_hash FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
        return row["delete_token_hash"] if row else None

    def soft_delete_comment(self, comment_id: str) -> bool:
        """Tombstone a comment: keep the row, drop the text.

        Used when the comment still anchors replies. The author is kept on
        purpose so the `@mention` those replies quote still resolves.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE comments
                   SET deleted_at = ?, content = '', delete_token_hash = NULL
                 WHERE id = ? AND deleted_at IS NULL
                """,
                (utcnow(), comment_id),
            )
            return cursor.rowcount > 0

    def comment_has_children(self, comment_id: str) -> bool:
        """True when anything was posted in reply to this comment."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM comments WHERE parent_id = ? LIMIT 1", (comment_id,)
            ).fetchone()
        return row is not None

    def delete_comment_only(self, comment_id: str) -> List[str]:
        """Hard delete a childless comment, its reactions and any tombstone it orphans.

        Returns every id that was actually removed, in the order they went.

        Deleting the last reply of a thread whose root is already a tombstone
        has to take the root with it. Otherwise the thread stays anchored to a
        permanent "该评论已被删除" placeholder with nothing underneath it, which
        is exactly what makes deleting a thread feel like it did not work.
        """
        removed: List[str] = []
        with self.connect() as conn:
            row = conn.execute(
                "SELECT parent_id FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
            if row is None:
                return removed
            self._delete_rows(conn, [comment_id], removed)
            self._purge_orphaned_tombstones(conn, row["parent_id"], removed)
        return removed

    @staticmethod
    def _delete_rows(
        conn: sqlite3.Connection, ids: Sequence[str], removed: List[str]
    ) -> None:
        """Delete comments (and their reactions), recording what really went."""
        for comment_id in ids:
            conn.execute(
                "DELETE FROM reactions WHERE target_type = 'comment' AND target_id = ?",
                (comment_id,),
            )
            if conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,)).rowcount:
                removed.append(comment_id)

    @classmethod
    def _purge_orphaned_tombstones(
        cls, conn: sqlite3.Connection, start_id: Optional[str], removed: List[str]
    ) -> None:
        """Walk up the parent chain, dropping tombstones that lost their last child.

        A tombstone exists only to hold a thread together, and walking up lets
        one nested deletion collapse a whole emptied chain rather than leaving
        a row of placeholders behind.
        """
        current = start_id
        while current:
            row = conn.execute(
                "SELECT id, parent_id, deleted_at FROM comments WHERE id = ?",
                (current,),
            ).fetchone()
            if row is None or not row["deleted_at"]:
                return
            still_used = conn.execute(
                "SELECT 1 FROM comments WHERE parent_id = ? LIMIT 1", (current,)
            ).fetchone()
            if still_used is not None:
                return
            parent_id = row["parent_id"]
            cls._delete_rows(conn, [current], removed)
            current = parent_id

    def delete_comment_tree(self, comment_id: str) -> List[str]:
        """Hard delete a thread (admin only).

        Returns every id that was removed, like :meth:`delete_comment_only` — a
        whole thread rather than a few rows, but the same answer to the same
        question, so callers can report what actually happened.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT thread_id FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
            if row is None:
                return []
            thread_id = row["thread_id"]
            conn.execute(
                "DELETE FROM reactions WHERE target_type = 'comment' AND target_id IN "
                "(SELECT id FROM comments WHERE thread_id = ?)",
                (thread_id,),
            )
            batch = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM comments WHERE thread_id = ? ORDER BY created_at ASC",
                    (thread_id,),
                )
            ]
            # `thread_id = ?` already covers the root itself, which stores its
            # own id as its thread id.
            conn.execute("DELETE FROM comments WHERE thread_id = ?", (thread_id,))
            return batch

    def purge_childless_tombstones(self) -> List[str]:
        """Drop every tombstone that no longer holds a thread together.

        A tombstone is a placeholder: it exists so the replies underneath keep a
        parent to sit under and their `@mention` keeps resolving. With no replies
        left it anchors nothing, and all a reader sees is a permanent
        「该评论已被删除」 line with empty space below it — the exact thing that
        makes deleting a comment look like it failed.

        Normal deletions never leave one behind: :meth:`delete_comment_only`
        walks up the parent chain and clears it as the last reply goes. This
        sweep exists for the states that are *not* normal — rows left by a
        release that predates that walk, a database restored from an old backup,
        or a hand-edited row. It runs once at startup so an upgraded deployment
        heals itself instead of displaying the breakage forever.

        Repeats until nothing changes, because clearing one tombstone can orphan
        the tombstone above it. Idempotent, and cheap: the table holds comments,
        not events.
        """
        removed: List[str] = []
        with self.connect() as conn:
            while True:
                ids = [
                    row["id"]
                    for row in conn.execute(
                        """
                        SELECT c.id
                          FROM comments c
                         WHERE c.deleted_at IS NOT NULL
                           AND NOT EXISTS (
                                 SELECT 1 FROM comments k WHERE k.parent_id = c.id
                           )
                      ORDER BY c.created_at ASC
                        """
                    )
                ]
                if not ids:
                    break
                # Each pass deletes at least one row, so this terminates; the
                # loop is what collapses a chain of tombstones bottom-up.
                self._delete_rows(conn, ids, removed)
        return removed

    # ------------------------------------------------------------------ #
    # page statistics
    # ------------------------------------------------------------------ #
    def increment_view(self, page: str) -> int:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO page_stats (page, views, created_at, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(page) DO UPDATE SET
                    views = views + 1,
                    updated_at = excluded.updated_at
                """,
                (page, now, now),
            )
            row = conn.execute(
                "SELECT views FROM page_stats WHERE page = ?", (page,)
            ).fetchone()
        return int(row["views"]) if row else 0

    def get_views(self, page: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT views FROM page_stats WHERE page = ?", (page,)
            ).fetchone()
        return int(row["views"]) if row else 0

    def get_views_many(self, pages: Sequence[str]) -> Dict[str, int]:
        if not pages:
            return {}
        placeholders = ",".join("?" for _ in pages)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT page, views FROM page_stats WHERE page IN ({placeholders})",
                tuple(pages),
            ).fetchall()
        return {row["page"]: int(row["views"]) for row in rows}

    # ------------------------------------------------------------------ #
    # reactions
    # ------------------------------------------------------------------ #
    def toggle_reaction(
        self,
        target_type: str,
        target_id: str,
        emoji: str,
        visitor_id: str,
        display_name: str = "",
        client_ip: str = "",
    ) -> bool:
        """Add or remove a reaction. Returns ``True`` when now active.

        Toggling is keyed on ``visitor_id``, so the same reader clicking twice
        removes their reaction rather than adding a second one. The name is a
        copy taken at write time; it is what the hover tooltip lists.
        """
        if target_type not in VALID_TARGETS:
            raise ValueError("非法的 target_type")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM reactions
                 WHERE target_type = ? AND target_id = ? AND emoji = ? AND visitor_id = ?
                """,
                (target_type, target_id, emoji, visitor_id),
            ).fetchone()
            if row is not None:
                conn.execute("DELETE FROM reactions WHERE id = ?", (row["id"],))
                return False
            conn.execute(
                """
                INSERT INTO reactions (
                    target_type, target_id, emoji, visitor_id, author, client_ip, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_type,
                    target_id,
                    emoji,
                    visitor_id,
                    display_name or client_ip,
                    client_ip,
                    utcnow(),
                ),
            )
            return True

    def reaction_actors(
        self, target_type: str, target_ids: Sequence[str]
    ) -> Dict[str, Dict[str, List[str]]]:
        """Who reacted with what, in the order they did so.

        Backs the hover tooltip. The name is the copy recorded on the row when
        the reaction was left, which keeps the list honest: there is no way to
        attribute a name to an address that a later reader shares.

        Reactors with no name at all are skipped; :meth:`reaction_counts` still
        reports them, and the UI spells out the remainder instead of quietly
        showing a short list.
        """
        if not target_ids:
            return {}
        placeholders = ",".join("?" for _ in target_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT target_id, emoji, author
                  FROM reactions
                 WHERE target_type = ? AND target_id IN ({placeholders})
                 ORDER BY created_at ASC, id ASC
                """,
                (target_type, *target_ids),
            ).fetchall()
        result: Dict[str, Dict[str, List[str]]] = {}
        for row in rows:
            name = (row["author"] or "").strip()
            if not name:
                continue
            names = result.setdefault(row["target_id"], {}).setdefault(row["emoji"], [])
            # Two rows can carry the same name (a reader who renamed themselves,
            # or two people who picked the same one), and listing it twice would
            # only make the tooltip look wrong.
            if name not in names:
                names.append(name)
        return result

    def reaction_counts(
        self, target_type: str, target_ids: Sequence[str]
    ) -> Dict[str, Dict[str, int]]:
        if not target_ids:
            return {}
        placeholders = ",".join("?" for _ in target_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT target_id, emoji, COUNT(*) AS n
                  FROM reactions
                 WHERE target_type = ? AND target_id IN ({placeholders})
                 GROUP BY target_id, emoji
                """,
                (target_type, *target_ids),
            ).fetchall()
        result: Dict[str, Dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["target_id"], {})[row["emoji"]] = int(row["n"])
        return result

    def my_reactions(
        self, target_type: str, target_ids: Sequence[str], visitor_id: str
    ) -> Dict[str, List[str]]:
        if not target_ids or not visitor_id:
            return {}
        placeholders = ",".join("?" for _ in target_ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT target_id, emoji
                  FROM reactions
                 WHERE target_type = ? AND visitor_id = ? AND target_id IN ({placeholders})
                """,
                (target_type, visitor_id, *target_ids),
            ).fetchall()
        result: Dict[str, List[str]] = {}
        for row in rows:
            result.setdefault(row["target_id"], []).append(row["emoji"])
        return result

    def delete_orphan_reactions(self) -> None:
        """Housekeeping: drop reactions pointing at removed comments / pages."""
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM reactions
                 WHERE target_type = 'comment'
                   AND target_id NOT IN (SELECT id FROM comments)
                """
            )
