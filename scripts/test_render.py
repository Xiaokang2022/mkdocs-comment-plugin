"""Rendering and sanitization tests for the comment backend.

Run directly (``python scripts/test_render.py``) or with pytest
(``pytest scripts/test_render.py``).

The suite pins down three behaviours that are easy to break:

* Markdown-authored text must survive with exactly **one** layer of escaping.
  Running ``bleach.linkify`` after rendering double-escaped entities, which is
  why autolinking now happens inside the Markdown pass.
* Rendering must **agree with the built site**. The extension set is
  configuration, so the parity section asserts the output shape of every
  construct a typical ``mkdocs.yml`` enables — including the classes the theme
  styles, which sanitization would otherwise strip.
* Every piece of untrusted markup must be removed by ``bleach.clean``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from markdown_render import plain_text, render_markdown  # noqa: E402

# --------------------------------------------------------------------------- #
# Markdown features
# --------------------------------------------------------------------------- #
RENDERS = [
    # Syntax highlighting wraps the string token, so the quotes are checked
    # rather than the neighbouring punctuation.
    ("fenced code keeps quotes", '```python\nprint("hello")\n```', '&quot;hello&quot;'),
    ("inline code keeps quotes", '`return "x";`', 'return "x";'),
    ("inline code escapes <", "`a < b`", "a &lt; b"),
    ("bold", "**bold**", "<strong>bold</strong>"),
    ("italic", "*italic*", "<em>italic</em>"),
    # Headings get the same slug the site generates, so `# Title` is linkable.
    ("heading", "# Title", '<h1 id="title">Title</h1>'),
    ("blockquote", "> quote", "<blockquote>"),
    ("ordered list", "1. one\n2. two", "<ol>"),
    ("unordered list", "- one\n- two", "<ul>"),
    ("table", "| a | b |\n| --- | --- |\n| 1 | 2 |", "<th>a</th>"),
    ("footnote", "text[^1]\n\n[^1]: note", "footnote"),
    ("definition list", "term\n:   def", "<dl>"),
    ("attribution quoting", "> quoted", "quoted"),
    ("chinese text", "他说「你好」", "他说「你好」"),
    ("emoji preserved", "nice 👍", "nice 👍"),
    ("ampersand escaped once", "Tom & Jerry", "Tom &amp; Jerry"),
    ("literal entity preserved", "&copy; 2026", "&copy;"),
]

# --------------------------------------------------------------------------- #
# Parity with `mkdocs.yml`: every construct the site renders must render here
# the same way. Each case pins the *shape* of the output, because that shape is
# what the theme's stylesheet hooks onto — a stripped class is a broken look.
# --------------------------------------------------------------------------- #
PARITY = [
    (
        "superfences wraps the code block",
        "```python\nx = 1\n```",
        '<div class="highlight">',
        "<code>",
        'class="n"',
    ),
    (
        "highlight knows the language",
        "```python\nx = 1\n```",
        '<span class="n">x</span>',
    ),
    (
        "details and summary survive",
        '??? note "Title"\n\n    body\n',
        '<details class="note">',
        "<summary>Title</summary>",
        "<p>body</p>",
    ),
    (
        "admonition keeps its title class",
        '!!! warning "Careful"\n\n    watch out\n',
        '<div class="admonition warning">',
        '<p class="admonition-title">Careful</p>',
    ),
    (
        "tabbed uses Material's alternate layout",
        '=== "A"\n\n    aaa\n\n=== "B"\n\n    bbb\n',
        '<div class="tabbed-set tabbed-alternate" data-tabs="1:2">',
        '<div class="tabbed-labels">',
        '<label for="__tabbed_1_1">A</label>',
        'type="radio"',
    ),
    (
        "footnote keeps its reference classes",
        "text[^1]\n\n[^1]: note",
        '<a class="footnote-ref" href="#fn:1">',
        '<a class="footnote-backref"',
    ),
    (
        "md_in_html renders markdown inside html",
        '<div class="x" markdown="1">\n\n**bold**\n\n</div>',
        '<div class="x">',
        "<strong>bold</strong>",
    ),
    (
        # A non-ASCII heading slugs to nothing, so the extension falls back to a
        # running counter. The built site does exactly the same (`#_2`, `#_3` …
        # in `demo/site/guide/index.html`), which is the point of checking it:
        # an in-comment `[跳过去](#_1)` link has to land where the site's does.
        "non-ascii heading id matches the site",
        "## 小节",
        '<h2 id="_1">',
    ),
    (
        "ascii heading keeps a readable id",
        "## Install",
        '<h2 id="install">',
    ),
]

# `nl2br` and `sane_lists` used to be enabled here but are absent from a typical
# `mkdocs.yml`. Both change visible output, and a preview that disagrees with the
# site is precisely what this section exists to prevent.
NOT_ENABLED = [
    ("strikethrough stays literal", "~~gone~~", "~~gone~~"),
    ("one newline stays one newline", "a\nb", "<p>a\nb</p>"),
]

# Output that used to be produced and must not be any more. These are the
# visible mismatches that prompted making the extension set configurable: the
# backend added `<br>` at every newline while the site did not, and an odd list
# numbered differently from the page around it.
NO_LONGER_EMITTED = [
    ("no <br> is injected at a newline", "one\ntwo", ["<br"]),
    ("~~ still does not strike through", "~~x~~", ["<del>", "<s>"]),
]

# --------------------------------------------------------------------------- #
# Autolinking and outbound-link hardening
# --------------------------------------------------------------------------- #
LINKS = [
    (
        "markdown link hardened",
        "[MkDocs](https://www.mkdocs.org)",
        'rel="nofollow noopener noreferrer"',
    ),
    ("markdown link target", "[MkDocs](https://www.mkdocs.org)", 'target="_blank"'),
    ("bare url linked", "see https://example.com/x", '<a href="https://example.com/x"'),
    ("bare url hardened", "see https://example.com/x", 'target="_blank"'),
    ("bare url with query kept", "https://e.com/a?b=1", "a?b=1"),
    (
        "url on markdown line",
        "website: https://example.com",
        '<a href="https://example.com"',
    ),
    ("mailto allowed", "[mail](mailto:a@b.com)", 'href="mailto:a@b.com"'),
    ("relative link left alone", "[rel](/guide/)", 'href="/guide/"'),
    (
        "url inside backticks untouched",
        "`https://example.com`",
        "<code>https://example.com</code>",
    ),
    ("url in code fence untouched", "```\nhttps://example.com\n```", "<code>"),
]

# --------------------------------------------------------------------------- #
# Sanitization: nothing below may appear in the output
# --------------------------------------------------------------------------- #
XSS = [
    ("script tag", "<script>alert('xss')</script>", ["<script"]),
    ("img onerror", '<img src=x onerror="alert(1)">', ["onerror"]),
    ("svg onload", "<svg onload=alert(1)></svg>", ["onload", "<svg"]),
    ("iframe", '<iframe src="https://evil.com"></iframe>', ["<iframe"]),
    ("javascript url", "[click](javascript:alert(1))", ["javascript:"]),
    ("data url", "[click](data:text/html,<script>x</script>)", ["data:"]),
    ("style attr", '<p style="position:fixed">x</p>', ["style="]),
    ("onclick attr", '<a href="#" onclick="evil()">x</a>', ["onclick"]),
    ("form injection", "<form action=/x><input name=a></form>", ["<form", "action="]),
    (
        "input cannot carry an action",
        '<input type=submit formaction="https://evil">',
        ["formaction"],
    ),
    ("input cannot steal focus", "<input type=text autofocus>", ["autofocus"]),
    ("input cannot prefill a value", '<input type=text value="x">', ["value="]),
    ("object tag", "<object data=x></object>", ["<object"]),
    ("base tag", "<base href=https://evil.com>", ["<base"]),
    ("meta refresh", '<meta http-equiv="refresh" content="0">', ["<meta"]),
    ("expression", '<div style="width:expression(alert(1))">x</div>', ["expression"]),
]

# --------------------------------------------------------------------------- #
# plain_text(): strips HTML markup only, not Markdown syntax
# --------------------------------------------------------------------------- #
PLAIN = [
    ("strips html tags", "<b>hi</b>", "hi"),
    ("strips attributes", '<a href="x" onclick="y">link</a>', "link"),
    ("keeps script text", "ok<script>alert(1)</script>", "alert(1)"),
    ("keeps markdown syntax", "**bold**", "**bold**"),
    ("collapses whitespace", "a\n\n\nb", "a b"),
    ("keeps chinese", "你好 世界", "你好 世界"),
]


def _run(label: str, items, check) -> tuple[int, int]:
    passed = failed = 0
    print(f"--- {label}")
    for name, source, *expected in items:
        output = check(source)
        ok = expected[0](output) if callable(expected[0]) else all(e in output for e in expected)
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}\n         in : {source!r}\n         out: {output!r}")
    return passed, failed


def main() -> int:
    passed = failed = 0

    p, f = _run("Markdown 渲染", RENDERS, render_markdown)
    passed, failed = passed + p, failed + f

    p, f = _run("与 mkdocs.yml 同源对齐", PARITY, render_markdown)
    passed, failed = passed + p, failed + f

    p, f = _run("未启用的语法保持字面量", NOT_ENABLED, render_markdown)
    passed, failed = passed + p, failed + f

    # Inverted like the XSS section: the needles must be ABSENT.
    print("--- 不再产生旧输出")
    for name, source, needles in NO_LONGER_EMITTED:
        output = render_markdown(source)
        hits = [n for n in needles if n in output]
        if hits:
            failed += 1
            print(f"  [FAIL] {name}: 仍然出现 {hits} -> {output!r}")
        else:
            passed += 1
            print(f"  [PASS] {name}")

    p, f = _run("链接处理", LINKS, render_markdown)
    passed, failed = passed + p, failed + f

    # XSS cases are inverted: the needles must be ABSENT.
    print("--- XSS 净化")
    for name, source, needles in XSS:
        output = render_markdown(source)
        hits = [n for n in needles if n in output]
        if hits:
            failed += 1
            print(f"  [FAIL] {name}: 未清除 {hits} -> {output!r}")
        else:
            passed += 1
            print(f"  [PASS] {name}")

    p, f = _run("plain_text()", PLAIN, plain_text)
    passed, failed = passed + p, failed + f

    # No path may ever double-escape an entity.
    print("--- 转义层数")
    double_escaped = [
        source
        for _, source, *_ in RENDERS + NOT_ENABLED + LINKS
        if "&amp;quot;" in render_markdown(source) or "&amp;amp;" in render_markdown(source)
    ]
    if double_escaped:
        failed += 1
        print(f"  [FAIL] 存在重复转义: {double_escaped}")
    else:
        passed += 1
        print("  [PASS] 无重复转义")

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


def test_render_markdown() -> None:
    for _, source, *expected in RENDERS:
        output = render_markdown(source)
        for needle in expected:
            assert needle in output, f"{needle!r} missing from {output!r}"


def test_parity_with_mkdocs() -> None:
    for name, source, *needles in PARITY:
        output = render_markdown(source)
        for needle in needles:
            assert needle in output, f"{name}: {needle!r} missing from {output!r}"


def test_disabled_extensions_stay_literal() -> None:
    for name, source, *needles in NOT_ENABLED:
        output = render_markdown(source)
        for needle in needles:
            assert needle in output, f"{name}: {needle!r} missing from {output!r}"


def test_removed_output_stays_removed() -> None:
    for name, source, needles in NO_LONGER_EMITTED:
        output = render_markdown(source)
        for needle in needles:
            assert needle not in output, f"{name}: {needle!r} came back in {output!r}"


def test_sanitization() -> None:
    for name, source, needles in XSS:
        output = render_markdown(source)
        for needle in needles:
            assert needle not in output, f"{name}: {needle!r} survived in {output!r}"


def test_no_double_escaping() -> None:
    for source in ('```\nprint("x")\n```', "Tom & Jerry"):
        output = render_markdown(source)
        assert "&amp;quot;" not in output
        assert "&amp;amp;" not in output


if __name__ == "__main__":
    sys.exit(main())
