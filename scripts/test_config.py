"""Configuration contract tests for the MkDocs plugin.

Run directly (``python scripts/test_config.py``) or with pytest
(``pytest scripts/test_config.py``).

The plugin, the generated ``config.js`` and the browser widget form a three
hop contract::

    mkdocs.yml -> plugin.config_scheme -> _js_config() -> CFG in comment.js

A break in any hop fails silently in the browser: a typo in ``plugin.py`` just
drops the option, and a key the frontend never declares is ignored. These tests
pin the contract down without needing a browser:

* Every ``Choice`` option rejects values outside its allowed set.
* Every option (bar the deliberate exclusions) reaches ``config.js`` under the
  camelCase name the frontend expects.
* Every key the plugin emits is declared in the frontend ``DEFAULTS``, so the
  widget can never receive a key it does not understand.
* Every key in ``DEFAULTS`` is actually *read* by ``comment.js`` — no dead
  configuration that looks tunable but does nothing.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mkdocs_comment_plugin"))

from plugin import CommentPlugin  # noqa: E402

JS_PATH = ROOT / "mkdocs_comment_plugin" / "assets" / "comment.js"

# `enabled` only gates asset injection, `extra_config` is merged verbatim
# rather than mapped, `labels` is merged key-by-key against the built-in
# defaults, and the two `meta_*` options are consumed by the Python side (which
# pages get a host element) and never reach the browser. None of them appears as
# a flat key in config.js.
NOT_MAPPED = {"enabled", "extra_config", "labels", "meta_key", "meta_default"}


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(word.capitalize() for word in rest)


def load(**overrides) -> CommentPlugin:
    """Validate a config the way MkDocs does, failing loudly on errors."""
    plugin = CommentPlugin()
    errors, _ = plugin.load_config(dict(overrides))
    if errors:
        raise AssertionError(f"配置校验失败: {errors}")
    return plugin


def payload(**overrides) -> dict:
    """The JSON the plugin writes into ``config.js``."""
    return load(**overrides)._js_config()


def frontend_defaults() -> set[str]:
    """Keys declared by the widget's ``DEFAULTS`` object in comment.js."""
    source = JS_PATH.read_text(encoding="utf-8")
    block = re.search(r"var DEFAULTS = \{(.*?)\n  \};", source, re.S)
    if not block:
        raise AssertionError("comment.js 中找不到 DEFAULTS")
    return set(re.findall(r"^\s{4}(\w+):", block.group(1), re.M))


def emitted_keys() -> set[str]:
    return set(payload()) - {"labels"}


# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #
CHOICES = [
    ("avatar_style", "initial", "none"),
    ("avatar_shape", "circle", "square"),
    ("density", "comfortable", "compact"),
    ("sort_order", "newest", "oldest"),
    ("time_style", "relative", "absolute"),
]

# Spot-checking the renames whose camelCase form is not obvious.
MAPPINGS = [
    ("accent_color", "accentColor"),
    ("avatar_style", "avatarStyle"),
    ("avatar_shape", "avatarShape"),
    ("editor_rows", "editorRows"),
    ("sort_order", "sortOrder"),
    ("time_style", "timeStyle"),
    ("remember_author", "rememberAuthor"),
    ("reply_quote", "replyQuote"),
    ("page_selector", "pageSelector"),
    ("emoji_picker", "emojiPicker"),
    ("show_page_reactions", "showPageReactions"),
]

APPEARANCE = {
    "accent_color": "#00a884",
    "avatar_style": "none",
    "density": "compact",
    "editor_rows": 8,
    "sort_order": "oldest",
    "time_style": "absolute",
    "remember_author": False,
    "reply_quote": False,
}

EXPECTED_APPEARANCE = {
    "accentColor": "#00a884",
    "avatarStyle": "none",
    "density": "compact",
    "editorRows": 8,
    "sortOrder": "oldest",
    "timeStyle": "absolute",
    "rememberAuthor": False,
    "replyQuote": False,
}


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

    print("--- Choice 枚举校验")
    for option, _, second in CHOICES:
        check(f"{option} 接受 {second!r}", load(**{option: second}).config[option] == second)

        invalid, _ = CommentPlugin().load_config({option: "definitely-not-valid"})
        check(f"{option} 拒绝非法值", bool(invalid), f"errors={invalid}")

    print("--- 选项映射到 config.js")
    options = {name for name, _ in CommentPlugin.config_scheme}
    missing = sorted({camel(name) for name in options - NOT_MAPPED} - emitted_keys())
    check("所有选项都已输出", not missing, f"缺失 {missing}")

    emitted = set(payload())
    for option, key in MAPPINGS:
        check(f"{option} -> {key}", key in emitted, f"{key} 未出现在 payload 中")

    print("--- 前端契约")
    declared = frontend_defaults()
    unknown = sorted(emitted_keys() - declared)
    check("插件输出的键都被前端声明", not unknown, f"前端未声明 {unknown}")

    source = JS_PATH.read_text(encoding="utf-8")
    unread = sorted(key for key in declared if not re.search(rf"CFG\.{key}\b", source))
    check("DEFAULTS 中无死配置", not unread, f"从未被读取 {unread}")

    print("--- 取值传递")
    result = payload(**APPEARANCE)
    for key, value in EXPECTED_APPEARANCE.items():
        check(f"{key} = {value!r}", result.get(key) == value, f"实际 {result.get(key)!r}")

    print("--- 默认值")
    base = payload()
    for key in EXPECTED_APPEARANCE:
        check(f"{key} 有默认值", key in base)
    check("accentColor 默认为空（继承主题）", base["accentColor"] == "")
    check("density 默认为 comfortable", base["density"] == "comfortable")
    check("timeStyle 默认为 relative", base["timeStyle"] == "relative")
    check("config.js 内容可序列化为 JSON", bool(json.dumps(base, ensure_ascii=False)))

    print("--- extra_config 覆盖")
    override = payload(extra_config={"density": "compact", "custom": 1})
    check("extra_config 可覆盖已声明键", override["density"] == "compact")
    check("extra_config 可注入自定义键", override.get("custom") == 1)

    print("--- 哪些页面注入评论区（页面元数据）")
    gate_checks(plugin=load(), check=check)

    print("--- 快捷表情（三处副本必须一致）")
    reaction_checks(check)

    print("--- 挂载开关：只有宿主元素能决定是否挂载")
    mount_guard_checks(check)

    print("--- 「我」标签：按地址判定，不看本地令牌")
    identity_badge_checks(check)

    print("--- 配色两级：选中色与高亮色各就各位")
    palette_checks(check)

    print("--- 提示气泡：与主题的「已复制」同位置、同外观")
    toast_checks(check)

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


SETTINGS_PATH = ROOT / "backend" / "settings.py"


def js_reactions() -> list[str]:
    """The widget's built-in quick-reaction list, read out of comment.js."""
    source = JS_PATH.read_text(encoding="utf-8")
    block = re.search(r"\n    reactions: \[(.*?)\],", source, re.S)
    if not block:
        raise AssertionError("comment.js 的 DEFAULTS 里找不到 reactions")
    return re.findall(r'"([^"]*)"', block.group(1))


def backend_reactions() -> list[str]:
    """`settings.DEFAULT_REACTIONS`, read from source.

    Read as text rather than imported so this suite keeps working without the
    backend's dependencies installed — it is a contract test for the plugin.
    """
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    block = re.search(r"^DEFAULT_REACTIONS: List\[str\] = \[(.*?)\]", source, re.M)
    if not block:
        raise AssertionError("settings.py 里找不到 DEFAULT_REACTIONS")
    return re.findall(r'"([^"]*)"', block.group(1))


def reaction_checks(check) -> None:
    """The quick-reaction list exists in three places; they must not drift.

    The plugin ships a default, the browser ships the same default as its
    fallback for "config.js never loaded", and the backend ships the allow-list
    it validates against. A reaction the server does not allow is a button that
    always fails, so disagreement here is a bug the user only sees after
    clicking.
    """
    frontend = js_reactions()
    backend = backend_reactions()
    emitted = payload()["reactions"]

    check("眼睛是内置快捷表情之一", "👀" in emitted, str(emitted))
    check("插件默认表情与前端 DEFAULTS 一致", emitted == frontend,
          f"plugin={emitted} frontend={frontend}")
    check("插件默认表情与后端白名单一致", emitted == backend,
          f"plugin={emitted} backend={backend}")
    check("快捷表情无重复", len(set(emitted)) == len(emitted), str(emitted))
    check("每个表情都是单字素（面板里不会裂开）",
          all(1 <= len(e) <= 4 for e in emitted), str(emitted))


def mount_guard_checks(check) -> None:
    """`page_selector` must not be able to switch a page's comments on.

    A CSS selector matches on every page it matches, so using one as a fallback
    mount point is indistinguishable from "mount everywhere". That is exactly
    how it behaved, and the symptom was that the per-page front matter looked
    inert: pages without `comments: true` still grew a comment section. The
    widget must therefore give up when there is no host element, *before* it
    ever reads `pageSelector`.
    """
    source = JS_PATH.read_text(encoding="utf-8")
    body = source.split("function mount()", 1)[1]
    body = body[: body.index("\n  }")]

    guard = re.search(r"if \(!host\)\s*\{\s*return;", body)
    selector_at = body.find("CFG.pageSelector")

    check("mount 里以宿主元素为准", ".md-comment-host" in body, body[:80])
    check("没有宿主元素就直接返回", guard is not None, "缺少 if (!host) return")
    check(
        "pageSelector 在宿主判定之后才被读取",
        selector_at == -1 or (guard is not None and guard.end() < selector_at),
        f"guard@{guard.end() if guard else None} selector@{selector_at}",
    )
    check(
        "pageSelector 不再作为缺少宿主时的兜底",
        not re.search(r"host\s*\|\|", body),
        "仍存在 host || ... 形式的兜底挂载",
    )


def identity_badge_checks(check) -> None:
    """The「我」badge must follow the address rule, not a stored token.

    It used to be driven by the per-comment delete token kept in localStorage.
    That stopped describing reality the moment identity became the source
    address: a reader who cleared site data still owned their comments but lost
    the badge, while a reader who inherited a browser profile saw「我」on
    comments they never wrote — the same comment both over- and under-claimed.

    The server now answers the question in `is_mine`, computed by the one rule
    that also decides deletion, and the widget must read that field. A source
    check is the right shape here because a browser test cannot see the
    difference: both versions render a badge.
    """
    body = JS_PATH.read_text(encoding="utf-8").split(
        "function commentHtml(comment, replies)", 1
    )[1]
    body = body[: body.index("\n  }")]
    badge = body.index("md-comment__badge")
    # The condition is on the line above the markup it guards, so read both —
    # checking only the `meta +=` line would pass for any condition at all.
    condition = body[body.rindex("if (", 0, badge) : badge]

    check("「我」标签读服务端的 is_mine", "isMine" in condition, condition.strip())
    check("「我」标签不再自己看令牌", "tokens()" not in condition, condition.strip())

    backend = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    check(
        "后端按来源地址算出 is_mine",
        re.search(r"is_mine = owns_row\(row, visitor\)", backend) is not None,
    )
    check(
        "is_mine 不依赖 allow_delete（否则关掉删除就没了标签）",
        not re.search(r"is_mine\s*=.*allow_delete", backend),
    )
    check(
        "is_mine 不依赖昵称",
        not re.search(r"is_mine\s*=.*\bauthor\b", backend),
    )


CSS_PATH = ROOT / "mkdocs_comment_plugin" / "assets" / "comment.css"


def css_block(selector: str) -> str:
    """The declaration block of a top-level rule, matched by selector.

    Comments are stripped. They are prose about the rule — and prose that
    naturally quotes the very token names these tests ban ("it used to be
    `var(--mkc-danger)`") would otherwise make a check pass or fail on its own
    explanation rather than on the code.
    """
    source = CSS_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^\s*{re.escape(selector)}\s*\{{(.*?)\}}", source, re.S | re.M
    )
    if not match:
        raise AssertionError(f"comment.css 里找不到 {selector}")
    return re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)


def _rem(value: str | None) -> float | None:
    """`".8rem"` / `"0.8rem"` -> `0.8`, so the two spellings compare equal."""
    if not value:
        return None
    match = re.match(r"([0-9]*\.?[0-9]+)rem$", value.strip())
    return float(match.group(1)) if match else None


def theme_dialog_insets() -> dict[str, str] | None:
    """Read `.md-dialog`'s corner offsets out of the installed Material theme.

    Material's `.md-dialog` is the bubble it shows after you copy a code block
    ("已复制"), and the widget's toast deliberately sits in the same corner with
    the same inset. Copying a number from another project means it can go stale
    without anyone noticing, which is the same problem `test_icons.py` solves for
    the inlined icons — so this reads the installed theme and compares, rather
    than trusting the literal.

    Returns ``None`` when Material is not installed, since the widget itself does
    not depend on the theme.
    """
    spec = importlib.util.find_spec("material")
    if spec is None or not spec.submodule_search_locations:
        return None
    for base in spec.submodule_search_locations:
        sheets = Path(base) / "templates" / "assets" / "stylesheets"
        if not sheets.is_dir():
            continue
        for sheet in sheets.glob("main*.css"):
            text = sheet.read_text(encoding="utf-8", errors="replace")
            insets: dict[str, str] = {}
            # Every `.md-dialog{…}` rule, not just the first: the offsets live in
            # one rule and the logical-direction split in others. The value may
            # also be the last thing before `}`, with no trailing semicolon —
            # minified CSS drops it — so the separator must be optional.
            for block in re.findall(r"\.md-dialog\{(.*?)\}", text, re.S):
                for name, value in re.findall(r"(bottom|right|left):([^;}]+)", block):
                    insets.setdefault(name, value.strip())
            if insets:
                return insets
    return None


def toast_checks(check) -> None:
    """The status bubble must match Material's own, and must survive on <body>.

    Two things are being pinned here, and the second is the important one.

    *Placement*: `toast()` appends its node to `<body>`, so the bubble lands in
    the corner, outside the widget's own layout. Material puts its "已复制"
    confirmation there too, and two self-dismissing status messages drifting to
    different corners read as two unrelated systems.

    *Token scope*: because the node is on `<body>` and not inside `.md-comment`,
    every `--mkc-*` custom property is *undefined* on it. A `var()` with no
    fallback makes the whole declaration invalid at computed-value time, which
    happens silently — the first version of this rule lost both its border radius
    and its elevation that way, and the error variant lost its red background, so
    a failure looked exactly like a success. Hence the ban below: only root-level
    `--md-*` tokens and literal values are allowed here.
    """
    block = css_block(".md-comment__toast")
    declarations = " ".join(block.split())

    check(
        "提示框不再依赖 --mkc-* 变量（它挂在 body 上，那些变量根本不生效）",
        "--mkc-" not in declarations,
        [line.strip() for line in block.splitlines() if "--mkc-" in line],
    )
    check(
        "提示框只使用根级别的 --md-* 变量",
        all(token in ("--md-default-fg-color", "--md-default-bg-color", "--md-shadow-z3")
            for token in re.findall(r"var\((--md-[a-z0-9-]+)", declarations)),
        re.findall(r"var\((--md-[a-z0-9-]+)", declarations),
    )
    check("提示框自带圆角，不靠变量", "border-radius:" in declarations)
    check("提示框自带阴影，不靠变量", "box-shadow:" in declarations)
    check(
        "提示框宽度有上限，窄屏不会溢出",
        "max-width: calc(100vw - 1.6rem)" in declarations
        and "min-width: min(11.1rem, calc(100vw - 1.6rem))" in declarations,
        declarations,
    )
    # `inset-inline-end` so an RTL site mirrors it, exactly like the theme does
    # with `[dir=rtl] .md-dialog { left: … }`.
    check(
        "提示框用逻辑属性贴住行尾（RTL 会镜像到另一边）",
        "inset-inline-end: 0.8rem" in declarations and "\n  left:" not in block,
        declarations,
    )

    insets = theme_dialog_insets()
    if insets is None:
        print("  [SKIP] 未安装 Material 主题，跳过与主题 `.md-dialog` 的位置对照")
    else:
        # Compared as numbers, not as strings: the theme writes `.8rem` and the
        # widget writes `0.8rem`, which are the same length spelled two ways.
        # Pinning the string would fail on the next theme release for no reason.
        theme_bottom = _rem(insets.get("bottom"))
        check(
            f"与主题 `.md-dialog` 的下边距一致（主题为 {insets.get('bottom')}）",
            theme_bottom == 0.8,
            f"theme={insets} ours=0.8rem",
        )
        check(
            "提示框与主题同角（行尾对齐，不是居中）",
            "inset-inline-end: 0.8rem" in declarations and "left: 50%" not in declarations,
            declarations,
        )

    # The error variant is not allowed to reach for `--mkc-danger` either, and
    # its fill has to be a literal, because on <body> the variable is dead.
    error = css_block(".md-comment__toast--error")
    check("错误提示不依赖 --mkc-danger", "--mkc-danger" not in error, error)
    check(
        "错误提示是实心红底白字",
        "background: #d32f2f" in " ".join(error.split()) and "color: #ffffff" in error,
        error,
    )
    # A filled bubble and the widget's text-level danger colour need opposite
    # treatment under the dark palette: text must get lighter, a fill holding
    # white text must get darker. Sharing one token would get one of them wrong.
    slate = css_block('body[data-md-color-scheme="slate"] .md-comment__toast--error')
    check("深色配色下错误提示反而更深（白字才压得住）", "background: #b71c1c" in slate, slate)


def palette_checks(check) -> None:
    """The widget lights things up two ways, and they are not the same colour.

    *Selected* is a settled state (this pill is on, this button is the default)
    and wears the site's primary. *Highlighted* is a momentary response (this
    field has the caret, the pointer is over this pill) and wears the theme's
    link-hover colour, so a focused box reads as live rather than as branded.

    Collapsing the two back onto one variable would silently undo the visual
    distinction the widget was built around, and nothing else would notice —
    hence this test.
    """
    source = CSS_PATH.read_text(encoding="utf-8")

    check(
        "高亮色默认取主题的链接悬停色",
        re.search(
            r"--mkc-accent:\s*var\(--md-accent-fg-color,", source
        )
        is not None,
        "--mkc-accent 未回退到 --md-accent-fg-color",
    )
    check(
        "选中色默认取主题主色",
        re.search(r"--mkc-primary:\s*var\(--md-primary-fg-color,", source)
        is not None,
        "--mkc-primary 未回退到 --md-primary-fg-color",
    )

    focused = css_block(".md-comment__form:focus-within")
    check("输入框聚焦用高亮色", "var(--mkc-accent)" in focused, focused)
    check(
        "昵称框聚焦用高亮色",
        "var(--mkc-accent)"
        in css_block(".md-comment__identity:focus-within"),
    )
    check(
        "表情胶囊悬停的边框用高亮色",
        "var(--mkc-accent)"
        in css_block("button.md-comment__reaction:hover"),
    )
    check(
        "表情胶囊悬停的文字用高亮色",
        "var(--mkc-accent)"
        in css_block("button.md-comment__reaction:not(.is-active):hover"),
    )

    active = css_block(".md-comment__reaction.is-active")
    check("已选中的胶囊仍用主色", "var(--mkc-primary)" in active, active)

    # `accent_color` is the operator saying "this is my accent", so it must move
    # both tiers; leaving highlights on the theme colour would look like a bug.
    theme = JS_PATH.read_text(encoding="utf-8")
    body = theme.split("function applyTheme(root)", 1)[1]
    body = body[: body.index("\n  }")]
    check(
        "accent_color 同时覆盖选中色与高亮色",
        '"--mkc-primary", CFG.accentColor' in body
        and '"--mkc-accent", CFG.accentColor' in body,
        body,
    )


class FakePage:
    """Just enough of an MkDocs `Page` for the opt-in check."""

    def __init__(self, url: str = "", meta: dict | None = None) -> None:
        self.url = url
        self.meta = meta or {}


def gate_checks(plugin: CommentPlugin, check) -> None:
    """The per-page gate, which decides whether a page gets a host element.

    This is the switch that keeps a comment section off a page nobody asked to
    have one on, so both the default and the parsing of front-matter values are
    worth pinning down.
    """
    check("默认不注入（无元数据）", plugin._page_enabled(FakePage("guide/", {})) is False)
    check("默认不注入（无 meta 属性）", plugin._page_enabled(object()) is False)
    check("comments: true 注入", plugin._page_enabled(FakePage("guide/", {"comments": True})) is True)
    # Markdown's `meta` extension hands over strings for a bare word.
    check("字符串 true 也算数", plugin._page_enabled(FakePage("g/", {"comments": "true"})) is True)
    check("yes 也算数", plugin._page_enabled(FakePage("g/", {"comments": "yes"})) is True)
    check("1 也算数", plugin._page_enabled(FakePage("g/", {"comments": "1"})) is True)
    check("comments: false 不注入", plugin._page_enabled(FakePage("g/", {"comments": False})) is False)
    check("字符串 false 不注入", plugin._page_enabled(FakePage("g/", {"comments": "false"})) is False)
    check("空值不注入", plugin._page_enabled(FakePage("g/", {"comments": ""})) is False)
    # A typo should leave the section off, not break the build.
    check("无法识别的值不注入", plugin._page_enabled(FakePage("g/", {"comments": "也许"})) is False)

    open_by_default = load(meta_default=True)
    check("meta_default=true 时默认注入", open_by_default._page_enabled(FakePage("g/", {})) is True)
    check("显式关闭仍然生效",
          open_by_default._page_enabled(FakePage("g/", {"comments": "false"})) is False)

    custom = load(meta_key="discuss")
    check("可以换一个字段名", custom._page_enabled(FakePage("g/", {"discuss": True})) is True)
    check("换了字段名后 comments 不再生效",
          custom._page_enabled(FakePage("g/", {"comments": True})) is False)

    print("--- 页面键（要与前端/历史数据一致）")
    # These are the keys the widget has always sent, so existing comments must
    # keep resolving to the same page.
    for url, expected in (
        ("", "/"),
        (".", "/"),
        ("/", "/"),
        ("guide/", "/guide"),
        ("guide/index.html", "/guide"),
        ("a/b/", "/a/b"),
        ("a/b/index.html", "/a/b"),
        ("guide", "/guide"),
        ("guide/#anchor", "/guide"),
        ("guide/?q=1", "/guide"),
    ):
        from plugin import _page_key

        check(f"{url!r} -> {expected}", _page_key(FakePage(url)) == expected,
              f"得到 {_page_key(FakePage(url))!r}")

    print("--- 宿主元素只在开启的页面上出现")
    html = plugin.on_page_content("<p>正文</p>", FakePage("guide/", {"comments": True}),
                                 {"site_url": "https://example.com/docs/"}, None)
    check("注入了宿主", "md-comment-host" in html, html)
    check("宿主带页面键", 'data-page="/guide"' in html, html)
    check("宿主带资源前缀", 'data-assets="/docs/assets/comment-plugin"' in html, html)
    check("原正文保留", html.startswith("<p>正文</p>"), html)

    off = load().on_page_content("<p>正文</p>", FakePage("guide/", {}), {}, None)
    check("未开启的页面原样返回", off == "<p>正文</p>", off)

    disabled = load(enabled=False).on_page_content(
        "<p>正文</p>", FakePage("guide/", {"comments": True}), {}, None
    )
    check("enabled=false 时一律不注入", disabled == "<p>正文</p>", disabled)


def test_choices_reject_invalid() -> None:
    for option, _, _ in CHOICES:
        errors, _ = CommentPlugin().load_config({option: "nope"})
        assert errors, f"{option} 未拒绝非法值"


def test_every_option_is_emitted() -> None:
    options = {name for name, _ in CommentPlugin.config_scheme}
    assert not {camel(name) for name in options - NOT_MAPPED} - emitted_keys()


def test_frontend_declares_every_emitted_key() -> None:
    assert not emitted_keys() - frontend_defaults()


def test_no_dead_configuration() -> None:
    source = JS_PATH.read_text(encoding="utf-8")
    unread = [key for key in frontend_defaults() if not re.search(rf"CFG\.{key}\b", source)]
    assert not unread, f"从未被读取: {unread}"


if __name__ == "__main__":
    sys.exit(run())
