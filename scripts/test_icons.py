"""Provenance test for the icons inlined into ``comment.js``.

The frontend carries a copy of four Material Design Icons instead of loading
them at runtime. That keeps the widget dependency-free — no icon font, no extra
request, and a `currentColor` path that follows the palette — but a copy can go
stale. This test re-reads the installed theme and fails when the copies drift,
so an upgrade that redraws an icon is caught rather than silently ignored.

It skips (rather than fails) when Material is absent, because the widget itself
does not require the theme.

Run directly (``python scripts/test_icons.py``) or with pytest.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "mkdocs_comment_plugin" / "assets" / "comment.js"

# Local name in `ICON_PATHS` -> file under Material's `.icons/material/`.
ICONS = {
    "account": "account.svg",
    "emoticon": "emoticon-outline.svg",
    "views": "eye-outline.svg",
    "comments": "comment-text-outline.svg",
    "markdown": "language-markdown.svg",
}


def icons_dir() -> Path | None:
    """Material's icon directory, or ``None`` when the theme is not installed."""
    spec = importlib.util.find_spec("material")
    if spec is None or not spec.submodule_search_locations:
        return None
    for base in spec.submodule_search_locations:
        candidate = Path(base) / "templates" / ".icons" / "material"
        if candidate.is_dir():
            return candidate
    return None


def _normalise(path: str) -> str:
    """Kept for callers that want the raw path; comparison uses `_tokens`."""
    return path.strip()


# SVG path data uses whitespace as a *separator*, so it must not be stripped
# before comparing: `c5 0 9.27` and `c5 09.27` are different paths, and the
# second renders as the wrong glyph. An earlier version of this test removed
# all whitespace, which made those two compare equal and let a corrupted icon
# through. Tokenising keeps the distinction.
_PATH_TOKEN = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
)


def _tokens(path: str) -> list[str]:
    return _PATH_TOKEN.findall(path)


def js_paths() -> dict[str, str]:
    """The `ICON_PATHS` entries, rebuilt from the JS string literals.

    Entries are single literals, but the `+` join is still handled so a future
    re-wrap is read correctly rather than silently truncated.
    """
    source = JS.read_text(encoding="utf-8")
    block = re.search(r"var ICON_PATHS = \{(.*?)\n  \};", source, re.S)
    if not block:
        raise AssertionError("comment.js 中找不到 ICON_PATHS")

    found: dict[str, str] = {}
    for match in re.finditer(r"(\w+):\s*((?:\s*\"(?:[^\"\\]|\\.)*\"\s*\+?)+)", block.group(1)):
        chunks = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(2))
        found[match.group(1)] = "".join(chunks).replace("\\\"", '"')
    return found


def svg_path(path: Path) -> str:
    """The `d` attribute of a single-path icon file."""
    markup = path.read_text(encoding="utf-8")
    match = re.search(r'<path d="([^"]+)"', markup)
    if not match:
        raise AssertionError(f"{path.name} 中没有 <path d=...>")
    return match.group(1)


def run() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name}{(': ' + detail) if detail else ''}")

    print("--- 内联图标出处")
    inline = js_paths()
    for name in ICONS:
        check(f"ICON_PATHS 含 {name}", name in inline)

    directory = icons_dir()
    if directory is None:
        print("  [SKIP] 未安装 Material 主题，跳过来源比对")
    else:
        print(f"  主题图标目录：{directory}")
        for name, filename in ICONS.items():
            source = directory / filename
            if not source.is_file():
                check(f"{filename} 存在", False, "主题中找不到该图标")
                continue
            check(
                f"{name} 与 {filename} 一致",
                _tokens(inline.get(name, "")) == _tokens(svg_path(source)),
                f"已过期，请从 {source} 更新 comment.js",
            )

    print("--- 不使用 emoji 作为界面图标")
    source = JS.read_text(encoding="utf-8")
    for glyph, label in (("\U0001f441", "👁 浏览"), ("\U0001f4ac", "💬 评论")):
        check(f"未使用 {label}", glyph not in source)

    print("--- 匿名头像用主题图标而非字母")
    # A lettered avatar has to invent an initial for a name that has none, and
    # hashing the placeholder for a colour implies an identity it does not
    # carry. The silhouette is the only cue that says "no name".
    avatar = re.search(r"function avatarHtml\(([^)]*)\)\s*\{(.*?)\n  \}", source, re.S)
    check("能定位 avatarHtml", avatar is not None)
    if avatar:
        check("接收匿名标记", "anonymous" in avatar.group(1), avatar.group(1))
        body = avatar.group(2)
        branch = re.search(r"if \(anonymous\) \{(.*?)\n    \}", body, re.S)
        check("有匿名分支", branch is not None)
        if branch:
            check("匿名分支用 icon(\"account\")", 'icon("account")' in branch.group(1))
            check("匿名分支不输出字母", "avatarInitial" not in branch.group(1))
            check("匿名头像带专用 class", "md-comment__avatar--anonymous" in branch.group(1))
    check("avatarInitial 不再接收匿名参数", "avatarInitial(name, anonymous)" not in source)
    check("匿名配色不再参与哈希", "name === CFG.anonymousName" not in source)

    # Guards the guard: an earlier version of this test stripped whitespace
    # before comparing, which made a corrupted path compare equal to the real
    # one. If the tokeniser ever regresses, these must fail.
    print("--- 检查器自检")
    check("能区分 `c5 0 9.27` 与 `c5 09.27`", _tokens("c5 0 9.27") != _tokens("c5 09.27"))
    check("能区分 `7 9.5 1.5` 与 `7 9.51.5`", _tokens("7 9.5 1.5") != _tokens("7 9.51.5"))

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


def test_inlined_icons_match_theme() -> None:
    directory = icons_dir()
    if directory is None:
        return
    inline = js_paths()
    for name, filename in ICONS.items():
        assert _tokens(inline[name]) == _tokens(svg_path(directory / filename)), (
            f"{name} 与主题的 {filename} 不一致"
        )


def test_tokeniser_distinguishes_merged_numbers() -> None:
    """The regression that shipped: a lost separator merged two numbers.

    Guards the guard — if `_tokens` ever collapses whitespace again, this fails.
    """
    assert _tokens("c5 0 9.27") != _tokens("c5 09.27")
    assert _tokens("7 9.5 1.5") != _tokens("7 9.51.5")


def test_stats_do_not_use_emoji() -> None:
    source = JS.read_text(encoding="utf-8")
    assert "\U0001f441" not in source
    assert "\U0001f4ac" not in source


if __name__ == "__main__":
    sys.exit(run())
