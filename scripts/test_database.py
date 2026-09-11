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
        visitor_id=f"ip-{author}",
    )
    return row["id"]


def test_upgrade_of_an_older_database() -> None:
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

        # Three shapes of historical row, each on its own emoji because a visitor
        # can only hold one row per emoji and target:
        #   👍 a pre-migration row: no name, no address — nothing of its own
        #   ❤️ a real nickname
        #   🔥 a name that is literally the address, i.e. the old fallback
        print("--- 旧数据的署名")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,created_at) "
                    "VALUES ('comment','c1','👍','v-named','2026-01-01T00:00:00+00:00')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,author,client_ip,created_at) "
                    "VALUES ('comment','c1','❤️','v-named','甲','9.9.9.9','2026-01-02T00:00:00+00:00')")
        _exec(path, "INSERT INTO reactions (target_type,target_id,emoji,visitor_id,author,client_ip,created_at) "
                    "VALUES ('comment','c1','🔥','v-old','10.0.0.1','10.0.0.1','2026-01-04T00:00:00+00:00')")

        db.init_schema()  # idempotent, and this is where the relabelling runs

        actors = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("行内姓名直接可用", actors.get("❤️") == ["甲"], str(actors))
        # Nothing is invented for a row that never had a name: neither an
        # address nor a placeholder is a name somebody chose.
        check("从未署名的行不出现在名单里", "👍" not in actors, str(actors))
        check("但它仍被计入总数",
              db.reaction_counts("comment", ["c1"]).get("c1", {}).get("👍") == 1,
              str(db.reaction_counts("comment", ["c1"])))

        print("--- 历史 IP 昵称归并")
        check("归并前它显示为地址", actors.get("🔥") == ["10.0.0.1"], str(actors))
        renamed = db.normalize_anonymous_authors("匿名用户")
        check("至少改掉一条", renamed >= 1, str(renamed))
        actors = db.reaction_actors("comment", ["c1"]).get("c1", {})
        check("地址昵称被改成匿名名字", actors.get("🔥") == ["匿名用户"], str(actors))
        check("再跑一次不改任何行", db.normalize_anonymous_authors("匿名用户") == 0)
        check("真实昵称不受影响", actors.get("❤️") == ["甲"], str(actors))

        print("--- 名单顺序与去重")
        # Order is what the tooltip shows, so it has to be the order people
        # reacted in, and a repeated name must not be listed twice.
        for who in ("乙", "甲", "丙"):
            db.toggle_reaction("comment", "c1", "👍", f"ip-{who}", display_name=who)
        names = db.reaction_actors("comment", ["c1"]).get("c1", {}).get("👍")
        check("按点赞先后排列", names == ["乙", "甲", "丙"], str(names))
        db.toggle_reaction("comment", "c1", "👍", "ip-乙")  # same visitor toggles off
        names = db.reaction_actors("comment", ["c1"]).get("c1", {}).get("👍")
        check("同一访客再点一次是取消", names == ["甲", "丙"], str(names))
        # The count stays authoritative: the pre-migration 👍 row has no name and
        # is still counted, which is exactly what the tooltip spells out.
        count = db.reaction_counts("comment", ["c1"])["c1"]["👍"]
        check("计数不少于名单长度", count >= len(names), f"{count} vs {names}")
        check("差额正好是那条无名旧记录", count - len(names) == 1, f"{count} vs {names}")

        print("--- 同一地址就是同一个人")
        added = db.toggle_reaction("comment", "c2", "🎉", "ip-shared", display_name="我")
        check("首次点赞生效", added is True)
        removed = db.toggle_reaction("comment", "c2", "🎉", "ip-shared", display_name="我")
        check("同一身份再点是取消", removed is False)
        check("取消后计数归零",
              db.reaction_counts("comment", ["c2"]).get("c2", {}).get("🎉", 0) == 0,
              str(db.reaction_counts("comment", ["c2"])))


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
        db.toggle_reaction("comment", g, "👍", "v1", display_name="点赞者")
        db.toggle_reaction("comment", h, "👍", "v1", display_name="点赞者")
        db.soft_delete_comment(g)
        removed = db.delete_comment_only(h)
        check("两个 id 都被删除", sorted(removed) == sorted([h, g]), str(removed))
        check("墓碑上的点赞也没了",
              db.reaction_counts("comment", [g]).get(g, {}) == {},
              str(db.reaction_counts("comment", [g])))


def test_purge_childless_tombstones() -> None:
    """The startup sweep, which heals databases written by an older release.

    Deleting the last reply clears the tombstone above it, so current code never
    *creates* a childless tombstone. The sweep is not for that case — it is for
    the state a database can arrive in from elsewhere: rows written before that
    walk-up existed, or a backup restored from that era. Such a row renders as
    「该评论已被删除」 with nothing under it, which a reader cannot tell apart
    from a live bug.

    The rows are therefore planted directly, since no API call can produce them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "sweep2.db"))
        page = "/legacy"

        def plant(author: str, parent_id: str | None, deleted: bool = False) -> str:
            comment_id = _make_comment(db, page, author, parent_id=parent_id)
            if deleted:
                db.soft_delete_comment(comment_id)
            return comment_id

        print("--- 无回复的墓碑会被清理（模拟旧版本留下的行）")
        lonely = plant("孤独的墓碑", None, deleted=True)
        healthy = plant("正常的评论", None)
        check("清理前它确实在库里", db.get_comment(lonely) is not None)
        removed = db.purge_childless_tombstones()
        check("被清理的正是那一条", removed == [lonely], str(removed))
        check("库里不再有它", db.get_comment(lonely) is None)
        check("正常评论未受影响", db.get_comment(healthy) is not None)

        print("--- 带有回复的墓碑必须保留")
        holder = plant("还挂着回复的墓碑", None, deleted=True)
        child = plant("回复", holder)
        removed = db.purge_childless_tombstones()
        check("没有清理任何东西", removed == [], str(removed))
        check("墓碑仍在", db.get_comment(holder) is not None)
        check("回复仍在", db.get_comment(child) is not None)

        print("--- 墓碑链自底向上一起清掉")
        top = plant("顶层墓碑", None, deleted=True)
        mid = plant("中层墓碑", top, deleted=True)
        bottom = plant("底层墓碑", mid, deleted=True)
        removed = db.purge_childless_tombstones()
        check("三层一次清完", sorted(removed) == sorted([bottom, mid, top]), str(removed))
        check("一条都不剩", all(db.get_comment(i) is None for i in (top, mid, bottom)))

        print("--- 清理时连同点赞一起丢弃")
        liked = plant("有人点赞的墓碑", None, deleted=True)
        db.toggle_reaction("comment", liked, "👍", "v1", display_name="点赞者")
        check("点赞先写在上面",
              db.reaction_counts("comment", [liked]).get(liked, {}).get("👍") == 1)
        db.purge_childless_tombstones()
        check("墓碑没了", db.get_comment(liked) is None)
        check("点赞也没了",
              db.reaction_counts("comment", [liked]).get(liked, {}) == {},
              str(db.reaction_counts("comment", [liked])))

        print("--- 幂等：再跑一次什么都不做")
        check("第二次返回空", db.purge_childless_tombstones() == [])
        check("第三次也返回空", db.purge_childless_tombstones() == [])
        # The page keeps exactly the three rows that were never childless: the
        # live comment, and the tombstone that still holds a reply.
        check("只剩两行活着的评论加一个仍在用的墓碑",
              db.count_comments(page)["total"] == 3,
              str(db.count_comments(page)))
        check("留下来的正是该留的",
              db.get_comment(healthy) is not None
              and db.get_comment(holder) is not None
              and db.get_comment(child) is not None,
              "healthy/holder/child")

    print("--- 清扫已接到服务启动流程上")
    # The sweep only heals an existing deployment if it actually runs. A test
    # that calls it by hand would keep passing after someone removed the call.
    main_py = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    startup = main_py.split('@app.on_event("startup")', 1)[1].split("\n@app.", 1)[0]
    check("启动时调用清扫", "purge_childless_tombstones()" in startup,
          startup.strip()[:120])


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
        db.toggle_reaction("comment", root, "👍", "v1", display_name="赞")
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
    """The placeholder is a label, not somebody's chosen name."""
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

        print("--- 占位名不会被当成某人的昵称")
        # The relabelled row now carries the placeholder, and a name list built
        # from it would claim somebody is called 「匿名用户」.
        with db.connect() as c:
            db.toggle_reaction("comment", "c1", "🎉", "v1", display_name="匿名用户")
        names = db.reaction_actors("comment", ["c1"]).get("c1", {})
        # Reaction rows keep whatever name they were written with — that is the
        # display layer's decision, not the store's. What must not happen is the
        # placeholder being *learned* and then attributed to other rows, which is
        # why nothing but the row records a name now.
        check("新行仍写明写入时的名字", names.get("🎉") == ["匿名用户"], str(names))
        check("未被写入行的旧行仍无名", "👍" not in names, str(names))

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
    test_upgrade_of_an_older_database()
    print()
    test_anonymous_name_is_not_a_nickname()
    print()
    test_tombstone_sweep()
    print()
    test_purge_childless_tombstones()
    print()
    test_purge_of_unknown_id()

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
