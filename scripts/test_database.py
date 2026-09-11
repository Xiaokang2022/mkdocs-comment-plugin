"""Persistence-layer tests for the two behaviours the UI depends on.

They run against a throwaway SQLite file, so they are fast, need no server, and
can build a *pre-upgrade* database to check that an existing deployment is
brought forward correctly.

Run directly (``python scripts/test_database.py``) or with pytest.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from database import Database  # noqa: E402

passed = 0
failed = 0

# The schema exactly as it shipped before the `visitors` work: no
# `comments.visitor_id`, and no name columns on `reactions` at all.
LEGACY_SCHEMA = """
CREATE TABLE comments (
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
    user_agent        TEXT
);
CREATE TABLE reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    emoji       TEXT NOT NULL,
    visitor_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (target_type, target_id, emoji, visitor_id)
);
CREATE TABLE page_stats (
    page       TEXT PRIMARY KEY,
    views      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def check(label: str, condition: bool, extra: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}".rstrip())


def _columns(path: str, table: str) -> set:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _visitors(path: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {row[0]: row[1] for row in conn.execute("SELECT visitor_id, name FROM visitors")}
    finally:
        conn.close()


def _exec(path: str, sql: str, params: tuple = ()) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _make_comment(db: Database, page: str, author: str, parent_id=None, token="t") -> str:
    row = db.create_comment(
        page=page,
        author=author,
        content=f"{author} 的内容",
        parent_id=parent_id,
        delete_token_hash=token,
        client_ip="203.0.113.7",
        user_agent="pytest",
        visitor_id=f"visitor-{author}",
        visitor_name=author,
    )
    return row["id"]


def test_upgrade_and_name_recovery() -> None:
    global passed, failed
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript(LEGACY_SCHEMA)
        conn.commit()
        conn.close()

        print("--- 旧库升级")
        db = Database(path)
        check("comments 补上 visitor_id", "visitor_id" in _columns(path, "comments"))
        check("reactions 补上 author", "author" in _columns(path, "reactions"))
        check("reactions 补上 client_ip", "client_ip" in _columns(path, "reactions"))
        check("新建 visitors 表", "name" in _columns(path, "visitors"))

        # Four shapes of historical row, each on its own emoji because a visitor
        # can only hold one row per emoji and target:
        #   👍 a pre-migration row: no name, no address — nothing of its own
        #   ❤️ the row that teaches us that visitor's name
        #   🎉 an address only
        #   🔥 a name that is literally the address, i.e. the old fallback
        print("--- 旧数据回填")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,created_at) "
                    "VALUES ('comment','c1','👍','v-named','2026-01-01T00:00:00+00:00')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,author,client_ip,created_at) "
                    "VALUES ('comment','c1','❤️','v-named','甲','9.9.9.9','2026-01-02T00:00:00+00:00')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,author,client_ip,created_at) "
                    "VALUES ('comment','c1','🎉','v-ip',NULL,'10.0.0.1','2026-01-03T00:00:00+00:00')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,author,client_ip,created_at) "
                    "VALUES ('comment','c1','🔥','v-ghost','10.0.0.1','10.0.0.1','2026-01-04T00:00:00+00:00')")

        db.init_schema()  # idempotent, and this is where the backfill runs

        actors = db.reaction_actors("comment", ["c1"]).get("c1", {})
        # The 👍 row has no author of its own; it is resolved through the
        # visitor, which is exactly the row that used to be invisible.
        check("无名行按访客恢复姓名", actors.get("👍") == ["甲"], str(actors))
        check("行内姓名仍然可用", actors.get("❤️") == ["甲"], str(actors))
        # Nothing is invented for a row that only has an address: an address is
        # not a name, and the display layer applies it as a fallback itself.
        check("只有地址的行不会被当成姓名", "🎉" not in actors, str(actors))
        check("两项线索都没有的行仍被计入总数",
              db.reaction_counts("comment", ["c1"]).get("c1", {}).get("👍") == 1,
              str(db.reaction_counts("comment", ["c1"])))
        check("同一个名字不会重复列出", (actors.get("👍") or []).count("甲") == 1, str(actors))

        print("--- 历史 IP 昵称归并")
        # `🔥` stores the address *as* the name, which is what an older version
        # did when the nickname box was left empty.
        check("归并前它显示为地址", actors.get("🔥") == ["10.0.0.1"], str(actors))
        renamed = db.normalize_anonymous_authors("匿名用户")
        check("至少改掉一条", renamed >= 1, str(renamed))
        actors = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("地址昵称被改成匿名名字", actors.get("🔥") == ["匿名用户"], str(actors))
        check("再跑一次不改任何行", db.normalize_anonymous_authors("匿名用户") == 0)
        check("真实昵称不受影响", actors.get("❤️") == ["甲"], str(actors))

        print("--- 记住的永远只有真实昵称")
        before = db.reaction_actors("comment", ["c1"]).get("c1", {})
        with db.connect() as c:
            db._remember_visitor(c, "", "无名")
            db._remember_visitor(c, "v-x", "   ")
        after = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("空白访客/昵称被忽略，不影响名单", before == after, f"{before} != {after}")

        # The copy on the row is a snapshot; the visitor is the source of truth,
        # so a name learned later replaces an address that was only a fallback.
        with db.connect() as c:
            db._remember_visitor(c, "v-ip", "后来改了名")
        actors2 = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("改名后旧记录跟着改",
              actors2.get("🎉") == ["后来改了名"],
              str(actors2))

        with db.connect() as c:
            db._remember_visitor(c, "v-ghost", "幽灵读者")
        actors3 = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("从未留名者一旦留名，历史记录恢复可见",
              actors3.get("🔥") == ["幽灵读者"],
              str(actors3))
        check("四个表情现在都能列出名字",
              all(len(v) == 1 for v in actors3.values()) and len(actors3) == 4,
              str(actors3))

        print("--- 共享的 IP 身份不记名字")
        # `ip-…` ids are derived from address + user agent, so everybody behind
        # one address shares them. A nickname stored against one would be shown
        # for strangers' reactions.
        _exec(path, "INSERT INTO visitors (visitor_id, name, updated_at) "
                    "VALUES ('ip-0123456789abcdef','不该保留','2026-01-01T00:00:00+00:00')")
        with db.connect() as c:
            db._remember_visitor(c, "ip-deadbeef", "也不该记住")
            db._remember_visitor(c, "v-real", "真实的")
        db.init_schema()
        stored = _visitors(path)
        check("已有的 ip- 条目被清除", "ip-0123456789abcdef" not in stored, str(stored))
        check("新的 ip- 条目不会写入", "ip-deadbeef" not in stored, str(stored))
        check("普通浏览器 UUID 正常记住", stored.get("v-real") == "真实的", str(stored))


def test_tombstone_sweep() -> None:
    global passed, failed
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "sweep.db")
        db = Database(path)
        page = "/p"

        print("--- 末条回复被删除后腾清墓碑")
        root = _make_comment(db, page, "楼主")
        child = _make_comment(db, page, "回复者", parent_id=root)
        check("根评论挂在 thread 上", db.get_comment(child)["thread_id"] == root)

        db.soft_delete_comment(root)
        check("有回复时根评论变墓碑", db.get_comment(root)["deleted_at"] is not None)

        removed = db.delete_comment_only(child)
        check("删除回复返回两个 id", sorted(removed) == sorted([child, root]), str(removed))
        check("根评论已从库中消失", db.get_comment(root) is None)
        check("回复也已从库中消失", db.get_comment(child) is None)
        check("列表中不残留任何行", db.count_comments(page)["total"] == 0,
              str(db.count_comments(page)))

        print("--- 未删除的根评论不受影响")
        root2 = _make_comment(db, page, "楼主二")
        child2 = _make_comment(db, page, "回复者二", parent_id=root2)
        removed = db.delete_comment_only(child2)
        check("只删掉回复本身", removed == [child2], str(removed))
        check("根评论仍在", db.get_comment(root2) is not None)
        check("根评论未被标记删除", db.get_comment(root2)["deleted_at"] is None)

        print("--- 墓碑链一起腾清")
        # A reply can itself be replied to, so a deletion can empty a whole
        # chain of tombstones rather than just the one at the top.
        a = _make_comment(db, page, "A")
        b = _make_comment(db, page, "B", parent_id=a)
        c = _make_comment(db, page, "C", parent_id=b)
        db.soft_delete_comment(a)
        db.soft_delete_comment(b)
        check("两层墓碑就位",
              db.get_comment(a)["deleted_at"] is not None and db.get_comment(b)["deleted_at"] is not None)
        removed = db.delete_comment_only(c)
        check("整条墓碑链被带走", sorted(removed) == sorted([c, b, a]), str(removed))
        check("链上不留残余", db.count_comments(page)["total"] == 1,
              str(db.count_comments(page)))

        print("--- 墓碑仍带着回复时不腾清")
        d = _make_comment(db, page, "D")
        e = _make_comment(db, page, "E", parent_id=d)
        f = _make_comment(db, page, "F", parent_id=d)
        db.soft_delete_comment(d)
        removed = db.delete_comment_only(e)
        check("另一个回复还在，墓碑必须保留", removed == [e], str(removed))
        check("墓碑仍在且仍是墓碑",
              db.get_comment(d) is not None and db.get_comment(d)["deleted_at"] is not None)
        check("剩下的回复未受影响", db.get_comment(f) is not None)

        print("--- 墓碑腾清时其点赞一并清除")
        g = _make_comment(db, page, "G")
        h = _make_comment(db, page, "H", parent_id=g)
        db.toggle_reaction("comment", g, "👍", "v1", display_name="点赞者", nickname="点赞者")
        db.toggle_reaction("comment", h, "👍", "v1", display_name="点赞者", nickname="点赞者")
        db.soft_delete_comment(g)
        removed = db.delete_comment_only(h)
        check("两个 id 都被删除", sorted(removed) == sorted([h, g]), str(removed))
        check("墓碑上的点赞也没了",
              db.reaction_counts("comment", [g]).get(g, {}) == {},
              str(db.reaction_counts("comment", [g])))


def test_purge_of_unknown_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "unknown.db"))
        print("--- 删除不存在的评论")
        check("返回空列表而不是抛错", db.delete_comment_only("nope") == [])
        check("整树删除同样返回空列表", db.delete_comment_tree("nope") == [])


    print("--- 整树硬删除列出全部 id")
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "tree.db"))
        root = _make_comment(db, "/t", "根")
        first = _make_comment(db, "/t", "一", parent_id=root)
        second = _make_comment(db, "/t", "二", parent_id=first)
        db.toggle_reaction("comment", root, "👍", "v1", display_name="赞", nickname="赞")
        removed = db.delete_comment_tree(second)
        check(
            "从任意一层删除都带走整条 thread",
            sorted(removed) == sorted([root, first, second]),
            str(removed),
        )
        check("库里已无残留", db.count_comments("/t")["total"] == 0)
        check("点赞也随之清除",
              db.reaction_counts("comment", [root]).get(root, {}) == {},
              str(db.reaction_counts("comment", [root])))


def test_anonymous_name_is_not_a_nickname() -> None:
    """The placeholder must never be learned as somebody's nickname."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "anon.db")
        print("--- 构造时给出占位名就自动归并")
        # Rows that only carry an address, as an older version wrote them.
        conn = sqlite3.connect(path)
        conn.executescript(LEGACY_SCHEMA)
        conn.commit()
        conn.close()
        _exec(path, "INSERT INTO comments (id,page,thread_id,author,content,created_at,client_ip) "
                    "VALUES ('c1','/p','c1','10.0.0.1','旧评论','2026-01-01T00:00:00+00:00','10.0.0.1')")
        _exec(path, "INSERT INTO comments (id,page,thread_id,author,content,created_at,client_ip) "
                    "VALUES ('c2','/p','c2','小明','正常评论','2026-01-01T00:00:00+00:00','10.0.0.1')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,created_at) "
                    "VALUES ('comment','c1','👍','v1','2026-01-01T00:00:00+00:00')")

        db = Database(path, anonymous_name="匿名用户")
        check("地址昵称被改成占位名", db.get_comment("c1")["author"] == "匿名用户",
              str(db.get_comment("c1")["author"]))
        check("真实昵称不动", db.get_comment("c2")["author"] == "小明")
        check("地址仍然保留在 client_ip", db.get_comment("c1")["client_ip"] == "10.0.0.1")

        print("--- 占位名不会被学成昵称")
        # The v1 row now carries the placeholder, and the seeding pass must not
        # read it back as a nickname this visitor chose.
        with db.connect() as c:
            db.toggle_reaction("comment", "c1", "🎉", "v1", display_name="匿名用户")
        stored = _visitors(path)
        check("visitors 里没有占位名", "匿名用户" not in stored.values(), str(stored))

        print("--- 真的留名时照样记住")
        with db.connect() as c:
            db._remember_visitor(c, "v1", "后来的名字")
        check("真实昵称正常写入", _visitors(path).get("v1") == "后来的名字")

        print("--- 选用 IP 作为昵称时就完全不归并")
        path2 = str(Path(tmp) / "ipmode.db")
        conn = sqlite3.connect(path2)
        conn.executescript(LEGACY_SCHEMA)
        conn.commit()
        conn.close()
        _exec(path2, "INSERT INTO comments (id,page,thread_id,author,content,created_at,client_ip) "
                     "VALUES ('c1','/p','c1','10.0.0.1','旧评论','2026-01-01T00:00:00+00:00','10.0.0.1')")
        db2 = Database(path2, anonymous_name="")
        check("未给占位名则保持原样", db2.get_comment("c1")["author"] == "10.0.0.1",
              str(db2.get_comment("c1")["author"]))


def main() -> int:
    test_upgrade_and_name_recovery()
    print()
    test_anonymous_name_is_not_a_nickname()
    print()
    test_tombstone_sweep()
    print()
    test_purge_of_unknown_id()

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
