"""Runtime configuration for the comment backend.

Everything is driven by environment variables (optionally loaded from a
``.env`` file placed next to this module) so the service can be deployed
without touching the code.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Sequence

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_REACTIONS: List[str] = ["👍", "❤️", "😄", "🎉", "🚀", "👀"]

# What a commenter who leaves the nickname box empty is called. Shown in the
# widget, in the `@mention` a reply quotes, and in the hover tooltip, so it is
# translated rather than hardcoded per language.
DEFAULT_ANONYMOUS_NAME = "匿名用户"

# The Markdown extension set used to render comments and previews.
#
# It deliberately mirrors a typical `mkdocs.yml` rather than binding to a
# frozen list: the preview must agree with the built page, and the only way to
# guarantee that is for both sides to run the *same* extensions. The names and
# nested options are exactly what MkDocs accepts, so a site's
# `markdown_extensions:` block can be copied here unchanged (or expressed as
# JSON in `MKC_MARKDOWN_EXTENSIONS`).
#
# `scripts/test_markdown_parity.py` compares this with `demo/mkdocs.yml` so the
# two cannot drift apart unnoticed.
DEFAULT_MARKDOWN_EXTENSIONS: List[Any] = [
    "admonition",
    "attr_list",
    "def_list",
    "footnotes",
    "md_in_html",
    "tables",
    # Plain `toc` keeps the heading ids the site generates, without its
    # permalink `¶`. This is the one deliberate deviation from a straight copy
    # of `markdown_extensions`: a comment heading is not a page section, and a
    # permalink inside one invites copying an anchor that resolves into the
    # comments. Use `{"toc": {"permalink": true}}` for byte-for-byte parity.
    "toc",
    "pymdownx.details",
    "pymdownx.superfences",
    # Options matter as much as names: `alternate_style` is what gives tabs
    # Material's horizontal bar instead of stacked disclosure blocks.
    {"pymdownx.highlight": {"anchor_linenums": True}},
    {"pymdownx.tabbed": {"alternate_style": True}},
]

DEFAULT_EMOJI_PICKER: List[str] = [
    "👍", "👎", "❤️", "🔥", "🎉", "😄", "😁", "😂", "🤣", "😊",
    "🙏", "👏", "🤝", "💯", "✅", "❌", "🚀", "✨", "👀", "🤔",
    "😮", "😢", "😡", "🥳",
]


def _load_dotenv() -> None:
    """Minimal ``.env`` loader (no external dependency)."""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Never override values that were explicitly exported.
        os.environ.setdefault(key, value)


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_choice(name: str, default: str, allowed: Sequence[str]) -> str:
    """Read one of a fixed set of values, loudly.

    A typo in a setting that changes what readers see is worse than a crash at
    startup: silently falling back would leave the operator believing a policy
    is in force when it is not.
    """
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    value = value.strip().lower()
    if value not in allowed:
        raise ValueError(
            f"{name} 只能是 {'、'.join(allowed)} 之一，收到 {value!r}。"
        )
    return value


def _env_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return list(default)
    items = [item.strip() for item in value.replace(";", ",").split(",")]
    return [item for item in items if item]


def _env_markdown_extensions(name: str, default: List[Any]) -> List[Any]:
    """Parse a Markdown extension list.

    Two spellings are accepted because the two useful cases are different in
    shape. A plain comma separated list covers the common case
    (``tables,pymdownx.superfences``), while a JSON array is needed the moment
    an extension takes options — it is then a literal translation of the
    ``markdown_extensions`` block in ``mkdocs.yml``::

        MKC_MARKDOWN_EXTENSIONS='["tables", {"toc": {"permalink": true}}]'

    A malformed value raises instead of falling back to the default: silently
    rendering with a different extension set than the operator asked for is
    exactly the mismatch this setting exists to prevent.
    """
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return copy.deepcopy(default)
    value = value.strip()
    if not value.startswith("["):
        items = [item.strip() for item in value.replace(";", ",").split(",")]
        return [item for item in items if item]
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} 不是合法的 JSON 数组：{exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{name} 必须是 JSON 数组。")
    for item in parsed:
        if isinstance(item, str):
            continue
        # `{"toc": {"permalink": true}}` — exactly one name, mapping to options.
        if isinstance(item, dict) and len(item) == 1:
            options = next(iter(item.values()))
            if isinstance(options, dict):
                continue
        raise ValueError(
            f"{name} 的每一项必须是扩展名字符串，或 {{\"名字\": {{\"选项\": 值}}}} 形式的对象。"
        )
    return parsed


def _resolve_db_path(value: str) -> str:
    """Anchor a relative database path to the backend directory.

    Without this, ``MKC_DB_PATH=comments.db`` resolves against the process
    working directory, so ``uvicorn --app-dir backend`` started from the repo
    root quietly creates a second, empty database while the real one sits
    untouched next to this file.
    """
    if value == ":memory:":
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)


@dataclass
class Settings:
    """All tunables of the comment service.

    Deliberately no host/port: the listen address is chosen by the command that
    starts uvicorn (``--host`` / ``--port``), because the process is launched
    through the ``uvicorn`` CLI. A setting here would look like it restricts the
    bind but could not, and an operator who believes a port is closed when it is
    not is worse off than one who never saw the option.
    """

    # --- server -------------------------------------------------------
    root_path: str = ""

    # --- storage ------------------------------------------------------
    db_path: str = str(BASE_DIR / "comments.db")

    # --- CORS ---------------------------------------------------------
    # "*" allows any origin (fine for a public docs site). Provide a
    # comma separated list to lock it down.
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = False

    # --- moderation / limits -----------------------------------------
    max_content_length: int = 5000
    max_author_length: int = 60
    max_page_length: int = 512
    # Write operations (posting comments, toggling reactions) per IP.
    rate_limit_requests: int = 30
    rate_limit_window: int = 60  # seconds
    # Page views per IP. Every page load counts, but flooding is capped here.
    view_rate_limit_requests: int = 60
    view_rate_limit_window: int = 60  # seconds
    allow_delete: bool = True
    admin_token: str = ""
    blocked_words: List[str] = field(default_factory=list)

    # --- networking ---------------------------------------------------
    trust_proxy_headers: bool = False
    proxy_header: str = "X-Forwarded-For"

    # --- behaviour ----------------------------------------------------
    # What an empty nickname box means. "anonymous" uses `anonymous_name`,
    # "ip" publishes the address as the name (the pre-1.1 behaviour), and
    # anything else is taken as a literal default nickname.
    default_author: str = "anonymous"
    comment_reactions: List[str] = field(default_factory=lambda: list(DEFAULT_REACTIONS))
    page_reactions: List[str] = field(default_factory=lambda: list(DEFAULT_REACTIONS))
    emoji_picker: List[str] = field(default_factory=lambda: list(DEFAULT_EMOJI_PICKER))

    # --- identity ------------------------------------------------------
    anonymous_name: str = DEFAULT_ANONYMOUS_NAME
    # Who gets their address printed next to the name: "always" | "anonymous"
    # | "never". Defaults to everyone, because that is the only setting under
    # which a named commenter and an anonymous one are distinguishable at a
    # glance, and because an address is the only identity a reader can actually
    # check. The address is never used *as* a name, so "never" really does hide
    # it — nothing to migrate.
    show_author_ip: str = "always"

    # --- rendering -----------------------------------------------------
    # Kept in step with the site's `markdown_extensions` so a preview and a
    # posted comment look exactly like the page around them.
    markdown_extensions: List[Any] = field(
        default_factory=lambda: copy.deepcopy(DEFAULT_MARKDOWN_EXTENSIONS)
    )

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            root_path=_env_str("MKC_ROOT_PATH", ""),
            db_path=_resolve_db_path(_env_str("MKC_DB_PATH", str(BASE_DIR / "comments.db"))),
            cors_origins=_env_list("MKC_CORS_ORIGINS", ["*"]),
            cors_allow_credentials=_env_bool("MKC_CORS_ALLOW_CREDENTIALS", False),
            max_content_length=_env_int("MKC_MAX_CONTENT_LENGTH", 5000),
            max_author_length=_env_int("MKC_MAX_AUTHOR_LENGTH", 60),
            max_page_length=_env_int("MKC_MAX_PAGE_LENGTH", 512),
            rate_limit_requests=_env_int("MKC_RATE_LIMIT_REQUESTS", 30),
            rate_limit_window=_env_int("MKC_RATE_LIMIT_WINDOW", 60),
            view_rate_limit_requests=_env_int("MKC_VIEW_RATE_LIMIT_REQUESTS", 60),
            view_rate_limit_window=_env_int("MKC_VIEW_RATE_LIMIT_WINDOW", 60),
            allow_delete=_env_bool("MKC_ALLOW_DELETE", True),
            admin_token=_env_str("MKC_ADMIN_TOKEN", ""),
            blocked_words=_env_list("MKC_BLOCKED_WORDS", []),
            trust_proxy_headers=_env_bool("MKC_TRUST_PROXY_HEADERS", False),
            proxy_header=_env_str("MKC_PROXY_HEADER", "X-Forwarded-For"),
            default_author=_env_str("MKC_DEFAULT_AUTHOR", "anonymous"),
            comment_reactions=_env_list("MKC_COMMENT_REACTIONS", DEFAULT_REACTIONS),
            page_reactions=_env_list("MKC_PAGE_REACTIONS", DEFAULT_REACTIONS),
            emoji_picker=_env_list("MKC_EMOJI_PICKER", DEFAULT_EMOJI_PICKER),
            anonymous_name=_env_str("MKC_ANONYMOUS_NAME", DEFAULT_ANONYMOUS_NAME),
            show_author_ip=_env_choice(
                "MKC_SHOW_AUTHOR_IP", "always", ("anonymous", "always", "never")
            ),
            markdown_extensions=_env_markdown_extensions(
                "MKC_MARKDOWN_EXTENSIONS", DEFAULT_MARKDOWN_EXTENSIONS
            ),
        )

    # ------------------------------------------------------------------ #
    # identity
    # ------------------------------------------------------------------ #
    def display_author(self, nickname: str, ip: str) -> str:
        """The name a comment or reaction is published under.

        One rule for both, because it is one identity: an empty nickname box
        means the deployment's anonymous name, never the visitor's address.
        Using the address as a *name* is what made every anonymous commenter
        look like `192.0.2.7` — and it cannot be turned off once it is the
        name, which is why `show_author_ip` only controls the badge beside the
        name and this method never returns an address unless asked to.
        """
        nickname = (nickname or "").strip()
        if nickname:
            return nickname
        configured = (self.default_author or "").strip()
        if configured == "ip":
            return ip
        if configured in ("", "anonymous"):
            return self.anonymous_name
        return configured

    def is_anonymous(self, author: str) -> bool:
        """True when a name is the placeholder rather than a chosen one."""
        return author == self.anonymous_name

    def shows_author_ip(self, is_anonymous: bool) -> bool:
        """Whether this commenter's address is published beside the name.

        `is_anonymous` only matters for the middle setting: on "always" (the
        default) it is ignored, on "never" the field is not even sent.
        """
        if self.show_author_ip == "never":
            return False
        if self.show_author_ip == "always":
            return True
        return is_anonymous

    def suggested_author(self, ip: str) -> str:
        """What to pre-fill in the nickname box, if anything.

        Empty in the anonymous modes on purpose: the name is added server side,
        while a pre-filled value would be stored as if the visitor had typed it
        — which also makes it *their* remembered nickname from then on, and
        would turn a whole site's readers into people called "匿名用户".
        """
        configured = (self.default_author or "").strip()
        if configured in ("", "anonymous"):
            return ""
        return ip if configured == "ip" else configured


settings = Settings.from_env()
