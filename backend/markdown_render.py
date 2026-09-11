"""Markdown -> sanitized HTML rendering.

Rendering happens on the server so every client receives consistently
sanitized markup, which makes stored XSS impossible even if a third-party page
embeds the comment widget.

Three concerns live here:

* **Parity with the built site.** The comment preview has to agree with the
  page it sits on, so the extension set is configuration
  (:data:`settings.markdown_extensions`) rather than a hardcoded list. Anything
  the site enables — ``pymdownx.superfences``, ``pymdownx.details``, the ``toc``
  permalinks — must be enabled here too, or the same text renders two
different ways. Extensions are re-resolved lazily and cached per thread,
  because Python-Markdown instances are stateful and therefore not safe to
  share between FastAPI's worker threads.
* **Autolinking** bare URLs is implemented as a small Python-Markdown extension
  rather than ``bleach.linkify``. ``linkify`` re-serializes the tree and escapes
  the ``&`` of entities markdown already produced, so ``print("x")`` inside a
  fenced code block surfaced as the literal ``&quot;``. Doing the work in the
  Markdown pass keeps exactly one escaping pass.
* **Sanitizing** the result: a single ``bleach.clean`` call is the only
  authority on which markup survives.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List
from xml.etree import ElementTree

import bleach
from markdown import Markdown
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.treeprocessors import Treeprocessor

from settings import settings

ALLOWED_TAGS = [
    "p", "br", "hr", "span", "div",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "sub", "sup", "small", "kbd",
    "abbr",
    "code", "pre",
    "blockquote",
    "ul", "ol", "li", "dl", "dt", "dd",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    # `pymdownx.details` (and raw HTML5) wrap their body in a real disclosure.
    "details", "summary",
    # `pymdownx.tabbed` is built from inert radio inputs plus labels; without
    # them the tabs render as unstyled stacked text.
    "input", "label",
]

# `class` is allowed wherever a Markdown extension decorates the theme's own
# markup: admonition titles, `footnote`/`footnote-ref`, tabbed labels, fenced
# `highlight` wrappers, and the dozens of unrelated tokens a syntax highlighter
# emits. Enumerating the names would be endless *and* would strip the next
# extension's output, while a class name cannot execute anything and the widget
# already lives in a page the commenter does not control.
_CLASS_HOOKED_TAGS = [
    "a", "abbr", "blockquote", "code", "dd", "del", "details", "div", "dl",
    "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "ins", "kbd", "label",
    "li", "mark", "ol", "p", "pre", "span", "strong", "sub", "summary",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
]

ALLOWED_ATTRIBUTES: Dict[str, List[str]] = {
    tag: ["class"] for tag in _CLASS_HOOKED_TAGS
}
# Everything else is attribute specific, so it is listed one tag at a time.
ALLOWED_ATTRIBUTES["a"] += ["href", "title", "rel", "target"]
ALLOWED_ATTRIBUTES["img"] = ["src", "alt", "title", "width", "height", "loading"]
ALLOWED_ATTRIBUTES["div"] += ["data-tabs"]
ALLOWED_ATTRIBUTES["label"] += ["for"]
# The `toc` extension slugs every heading; without the id an in-comment anchor
# link (`[跳到安装](#安装)`) has nothing to land on.
for _heading in ("h1", "h2", "h3", "h4", "h5", "h6"):
    ALLOWED_ATTRIBUTES[_heading] += ["id"]
ALLOWED_ATTRIBUTES["details"] += ["open", "id"]
# `pymdownx.tabbed` switches tabs with inert radio inputs.
ALLOWED_ATTRIBUTES["input"] = ["type", "id", "name", "checked", "disabled"]
ALLOWED_ATTRIBUTES["th"] += ["align", "colspan", "rowspan"]
ALLOWED_ATTRIBUTES["td"] += ["align", "colspan", "rowspan"]
ALLOWED_ATTRIBUTES["ol"] += ["start"]

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

SAFE_REL = "nofollow noopener noreferrer"

# A bare URL must not be preceded by a character that would make it part of a
# link, an attribute or a code span; those cases are already consumed by the
# higher-priority `link` and `backtick` inline patterns.
_BARE_URL = r"(?<![\w\"'=(\[<])(?P<url>https?://[^\s<>\"'\)\]]+)"


def _harden(anchor: ElementTree.Element) -> None:
    """Force safe behaviour on every outbound link."""
    if anchor.get("href", "").startswith(("http://", "https://")):
        anchor.set("rel", SAFE_REL)
        anchor.set("target", "_blank")


class _BareUrlLinker(InlineProcessor):
    """Turns ``https://example.com`` written in prose into an anchor."""

    def handleMatch(self, m, data):
        anchor = ElementTree.Element("a")
        anchor.set("href", m.group("url"))
        anchor.text = m.group("url")
        return anchor, m.start(0), m.end(0)


class _LinkHardener(Treeprocessor):
    """Applies :func:`_harden` to Markdown-syntax links as well."""

    def run(self, root):
        for anchor in root.iter("a"):
            _harden(anchor)
        return root


class _AutolinkExtension(Extension):
    """Bare-URL autolinking plus outbound-link hardening."""

    def extendMarkdown(self, md):  # noqa: N802 - Python-Markdown API
        # Priority 120 sits below `link` (160) and `backtick` (190), so a URL
        # already inside a link or a code span is left untouched.
        md.inlinePatterns.register(_BareUrlLinker(_BARE_URL, md), "bare_url", 120)
        md.treeprocessors.register(_LinkHardener(md), "link_hardener", 5)


# Python-Markdown instances carry mutable state (`reset()` rewrites their
# internal maps), so one shared instance would race when two of FastAPI's
# worker threads render at the same time. Each thread gets its own, built
# lazily and thrown away whenever the configuration changes.
_local = threading.local()
_lock = threading.Lock()
_configured: List[Any] = list(settings.markdown_extensions)
_generation = 0


def configure(extensions: Any = None) -> None:
    """Swap the extension set and invalidate every cached renderer.

    Called with no argument to (re)read :data:`settings.markdown_extensions`;
    tests pass an explicit list.
    """
    global _configured, _generation
    with _lock:
        _configured = list(
            settings.markdown_extensions if extensions is None else extensions
        )
        _generation += 1


def extension_names() -> List[str]:
    """Names of the active extensions, for diagnostics and parity checks."""
    return [
        item if isinstance(item, str) else next(iter(item))
        for item in _configured
    ]


def _split(entries: List[Any]) -> Any:
    """Split an MkDocs-shaped list into Markdown's two arguments.

    MkDocs accepts ``- toc: {permalink: true}`` in one list; Python-Markdown
    wants the names in ``extensions`` and the options in ``extension_configs``.
    Translating here means a site's ``markdown_extensions`` block can be reused
    verbatim instead of being rewritten by hand.
    """
    names: List[str] = []
    configs: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, str):
            names.append(entry)
            continue
        name, options = next(iter(entry.items()))
        names.append(name)
        configs[name] = options
    return names, configs


def _instance() -> Markdown:
    generation = _generation
    cached = getattr(_local, "entry", None)
    if cached is not None and cached[0] == generation:
        return cached[1]
    names, configs = _split(_configured)
    instance = Markdown(
        # `_AutolinkExtension` is ours, not a site setting, so it is appended
        # after whatever the site configures.
        extensions=names + [_AutolinkExtension()],
        extension_configs=configs,
        output_format="html",
        tab_length=4,
    )
    _local.entry = (generation, instance)
    return instance


def render_markdown(text: str) -> str:
    """Render ``text`` as Markdown and return sanitized HTML."""
    if not text:
        return ""

    raw_html = _instance().reset().convert(text)
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )


_WHITESPACE = re.compile(r"\s+")


def plain_text(text: str) -> str:
    """Text-only projection, used for blocked-word checks and logging."""
    stripped = bleach.clean(text, tags=[], attributes={}, strip=True)
    return _WHITESPACE.sub(" ", stripped).strip()
