"""MkDocs plugin that injects the comment widget into the pages that ask for it.

The plugin itself is a thin packaging layer: it copies the frontend assets
(CSS / JS) into the generated site and writes a small runtime configuration
file so the browser knows where the API lives and how the UI should behave.

It also decides *which* pages get a comment section. A page opts in through its
metadata, so the default is that nothing changes — dropping the plugin into an
existing site cannot start publishing comment forms on pages that never asked
for one.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

from mkdocs.config import config_options
from mkdocs.plugins import BasePlugin

log = logging.getLogger("mkdocs.plugins.comment")

PLUGIN_DIR = Path(__file__).resolve().parent
ASSET_DIR = PLUGIN_DIR / "assets"
DEST_SUBDIR = "assets/comment-plugin"

DEFAULT_EMOJI_PICKER = [
    "👍", "👎", "❤️", "🔥", "🎉", "😄", "😁", "😂", "🤣", "😊",
    "🙏", "👏", "🤝", "💯", "✅", "❌", "🚀", "✨", "👀", "🤔",
    "😮", "😢", "😡", "🥳",
]

# Name an empty nickname box resolves to. Kept in step with the backend's
# `MKC_ANONYMOUS_NAME`; the widget adopts whatever the server reports, so the
# placeholder it shows cannot disagree with the name the server stores.
DEFAULT_ANONYMOUS_NAME = "匿名用户"


class CommentPlugin(BasePlugin):
    """Inject a Material-styled, API-backed comment section."""

    config_scheme = (
        ("enabled", config_options.Type(bool, default=True)),
        # Where the backend lives. Absolute URL or a path on the same origin.
        ("api_base", config_options.Type(str, default="/api/v1")),
        ("title", config_options.Type(str, default="评论")),
        # CSS selector of a container *inside an opted-in page* to render the
        # widget in, for when the host lands at the end of the prose but the
        # comments belong somewhere else. Empty — the default — renders right
        # where the host is. It deliberately cannot opt a page in: a selector
        # matches on every page it matches, so allowing that made the per-page
        # front matter look inert. Only `meta_key` decides.
        ("page_selector", config_options.Type(str, default="")),
        ("reactions", config_options.Type(list, default=["👍", "❤️", "😄", "🎉", "🚀", "👀"])),
        # Leave empty to mirror `reactions`.
        ("page_reactions", config_options.Type(list, default=[])),
        ("emoji_picker", config_options.Type(list, default=list(DEFAULT_EMOJI_PICKER))),
        ("show_stats", config_options.Type(bool, default=True)),
        ("show_page_reactions", config_options.Type(bool, default=True)),
        # Every page load counts as a view; the backend caps per-IP flooding.
        ("count_views", config_options.Type(bool, default=True)),
        ("per_page", config_options.Type(int, default=20)),
        # Which pages get a comment section. A page opts in through its front
        # matter, where `meta_key` is the field to look for:
        #
        #     ---
        #     comments: true
        #     ---
        #
        # `meta_default` is what a page without that field gets, and it is
        # False on purpose: adding the plugin to an existing site must not
        # start publishing comment forms on every page. This requires the
        # `meta` Markdown extension (mkdocs-material enables it) — without it
        # the metadata never reaches the plugin and nothing opts in.
        ("meta_key", config_options.Type(str, default="comments")),
        ("meta_default", config_options.Type(bool, default=False)),
        # What an empty nickname box resolves to: "anonymous" (use
        # `anonymous_name`), "ip" (publish the address as the name), or a
        # literal default nickname.
        ("default_author", config_options.Type(str, default="anonymous")),
        # The name shown for a commenter who did not fill in a nickname. The
        # backend must agree — it is what gets stored — and the widget prefers
        # the server's value, so set it in both only if you want the label to
        # look right before the server responds.
        ("anonymous_name", config_options.Type(str, default=DEFAULT_ANONYMOUS_NAME)),
        ("require_author", config_options.Type(bool, default=False)),
        ("max_author_length", config_options.Type(int, default=60)),
        ("max_content_length", config_options.Type(int, default=5000)),
        ("allow_delete", config_options.Type(bool, default=True)),
        # --- appearance ------------------------------------------------
        # Overrides the inherited Material primary colour (any CSS colour).
        ("accent_color", config_options.Type(str, default="")),
        ("avatar_style", config_options.Choice(("initial", "none"), default="initial")),
        ("avatar_shape", config_options.Choice(("circle", "square"), default="circle")),
        ("density", config_options.Choice(("comfortable", "compact"), default="comfortable")),
        ("editor_rows", config_options.Type(int, default=4)),
        # --- behaviour ------------------------------------------------
        ("sort_order", config_options.Choice(("newest", "oldest"), default="newest")),
        ("time_style", config_options.Choice(("relative", "absolute"), default="relative")),
        # Remember the nickname in localStorage between visits.
        ("remember_author", config_options.Type(bool, default=True)),
        # Pre-fill `> **@author**` when replying to an existing reply.
        ("reply_quote", config_options.Type(bool, default=True)),
        # Override any UI string: labels: { submit: "Post" }
        ("labels", config_options.Type(dict, default={})),
        # Escape hatch for advanced users: raw keys merged into the JS config.
        ("extra_config", config_options.Type(dict, default={})),
    )

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def on_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.get("enabled", True):
            log.info("comment plugin disabled via config")
            return config

        prefix = _url_prefix(config)
        css = prefix + DEST_SUBDIR + "/comment.css"
        config_js = prefix + DEST_SUBDIR + "/config.js"
        main_js = prefix + DEST_SUBDIR + "/comment.js"

        if css not in config["extra_css"]:
            config["extra_css"].append(css)

        existing = list(config["extra_javascript"])
        for path in (config_js, main_js):
            if path not in existing:
                config["extra_javascript"].append(path)

        return config

    def on_page_content(
        self, html: str, page: Any, config: Dict[str, Any], files: Any
    ) -> str:
        """Append the widget host to the pages that opted in.

        The host carries the canonical page key, so the browser does not have to
        re-derive it from the URL. That is the same normalisation the widget used
        to do in JavaScript (strip `index.html` and any trailing slash), done
        once, on the side that knows the real file name.
        """
        if not self.config.get("enabled", True):
            return html
        if not self._page_enabled(page):
            return html
        prefix = _url_prefix(config)
        return html + (
            f'<div class="md-comment-host" data-page="{_page_key(page)}"'
            f' data-assets="{prefix}{DEST_SUBDIR}"></div>'
        )

    def on_post_build(self, config: Dict[str, Any]) -> None:
        if not self.config.get("enabled", True):
            return

        site_dir = Path(config["site_dir"])
        target = site_dir / DEST_SUBDIR
        target.mkdir(parents=True, exist_ok=True)

        for name in ("comment.css", "comment.js"):
            source = ASSET_DIR / name
            if not source.is_file():
                log.error("缺少前端资源文件：%s", source)
                continue
            shutil.copyfile(source, target / name)

        payload = self._js_config()
        (target / "config.js").write_text(
            "/* 由 mkdocs-comment-plugin 自动生成，请勿手动修改 */\n"
            "window.MKCOMMENT_CONFIG = "
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + ";\n",
            encoding="utf-8",
        )
        log.debug("评论插件资源已写入 %s", target)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _page_enabled(self, page: Any) -> bool:
        """Whether this page opted into a comment section.

        The value can arrive as a real boolean (`comments: true`) or as the
        string Markdown's `meta` extension produces for a bare word, and an
        unrecognised value is treated as "not enabled" rather than raising: a
        typo in front matter should not break the build.
        """
        key = self.config.get("meta_key") or "comments"
        meta = getattr(page, "meta", None) or {}
        if key not in meta:
            return bool(self.config.get("meta_default", False))
        return _as_bool(meta.get(key))

    def _js_config(self) -> Dict[str, Any]:
        cfg = dict(self.config)
        page_reactions: List[str] = cfg.get("page_reactions") or list(cfg.get("reactions") or [])

        labels = dict(cfg.get("labels") or {})
        payload: Dict[str, Any] = {
            "apiBase": cfg.get("api_base", "/api/v1"),
            "title": cfg.get("title", "评论"),
            "pageSelector": cfg.get("page_selector", ""),
            "reactions": list(cfg.get("reactions") or []),
            "pageReactions": page_reactions,
            "emojiPicker": list(cfg.get("emoji_picker") or DEFAULT_EMOJI_PICKER),
            "showStats": bool(cfg.get("show_stats", True)),
            "showPageReactions": bool(cfg.get("show_page_reactions", True)),
            "countViews": bool(cfg.get("count_views", True)),
            "perPage": int(cfg.get("per_page", 20)),
            "defaultAuthor": cfg.get("default_author", "anonymous"),
            "anonymousName": cfg.get("anonymous_name") or DEFAULT_ANONYMOUS_NAME,
            "requireAuthor": bool(cfg.get("require_author", False)),
            "maxAuthorLength": int(cfg.get("max_author_length", 60)),
            "maxContentLength": int(cfg.get("max_content_length", 5000)),
            "allowDelete": bool(cfg.get("allow_delete", True)),
            "accentColor": cfg.get("accent_color", ""),
            "avatarStyle": cfg.get("avatar_style", "initial"),
            "avatarShape": cfg.get("avatar_shape", "circle"),
            "density": cfg.get("density", "comfortable"),
            "editorRows": int(cfg.get("editor_rows", 4)),
            "sortOrder": cfg.get("sort_order", "newest"),
            "timeStyle": cfg.get("time_style", "relative"),
            "rememberAuthor": bool(cfg.get("remember_author", True)),
            "replyQuote": bool(cfg.get("reply_quote", True)),
            # Language overrides win over the built-in Chinese defaults.
            "labels": labels,
        }

        extra = cfg.get("extra_config") or {}
        if isinstance(extra, dict):
            payload.update(extra)
        return payload


def _as_bool(value: Any) -> bool:
    """Interpret a front-matter value as a switch.

    Markdown's `meta` extension hands over strings, so `comments: true` may
    arrive as the string ``"true"``; YAML-aware front matter can arrive as a
    real bool. Both spellings, and their negatives, are understood here.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0", ""):
        return False
    # Anything else (a sentence, a list) is not a switch; see `_page_enabled`.
    return False


def _page_key(page: Any) -> str:
    """The canonical comment key for a page: ``/guide``, or ``/`` for the root.

    Matches what the widget has always sent (`location.pathname` with
    `index.html` and any trailing slash removed), so existing comment data keeps
    resolving to the same page. Doing it here means the key comes from the real
    file name instead of from whatever URL the reader happened to arrive at.

    MkDocs spells the site root as an empty string, and a directory index as
    ``.`` — both are ``/``.
    """
    url = str(getattr(page, "url", "") or "").strip()
    url = url.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    if url.endswith("index.html"):
        url = url[: -len("index.html")]
    if url.strip("./") == "":
        return "/"
    if not url.startswith("/"):
        url = "/" + url
    return url.rstrip("/") or "/"


def _url_prefix(config: Dict[str, Any]) -> str:
    """Return a root-relative URL prefix that works under a sub-path deploy.

    ``site_url: https://example.com/docs/`` becomes ``/docs/``; when no
    ``site_url`` is configured (typical for ``mkdocs serve``) we fall back to
    ``/``.
    """
    site_url = config.get("site_url") or ""
    if not site_url:
        return "/"
    path = urlparse(site_url).path or "/"
    if not path.endswith("/"):
        path += "/"
    return path
