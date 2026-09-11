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

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mkdocs_comment_plugin"))

from plugin import CommentPlugin  # noqa: E402

JS_PATH = ROOT / "mkdocs_comment_plugin" / "assets" / "comment.js"

# `enabled` only gates asset injection, `extra_config` is merged verbatim
# rather than mapped, and `labels` is merged key-by-key against the built-in
# defaults. None of the three appears as a flat key in config.js.
NOT_MAPPED = {"enabled", "extra_config", "labels"}


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

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


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
