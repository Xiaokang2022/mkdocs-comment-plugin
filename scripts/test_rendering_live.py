"""Verify the rendering fixes against a running backend.

Posts a comment containing a fenced code block with quotes and asserts the
stored HTML has exactly one layer of escaping, and that the constructs a
``mkdocs.yml`` enables survive the round trip through storage and rendering.
Cleans up after itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from api import Client  # noqa: E402

PAGE = "/__render_test__"
ADMIN_TOKEN = os.getenv("MKC_ADMIN_TOKEN", "")

client = Client()
passed = 0
failed = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {extra}")


def main() -> int:
    print(f"==> {client.api}")

    source = (
        '```python\nprint("hello")\n```\n\n'
        "`if (a < b) return \"x\";`\n\n"
        "Tom & Jerry 说：见 https://example.com/a?b=1\n\n"
        "# 标题\n\n> 引用\n\n"
        '??? note "折叠"\n\n    内容\n\n'
        "一个换行\n应当保留\n"
    )

    status, created = client.call("POST", "/comments", {"page": PAGE, "content": source})
    check("发表评论返回 201", status == 201, f"got {status}")
    if status != 201:
        return 1

    html = created["comment"]["content_html"]
    comment_id = created["comment"]["id"]
    print(f"  HTML: {html[:260]}...")
    # Inside <pre><code>, quotes are legitimately escaped once as &quot;; the
    # browser renders them back to ". The bug being guarded against is a
    # *second* escape, which surfaces as the literal text &quot;. The quotes are
    # checked on their own because syntax highlighting wraps the text in token
    # spans, so `print(...)` is no longer one contiguous run.
    check("代码块引号单层转义", "&quot;hello&quot;" in html)
    check("无重复转义 (&amp;quot;)", "&amp;quot;" not in html)
    check("无重复转义 (&amp;amp;)", "&amp;amp;" not in html)
    check("行内代码保留引号", 'return "x";' in html)
    check("与号单层转义", "Tom &amp; Jerry" in html)
    check("裸链接已生成并加固", 'rel="nofollow noopener noreferrer"' in html)
    check("标题已渲染并带 id", '<h1 id="_1">' in html or '<h1 id="' in html)
    check("引用已渲染", "<blockquote>" in html)
    # The two halves of the parity story that a round trip through the database
    # could still lose: code highlighting, and no `<br>` at every newline.
    check("代码块被高亮包裹", '<div class="highlight">' in html)
    check("折叠块得以保留", '<details class="note">' in html and "<summary>折叠</summary>" in html)
    check("换行未被替换成 <br>", "<br" not in html)

    client.call("DELETE", f"/comments/{comment_id}?hard=true")
    status, listing = client.call("GET", f"/comments?page={PAGE}&limit=1")
    if ADMIN_TOKEN and status == 200:
        check("测试数据已清理", listing["total"] == 0, f"剩余 {listing['total']}")
    elif status == 200 and listing["total"]:
        print(f"  [注意] 未设置 MKC_ADMIN_TOKEN，{listing['total']} 条测试评论可能残留")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    client.require_ready()
    sys.exit(main())
