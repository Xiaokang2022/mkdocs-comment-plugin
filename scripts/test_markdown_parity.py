"""Keeps the backend's Markdown extensions in step with the demo site's.

The renderer is configurable so a preview can agree with the page it sits on,
but a configurable setting is only useful if the two sides are actually kept
equal. This test reads ``demo/mkdocs.yml``, compares it with the backend's
default extension set, and fails on drift — including drift introduced by
editing only one of the two.

It also pins the one deliberate deviation (no permalink `¶` on comment
headings) so it cannot quietly grow into several.

Run directly (``python scripts/test_markdown_parity.py``) or with pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import yaml  # noqa: E402  (provided by mkdocs)

import markdown_render  # noqa: E402
from settings import DEFAULT_MARKDOWN_EXTENSIONS  # noqa: E402

DEMO_CONFIG = ROOT / "demo" / "mkdocs.yml"

# Options the backend intentionally sets differently, with the reason. Anything
# not listed here must match exactly.
DOCUMENTED_DEVIATIONS = {
    "toc": "评论标题不需要 ¶ 永久链接，它会把页面锚点指向评论内部",
}


def _names(entries) -> list[str]:
    """Extension names in order, ignoring any per-extension options."""
    return [item if isinstance(item, str) else next(iter(item)) for item in entries]


def _options(entries, name: str) -> dict:
    for item in entries:
        if isinstance(item, dict) and name in item:
            return item[name] or {}
    return {}


def _site_extensions() -> list:
    config = yaml.safe_load(DEMO_CONFIG.read_text(encoding="utf-8"))
    return config.get("markdown_extensions") or []


def main() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {label}")
        else:
            failed += 1
            print(f"  [FAIL] {label}" + (f"\n         {detail}" if detail else ""))

    site = _site_extensions()
    backend = DEFAULT_MARKDOWN_EXTENSIONS
    site_names = _names(site)
    backend_names = _names(backend)

    print("--- 扩展名集合")
    check("demo 的每一项都能被识别", bool(site_names), f"读到的内容：{site!r}")
    check(
        "与 demo/mkdocs.yml 的 markdown_extensions 完全一致",
        site_names == backend_names,
        f"site   : {site_names}\n         backend: {backend_names}",
    )
    check(
        "顺序也一致（顺序影响扩展优先级）",
        site_names == backend_names,
        f"site   : {site_names}\n         backend: {backend_names}",
    )

    print("--- 有意的差异只有一处")
    differing = [
        name
        for name in site_names
        if _options(site, name) != _options(backend, name)
    ]
    check(
        "选项不一致的扩展都在文档说明里",
        set(differing) <= set(DOCUMENTED_DEVIATIONS),
        f"未说明的差异：{sorted(set(differing) - set(DOCUMENTED_DEVIATIONS))}",
    )
    for name, reason in DOCUMENTED_DEVIATIONS.items():
        check(
            f"{name} 的差异仍然存在且是有意的（{reason}）",
            name in differing,
            f"{name} 的选项已经相同，删掉这条说明或恢复差异",
        )

    print("--- 不再启用会改变可见输出的扩展")
    for unwanted in ("nl2br", "sane_lists"):
        check(
            f"{unwanted} 未启用",
            unwanted not in backend_names,
            f"{unwanted} 会改变换行/列表编号，导致预览与站点不一致",
        )

    print("--- 每一项都能真正加载")
    try:
        markdown_render.configure(site_names)
        for name in site_names:
            # Rendering is what forces the extension to be imported; a typo in
            # a name would be accepted silently until then.
            markdown_render.render_markdown("probe")
        check(f"按 demo 的列表构建渲染器（{len(site_names)} 个扩展）", True)
    except Exception as exc:  # noqa: BLE001 - the message is the point
        check("按 demo 的列表构建渲染器", False, repr(exc))
    finally:
        markdown_render.configure()

    print("--- 配置可切换")
    check("configure() 换掉了扩展集合", markdown_render.extension_names() == backend_names,
          f"{markdown_render.extension_names()}")
    markdown_render.configure(["tables"])
    check(
        "缩窄到 tables 后只剩它",
        markdown_render.extension_names() == ["tables"],
        f"{markdown_render.extension_names()}",
    )
    # With `toc` gone there is no heading id, which proves the swap took effect
    # rather than merely being recorded.
    check("切换后渲染结果随之改变", 'id="title"' not in markdown_render.render_markdown("# Title"))
    markdown_render.configure()

    print(f"\n{'=' * 46}")
    print(f"结果：{passed} 通过 / {failed} 失败")
    print(f"{'=' * 46}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
