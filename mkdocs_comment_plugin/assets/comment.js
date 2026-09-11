/*!
 * MkDocs Comment Plugin — frontend
 * Material for MkDocs styled comment section backed by a REST API.
 * No external dependencies.
 */
(function () {
  "use strict";

  /* ====================================================================== *
   * configuration
   * ====================================================================== */
  var DEFAULTS = {
    apiBase: "/api/v1",
    title: "评论",
    page: null, // null => use the key the plugin wrote into the host
    // Where *inside an opted-in page* the widget renders. Empty means "right
    // where the plugin put the host element". It does not decide whether a
    // page has comments: only the host does, so a page whose front matter does
    // not ask for a comment section never gets one, whatever this is set to.
    pageSelector: "",
    reactions: ["👍", "❤️", "😄", "🎉", "🚀", "👀"],
    pageReactions: null,
    emojiPicker: [
      "👍", "👎", "❤️", "🔥", "🎉", "😄", "😁", "😂", "🤣", "😊",
      "🙏", "👏", "🤝", "💯", "✅", "❌", "🚀", "✨", "👀", "🤔",
      "😮", "😢", "😡", "🥳"
    ],
    showStats: true,
    showPageReactions: true,
    countViews: true,
    perPage: 20,
    // What an empty nickname box means. "anonymous" lets the server fill in
    // `anonymousName`, so the box is never pre-filled with an address.
    defaultAuthor: "anonymous",
    anonymousName: "匿名用户",
    requireAuthor: false,
    maxAuthorLength: 60,
    maxContentLength: 5000,
    allowDelete: true,
    // --- appearance ---------------------------------------------------
    // Empty means "inherit the theme's primary colour".
    accentColor: "",
    avatarStyle: "initial", // initial | none
    avatarShape: "circle", // circle | square
    density: "comfortable", // comfortable | compact
    editorRows: 4,
    // --- behaviour ----------------------------------------------------
    sortOrder: "newest", // newest | oldest
    timeStyle: "relative", // relative | absolute
    rememberAuthor: true,
    replyQuote: true,
    syncServerConfig: true,
    labels: {}
  };

  var DEFAULT_LABELS = {
    author: "昵称",
    // `{name}` is substituted with the deployment's anonymous name, so renaming
    // it does not leave a stale placeholder behind. This is also the only place
    // the Markdown note appears — the composer bar used to repeat it beside the
    // emoji button, which said the same thing twice in one row.
    authorPlaceholder: "留空则显示为 {name}",
    authorIp: "该访客的 IP 地址",
    contentPlaceholder: "写下你的想法… 支持 Markdown 语法",
    submit: "发表评论",
    submitting: "提交中…",
    preview: "预览",
    previewEmpty: "没有可预览的内容",
    reply: "回复",
    cancel: "取消",
    remove: "删除",
    removeConfirm: "确认删除？",
    more: "加载更多",
    loading: "加载中…",
    empty: "还没有评论，来抢沙发吧～",
    views: "浏览",
    comments: "评论",
    addReaction: "添加表情",
    // Shown when the reactor list is shorter than the count — reactions left
    // before names were recorded, or two visitors sharing one nickname.
    reactionOthers: "和其他 {n} 人",
    reactionCount: "共 {n} 人",
    justNow: "刚刚",
    minuteAgo: "分钟前",
    hourAgo: "小时前",
    dayAgo: "天前",
    monthAgo: "个月前",
    yearAgo: "年前",
    error: "出错了，请稍后再试",
    networkError: "无法连接评论服务，请稍后再试",
    requiredAuthor: "请填写昵称",
    // Shown when the submit button is pressed with an empty box. Deliberately
    // short: it answers "what now?" in a glance, and the placeholder above
    // already explains what to do.
    emptyContent: "请先输入内容",
    tooLong: "内容超过长度限制",
    posted: "评论已发布",
    replied: "回复已发布",
    deleted: "评论已删除",
    deletedComment: "该评论已被删除",
    you: "我",
    retry: "重试",
    // The source toggle in a comment's top-right corner. One label per state,
    // because the button's icon stays the same and these are the only clue
    // about what clicking does next.
    showSource: "查看 Markdown 源码",
    showRendered: "查看渲染后的内容"
  };

  var raw = window.MKCOMMENT_CONFIG || {};
  var CFG = shallowMerge(shallowMerge({}, DEFAULTS), raw);
  CFG.labels = shallowMerge(shallowMerge({}, DEFAULT_LABELS), raw.labels || {});
  if (!CFG.pageReactions) {
    CFG.pageReactions = CFG.reactions.slice();
  }
  CFG.apiBase = String(CFG.apiBase || "").replace(/\/+$/, "");

  /* ====================================================================== *
   * tiny utilities
   * ====================================================================== */
  function shallowMerge(target, source) {
    for (var key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) {
        target[key] = source[key];
      }
    }
    return target;
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function attr(value) {
    return esc(value).replace(/`/g, "&#96;");
  }

  /**
   * Look up a UI string, allowing `labels` in mkdocs.yml to override it.
   *
   * An explicitly empty label (e.g. `more: ""`) renders nothing and
   * lets the caller drop the element, while a missing key falls back to the
   * built-in Chinese default from `DEFAULT_LABELS`.
   *
   * `{name}` placeholders are substituted from `vars` when given, so a label
   * that embeds a number stays translatable as a whole sentence instead of
   * being glued together from fragments the translator cannot reorder.
   */
  function t(key, vars) {
    var value = CFG.labels[key];
    if (value === undefined || value === null) {
      return key;
    }
    if (!vars) {
      return value;
    }
    return String(value).replace(/\{(\w+)\}/g, function (match, name) {
      return Object.prototype.hasOwnProperty.call(vars, name)
        ? String(vars[name])
        : match;
    });
  }

  function hashString(text) {
    var hash = 2166136261;
    for (var i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = (hash * 16777619) >>> 0;
    }
    return hash >>> 0;
  }

  /** Deterministic colour per author, Material-ish saturation. */
  function avatarColor(name) {
    return "hsl(" + (hashString(String(name || "?")) % 360) + ", 42%, 48%)";
  }

  var CJK = /[\u3400-\u9fff\uf900-\ufaff]/;

  /**
   * Glyph shown inside the avatar.
   *
   * * IPv4 / IPv6 → the most distinctive trailing block
   * * CJK names → the **last** glyph (Chinese names are surname-first, so the
   *   given name — the part people go by — comes last)
   * * everything else → initials
   *
   * Anonymous commenters do not come through here at all; they get
   * :func:`icon("account")` instead, because a letter cannot say "no name" the
   * way a person silhouette can.
   */
  function avatarInitial(name) {
    var text = String(name || "?").trim();
    if (!text) {
      return "?";
    }
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(text)) {
      return text.split(".").pop();
    }
    if (text.indexOf(":") !== -1) {
      return text.split(":").filter(Boolean).pop().slice(-4);
    }
    var chars = Array.from(text);
    for (var i = chars.length - 1; i >= 0; i--) {
      if (CJK.test(chars[i])) {
        return chars[i];
      }
    }
    var words = text.split(/\s+/).filter(Boolean);
    if (words.length > 1) {
      return (words[0][0] + words[1][0]).toUpperCase();
    }
    return chars[0].toUpperCase();
  }

  function parseDate(value) {
    if (!value) {
      return null;
    }
    var date = new Date(value);
    return isNaN(date.getTime()) ? null : date;
  }

  function timeAgo(value) {
    var date = parseDate(value);
    if (!date) {
      return "";
    }
    var seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 45) {
      return t("justNow");
    }
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) {
      return minutes + " " + t("minuteAgo");
    }
    var hours = Math.floor(minutes / 60);
    if (hours < 24) {
      return hours + " " + t("hourAgo");
    }
    var days = Math.floor(hours / 24);
    if (days < 30) {
      return days + " " + t("dayAgo");
    }
    var months = Math.floor(days / 30);
    if (months < 12) {
      return months + " " + t("monthAgo");
    }
    return Math.floor(months / 12) + " " + t("yearAgo");
  }

  function storage(key, value) {
    try {
      if (value === undefined) {
        return window.localStorage.getItem(key);
      }
      if (value === null) {
        window.localStorage.removeItem(key);
      } else {
        window.localStorage.setItem(key, value);
      }
    } catch (err) {
      /* private mode / disabled storage */
    }
    return null;
  }

  function uuid() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    var bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
      window.crypto.getRandomValues(bytes);
    } else {
      for (var i = 0; i < 16; i++) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = [];
    for (var j = 0; j < 16; j++) {
      hex.push((bytes[j] + 0x100).toString(16).slice(1));
    }
    return (
      hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
      hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
      hex.slice(10, 16).join("")
    );
  }

  /* ====================================================================== *
   * local identity
   * ====================================================================== */
  var VISITOR_KEY = "mkdocs-comment:visitor";
  var AUTHOR_KEY = "mkdocs-comment:author";
  var TOKEN_KEY = "mkdocs-comment:tokens";

  var visitorId = storage(VISITOR_KEY);
  if (!visitorId) {
    visitorId = uuid();
    storage(VISITOR_KEY, visitorId);
  }

  function tokens() {
    try {
      return JSON.parse(storage(TOKEN_KEY) || "{}") || {};
    } catch (err) {
      return {};
    }
  }

  function saveToken(id, token) {
    if (!id || !token) {
      return;
    }
    var map = tokens();
    map[id] = token;
    try {
      storage(TOKEN_KEY, JSON.stringify(map));
    } catch (err) {
      /* ignore */
    }
  }

  function dropToken(id) {
    var map = tokens();
    delete map[id];
    try {
      storage(TOKEN_KEY, JSON.stringify(map));
    } catch (err) {
      /* ignore */
    }
  }

  /* ====================================================================== *
   * appearance
   * ====================================================================== */
  /**
   * Readable text colour for a custom accent, using WCAG relative luminance.
   * Returns "" for anything that is not a hex colour, so the caller can keep
   * the theme's own pairing instead of guessing.
   */
  function contrastOn(hex) {
    var match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex == null ? "" : hex).trim());
    if (!match) {
      return "";
    }
    var raw = match[1];
    if (raw.length === 3) {
      raw = raw.charAt(0) + raw.charAt(0) + raw.charAt(1) + raw.charAt(1) +
        raw.charAt(2) + raw.charAt(2);
    }
    function channel(offset) {
      var value = parseInt(raw.substr(offset, 2), 16) / 255;
      return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
    }
    var luminance = 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4);
    return luminance > 0.45 ? "rgba(0, 0, 0, 0.87)" : "#ffffff";
  }

  /** Translate the appearance config into root classes and CSS variables. */
  function applyTheme(root) {
    if (CFG.density === "compact") {
      root.classList.add("md-comment--compact");
    }
    if (CFG.avatarShape === "square") {
      root.classList.add("md-comment--square");
    }
    if (CFG.accentColor) {
      // Both tiers, not just the fills. `accent_color` is the operator saying
      // "this is my accent", and a focus ring that stayed the theme's colour
      // while every filled control used theirs would read as a bug. Left unset,
      // the fills keep Material's primary and the highlights keep
      // `--md-accent-fg-color` — the colour a link turns when you hover it.
      root.style.setProperty("--mkc-primary", CFG.accentColor);
      root.style.setProperty("--mkc-accent", CFG.accentColor);
      var on = contrastOn(CFG.accentColor);
      if (on) {
        root.style.setProperty("--mkc-on-primary", on);
      }
    }
  }

  /* ====================================================================== *
   * page identification
   * ====================================================================== */
  function currentPage() {
    var raw = CFG.page || window.location.pathname || "/";
    try {
      raw = decodeURI(raw);
    } catch (err) {
      /* keep as-is */
    }
    raw = raw.replace(/index\.html$/, "");
    if (raw === "/index.html") {
      raw = "/";
    }
    if (raw.length > 1 && raw.charAt(raw.length - 1) === "/") {
      raw = raw.slice(0, -1);
    }
    return raw || "/";
  }

  /* ====================================================================== *
   * API layer
   * ====================================================================== */
  function apiUrl(path) {
    return CFG.apiBase + path;
  }

  function request(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || "GET",
      headers: { Accept: "application/json" },
      credentials: "omit"
    };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    if (opts.headers) {
      for (var key in opts.headers) {
        if (Object.prototype.hasOwnProperty.call(opts.headers, key)) {
          init.headers[key] = opts.headers[key];
        }
      }
    }
    return window.fetch(apiUrl(path), init).then(function (response) {
      var isJson = (response.headers.get("content-type") || "").indexOf("json") !== -1;
      return (isJson ? response.json() : response.text()).then(function (data) {
        if (!response.ok) {
          var detail = data && data.detail ? data.detail : response.statusText;
          var error = new Error(typeof detail === "string" ? detail : t("error"));
          error.status = response.status;
          error.payload = data;
          throw error;
        }
        return data;
      });
    });
  }

  /* ====================================================================== *
   * state
   * ====================================================================== */
  var state = {
    root: null,
    page: "/",
    comments: [],
    hasMore: false,
    offset: 0,
    stats: null,
    loading: false,
    mountedFor: null,
    // Pill whose reaction tooltip is currently open.
    tipFor: null,
    // Whether that tip was opened by focus rather than by hovering, and the
    // last pointer position, so `resyncTip` can re-derive the answer after a
    // rebuild has invalidated the pill it was based on.
    tipKeyboard: false,
    tipX: null,
    tipY: null,
    // Ids of the comments currently shown as Markdown source. Kept here rather
    // than in the DOM because `renderList` rebuilds every item, and without
    // this a reaction — whose pill sits directly under the toggle — would flip
    // the comment the reader was inspecting back to its rendered form.
    sourceOpen: {}
  };

  /* ====================================================================== *
   * DOM helpers
   * ====================================================================== */
  function $(selector, scope) {
    return (scope || state.root).querySelector(selector);
  }

  function $$(selector, scope) {
    return Array.prototype.slice.call((scope || state.root).querySelectorAll(selector));
  }

  function toast(message, isError) {
    var node = document.createElement("div");
    node.className = "md-comment__toast" + (isError ? " md-comment__toast--error" : "");
    node.setAttribute("role", "status");
    node.textContent = message;
    document.body.appendChild(node);
    window.requestAnimationFrame(function () {
      node.classList.add("is-visible");
    });
    window.setTimeout(function () {
      node.classList.remove("is-visible");
      window.setTimeout(function () {
        if (node.parentNode) {
          node.parentNode.removeChild(node);
        }
      }, 250);
    }, 2600);
  }

  /* ====================================================================== *
   * templates
   * ====================================================================== */
  function avatarHtml(name, anonymous) {
    if (CFG.avatarStyle === "none") {
      return "";
    }
    if (anonymous) {
      // One glyph for everybody unnamed. A lettered avatar would have to invent
      // an initial — and hashing the placeholder for a colour would imply a
      // stable identity the name does not carry. The silhouette says "no name"
      // outright, and takes the same neutral surface the rest of the meta line
      // uses so it reads as a placeholder rather than as a person.
      return (
        '<div class="md-comment__avatar md-comment__avatar--anonymous" aria-hidden="true">' +
        icon("account") +
        "</div>"
      );
    }
    return (
      '<div class="md-comment__avatar" aria-hidden="true" style="background:' +
      attr(avatarColor(name)) +
      '">' +
      esc(avatarInitial(name)) +
      "</div>"
    );
  }

  /** Stored nickname, unless the deploy opted out of remembering it. */
  function rememberedAuthor() {
    return CFG.rememberAuthor ? storage(AUTHOR_KEY) || "" : "";
  }

  /**
   * Nickname to attribute a reaction to: whatever is typed in the composer,
   * else the remembered one. An empty result makes the server fall back to the
   * anonymous name, exactly as it does for a comment author.
   */
  function currentAuthor() {
    var input = state.root && state.root.querySelector('.md-comment__form [data-role="author"]');
    var typed = input ? (input.value || "").trim() : "";
    return typed || rememberedAuthor();
  }

  /**
   * The text for "who reacted", combining the names we know with a count.
   *
   * The count is authoritative and the name list is not: reactions written
   * before names were recorded have none at all, and two visitors can share a
   * nickname. Spelling out the remainder is what keeps a pill reading
   * "👍 3" from opening a tooltip that names a single person.
   */
  function reactionTip(names, count) {
    var list = names || [];
    var total = count || 0;
    if (!list.length) {
      return total ? t("reactionCount", { n: total }) : "";
    }
    var text = list.join("、");
    var rest = total - list.length;
    return rest > 0 ? text + " " + t("reactionOthers", { n: rest }) : text;
  }

  function reactionHtml(emoji, count, active, action, users) {
    var names = users || [];
    var tip = reactionTip(names, count);
    // The tooltip text is resolved once here and carried on the element, so the
    // single shared tip node never has to re-derive it. It is also the
    // accessible name, keeping the information from being hover-only.
    var label = emoji + (count ? " " + count : "") + (tip ? " " + tip : "");
    return (
      '<button type="button" class="md-comment__reaction' + (active ? " is-active" : "") + '" ' +
      'data-act="' + action + '" data-emoji="' + attr(emoji) + '" ' +
      'aria-pressed="' + (active ? "true" : "false") + '" ' +
      'aria-label="' + attr(label) + '" ' +
      (tip ? 'data-tip="' + attr(tip) + '" ' : "") +
      ">" +
      esc(emoji) +
      (count ? '<span class="md-comment__reaction-count">' + count + "</span>" : "") +
      "</button>"
    );
  }

  /**
   * Reaction pills for a comment or for the page itself.
   *
   * `palette` seeds the row with the configured emoji so they are always
   * clickable. The page-level bar wants that; a comment does not — its picker
   * covers discovery, so comments pass `counted: true` and only render the
   * reactions somebody actually left.
   *
   * `users` maps emoji to the names of whoever reacted, in order.
   */
  function reactionRowHtml(counts, mine, action, palette, counted, users) {
    var html = "";
    var seen = {};
    var actors = users || {};
    (palette || []).concat(Object.keys(counts || {})).forEach(function (emoji) {
      if (!emoji || seen[emoji]) {
        return;
      }
      seen[emoji] = true;
      var count = (counts || {})[emoji] || 0;
      var active = (mine || []).indexOf(emoji) !== -1;
      if (counted && !count && !active) {
        return;
      }
      html += reactionHtml(emoji, count, active, action, actors[emoji]);
    });
    return html;
  }

  /* ------------------------------------------------------------------ tip
     One shared node lists who reacted with a pill. It renders on a single
     line and is ellipsised by CSS, which is why this is a real element
     rather than a `title` attribute. */
  function showTip(pill) {
    var tip = $('[data-role="tip"]');
    var text = pill.getAttribute("data-tip");
    if (!tip || !text) {
      hideTip();
      return;
    }
    state.tipFor = pill;
    if (tip.textContent !== text) {
      // Assigning the same string replaces the text node for nothing, and this
      // runs on every pointer move while a tip is up.
      tip.textContent = text;
    }
    tip.hidden = false;

    // Both boxes are viewport rects, so the difference is independent of how
    // far the page has been scrolled and the tip stays glued to the pill.
    var box = state.root.getBoundingClientRect();
    var rect = pill.getBoundingClientRect();
    var width = tip.offsetWidth;
    var left = rect.left - box.left + (rect.width - width) / 2;
    var limit = Math.max(6, state.root.clientWidth - width - 6);
    tip.style.left = Math.max(6, Math.min(left, limit)) + "px";
    tip.style.top = rect.top - box.top + "px";
  }

  function hideTip() {
    var tip = $('[data-role="tip"]');
    state.tipFor = null;
    state.tipKeyboard = false;
    if (tip) {
      tip.hidden = true;
    }
  }

  /**
   * Re-decide whether the tip should still be showing, from the pointer.
   *
   * A tip used to be able to outlive the pill it described. Toggling a
   * reaction rebuilds the pill row, and replacing a node does not fire
   * `mouseout` on it — nothing removes the element the pointer is over in a way
   * the browser reports, so the tip simply stayed on screen describing a pill
   * that no longer existed.
   *
   * Asking the document what is under the pointer answers the question the
   * event handlers were trying to answer, and does it after the fact: if that is
   * still a pill, its text and position are refreshed; if it is not, the tip
   * goes away. Called after every rebuild of a reaction row, and on every
   * pointer move while a tip is up.
   */
  function resyncTip() {
    var current = state.tipFor;
    if (!current) {
      return;
    }
    if (state.tipKeyboard) {
      // Opened by focus, so the pointer's position says nothing about it.
      if (!current.isConnected || !current.matches(":focus")) {
        hideTip();
      }
      return;
    }
    if (state.tipX == null) {
      // No pointer has been seen (a touch device, or a programmatic toggle).
      if (!current.isConnected) {
        hideTip();
      }
      return;
    }
    var under = document.elementFromPoint(state.tipX, state.tipY);
    var pill = under && under.closest ? under.closest("[data-tip]") : null;
    if (pill) {
      showTip(pill);
    } else {
      hideTip();
    }
  }

  /* ------------------------------------------------------------------ icons
     Taken from Material's own set (`material/templates/.icons/material/`) and
     inlined, so the widget needs no icon font, no extra request and no runtime
     theme lookup. Each is a single `currentColor` path — something an emoji
     glyph can never be, which is why the stats used to ignore the palette.

     `scripts/test_icons.py` re-reads the installed theme and compares these
     token by token, so drift is caught rather than ignored.

     Each path is ONE literal on purpose. Do not re-wrap these into `+`
     concatenations for line length: SVG path data uses whitespace as a
     *separator*, so a split at the wrong point silently merges two numbers
     (`c5 0 9.27` becomes `c5 09.27`), which renders as the wrong glyph and is
     invisible in the source. All three are the `-outline` variants, matching
     the theme's line weight. */
  var ICON_PATHS = {
    // The only filled variant here. The others sit on the page as strokes and
    // match the theme's line weight; this one is a glyph on a solid disc, where
    // an outline reads as a thin, half-erased mark.
    account:
      "M12 4a4 4 0 0 1 4 4 4 4 0 0 1-4 4 4 4 0 0 1-4-4 4 4 0 0 1 4-4m0 10c4.42 0 8 1.79 8 4v2H4v-2c0-2.21 3.58-4 8-4",
    emoticon:
      "M12 17.5c2.33 0 4.3-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5M8.5 11A1.5 1.5 0 0 0 10 9.5 1.5 1.5 0 0 0 8.5 8 1.5 1.5 0 0 0 7 9.5 1.5 1.5 0 0 0 8.5 11m7 0A1.5 1.5 0 0 0 17 9.5 1.5 1.5 0 0 0 15.5 8 1.5 1.5 0 0 0 14 9.5a1.5 1.5 0 0 0 1.5 1.5M12 20a8 8 0 0 1-8-8 8 8 0 0 1 8-8 8 8 0 0 1 8 8 8 8 0 0 1-8 8m0-18C6.47 2 2 6.5 2 12a10 10 0 0 0 10 10 10 10 0 0 0 10-10A10 10 0 0 0 12 2",
    views:
      "M12 9a3 3 0 0 1 3 3 3 3 0 0 1-3 3 3 3 0 0 1-3-3 3 3 0 0 1 3-3m0-4.5c5 0 9.27 3.11 11 7.5-1.73 4.39-6 7.5-11 7.5S2.73 16.39 1 12c1.73-4.39 6-7.5 11-7.5M3.18 12a9.821 9.821 0 0 0 17.64 0 9.821 9.821 0 0 0-17.64 0",
    comments:
      "M9 22a1 1 0 0 1-1-1v-3H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6.1l-3.7 3.71c-.2.19-.45.29-.7.29zm1-6v3.08L13.08 16H20V4H4v12zM6 7h12v2H6zm0 4h9v2H6z",
    markdown:
      "M20.56 18H3.44C2.65 18 2 17.37 2 16.59V7.41C2 6.63 2.65 6 3.44 6h17.12c.79 0 1.44.63 1.44 1.41v9.18c0 .78-.65 1.41-1.44 1.41M6.81 15.19v-3.66l1.92 2.35 1.92-2.35v3.66h1.93V8.81h-1.93l-1.92 2.35-1.92-2.35H4.89v6.38zM19.69 12h-1.92V8.81h-1.92V12h-1.93l2.89 3.28z"
  };

  /**
   * Material wraps icons in `.md-icon` and lets the theme tint the `svg`, so
   * reusing that class makes ours indistinguishable from the theme's own.
   * Decorative here — the surrounding control always carries the label.
   */
  function icon(name) {
    return (
      '<span class="md-icon" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" fill="currentColor">' +
      '<path d="' + ICON_PATHS[name] + '"/>' +
      "</svg></span>"
    );
  }

  /**
   * One template for root comments and replies alike — the only differences
   * are the nested list and the `@mention` context a reply carries.
   */
  function commentHtml(comment, replies) {
    var deleted = comment.deleted;
    // A one-time delete token this browser still holds for the comment. Not how
    // ownership is decided any more — the server compares source addresses (see
    // the delete button below) — but it is the fallback that keeps a comment
    // deletable after the reader moved to another network.
    var token = tokens()[comment.id];
    // Whether the server recognised this comment as the reader's own, by the
    // same address rule. This is what the "我" badge shows: it used to come from
    // the stored token, which was wrong once identity became the address — a
    // reader who cleared site data still owns their comments, and one who
    // inherited a profile did not write them.
    var isMine = !!comment.is_mine;

    // The source toggle sits at the far end of the meta row, which is the
    // comment's top-right corner. It is offered for every published comment
    // rather than only for ones that "look like Markdown": deciding that would
    // need a heuristic, and a control that comes and goes is harder to learn
    // than one that is always in the same place. It is also how the source gets
    // copied — plain text included.
    //
    // `sourceOpen` is read here so a rebuild keeps the view the reader chose.
    var sourceOpen = !!state.sourceOpen[comment.id];
    var sourceToggle = deleted
      ? ""
      : '<button type="button" class="md-comment__source-toggle' +
        (sourceOpen ? " is-active" : "") +
        '" data-act="source" aria-pressed="' + (sourceOpen ? "true" : "false") +
        '" title="' + attr(sourceOpen ? t("showRendered") : t("showSource")) +
        '" aria-label="' + attr(sourceOpen ? t("showRendered") : t("showSource")) +
        '">' + icon("markdown") + "</button>";

    var meta = '<span class="md-comment__author">' + esc(comment.author) + "</span>";
    // The address is only sent when the deployment publishes it, and it is only
    // worth a badge when it says something the name does not: under
    // `default_author: ip` the two are the same string.
    if (comment.author_ip && comment.author_ip !== comment.author) {
      meta +=
        '<span class="md-comment__ip" title="' + attr(t("authorIp")) + '">' +
        esc(comment.author_ip) +
        "</span>";
    }
    if (!deleted && comment.reply_to) {
      meta +=
        '<span class="md-comment__reply-to">↩ ' + esc(comment.reply_to) + "</span>";
    }
    if (isMine) {
      meta += '<span class="md-comment__badge">' + esc(t("you")) + "</span>";
    }
    meta += timeHtml(comment.created_at) + sourceToggle;

    // Both views ship in the markup and the toggle flips which one is `hidden`.
    // Building the source lazily would mean looking the comment up in
    // `state.comments` and re-rendering on click, which would close any open
    // reply box in the thread; flipping an attribute cannot disturb anything.
    var body = deleted
      ? '<p class="md-comment__gone">' + esc(t("deletedComment")) + "</p>"
      : '<div class="md-typeset md-comment__body" data-role="body"' +
        (sourceOpen ? " hidden" : "") +
        ">" +
        (comment.content_html || "") +
        "</div>" +
        // `<pre>` rather than a styled div: the point of this view is to show
        // the source *exactly* as typed, and only `<pre>` preserves the
        // meaningful whitespace in a list, a code fence or an indented block
        // without a single CSS rule of our own.
        '<pre class="md-comment__source" data-role="source"' +
        (sourceOpen ? "" : " hidden") +
        ">" +
        esc(comment.content || "") +
        "</pre>";

    var actions = "";
    if (!deleted) {
      // Reaction pills only appear once somebody reacted. Rendering the full
      // palette on every comment was the single biggest source of visual
      // noise — an untouched comment now carries just two lightweight actions.
      var counts = comment.reactions || {};
      var hasReactions = Object.keys(counts).some(function (emoji) {
        return counts[emoji] > 0;
      });
      actions +=
        '<div class="md-comment__reactions" data-role="reactions"' +
        (hasReactions ? "" : " hidden") +
        ">" +
        (hasReactions
          ? reactionRowHtml(counts, comment.my_reactions, "react", null, true, comment.reaction_users)
          : "") +
        "</div>";
      if (CFG.emojiPicker.length) {
        actions +=
          '<button type="button" class="md-comment__action md-comment__action--icon" ' +
          'data-act="emoji" title="' + attr(t("addReaction")) + '" aria-label="' +
          attr(t("addReaction")) + '">' + icon("emoticon") + "</button>";
      }
      actions +=
        '<button type="button" class="md-comment__action" data-act="reply">' +
        esc(t("reply")) + "</button>";
      // The server decides this from the request's address, and says so in
      // `can_delete`. The browser no longer infers it from the nickname, nor
      // relies solely on a token it may have cleared — but a token it still
      // holds keeps working, because the server accepts either.
      if (CFG.allowDelete && (comment.can_delete || token)) {
        actions +=
          '<button type="button" class="md-comment__action md-comment__action--danger" ' +
          'data-act="delete">' + esc(t("remove")) + "</button>";
      }
    }

    var nested = "";
    if (replies && replies.length) {
      nested = '<ul class="md-comment__replies">';
      replies.forEach(function (reply) {
        nested += commentHtml(reply);
      });
      nested += "</ul>";
    }

    return (
      '<li class="md-comment__item' + (deleted ? " is-deleted" : "") +
      '" data-id="' + attr(comment.id) + '" data-thread="' + attr(comment.thread_id) + '">' +
      avatarHtml(comment.author, comment.anonymous) +
      '<div class="md-comment__main">' +
      '<div class="md-comment__meta">' + meta + "</div>" +
      body +
      '<div class="md-comment__actions">' + actions +
      // The picker lives inside the action row so it can be absolutely
      // positioned against it — opening it must not reflow the thread.
      '<div class="md-comment__picker" data-role="picker" hidden></div>' +
      "</div>" +
      '<div class="md-comment__reply-slot"></div>' +
      nested +
      "</div></li>"
    );
  }

  function fullDate(value) {
    var date = parseDate(value);
    if (!date) {
      return "";
    }
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    return (
      date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) + ":" + pad(date.getMinutes())
    );
  }

  /**
   * The `<time>` block. `time_style: absolute` swaps the visible text for a
   * fixed `YYYY-MM-DD HH:MM` stamp; the `title` always carries the other form.
   */
  function timeHtml(value) {
    var absolute = fullDate(value);
    var relative = timeAgo(value);
    return (
      '<time class="md-comment__time" datetime="' + attr(value) + '" title="' +
      attr(CFG.timeStyle === "absolute" ? relative : absolute) + '">' +
      esc(CFG.timeStyle === "absolute" ? absolute : relative) + "</time>"
    );
  }

  function statsHtml() {
    if (!CFG.showStats) {
      return "";
    }
    var stats = state.stats || { views: 0, comments: 0, reactions: {}, my_reactions: [] };
    // Labels are wrapped rather than left as bare text nodes so the pill's flex
    // `gap` spaces icon, number and word evenly.
    var html =
      '<span class="md-comment__stat">' + icon("views") +
      '<strong data-role="views">' + (stats.views || 0) + "</strong>" +
      '<span>' + esc(t("views")) + "</span></span>" +
      '<span class="md-comment__stat">' + icon("comments") +
      '<strong data-role="total">' + (stats.comments || 0) + "</strong>" +
      '<span>' + esc(t("comments")) + "</span></span>";

    if (CFG.showPageReactions && CFG.pageReactions.length) {
      html +=
        '<span class="md-comment__divider" aria-hidden="true"></span>' +
        reactionRowHtml(
          stats.reactions,
          stats.my_reactions,
          "page-react",
          CFG.pageReactions,
          false,
          stats.reaction_users
        );
    }
    return html;
  }

  function formHtml(options) {
    var isReply = !!(options && options.small);
    // A reply box is one line shorter than the main one; both stay usable even
    // if `editor_rows` is configured to something silly.
    var rows = isReply ? Math.max(2, CFG.editorRows - 1) : Math.max(2, CFG.editorRows);
    return (
      '<form class="md-comment__form' + (isReply ? " md-comment__form--reply" : "") + '" novalidate>' +
      // The composer reads as a single control: the prose area on top, and one
      // bar underneath holding identity, the emoji trigger and the buttons.
      // The Markdown note lives in the textarea placeholder rather than in this
      // bar: it was printed in both places, which said the same thing twice.
      '<textarea class="md-comment__input" name="content" rows="' + rows + '" ' +
      'maxlength="' + CFG.maxContentLength + '" ' +
      'placeholder="' + attr(t("contentPlaceholder")) + '" data-role="content"></textarea>' +
      '<div class="md-comment__bar">' +
      // A <label> wrapping the control associates the two without needing ids,
      // which matters because reply forms are created dynamically.
      '<label class="md-comment__identity">' +
      '<span class="md-comment__label">' + esc(t("author")) + "</span>" +
      '<input type="text" class="md-comment__field" name="author" ' +
      'maxlength="' + CFG.maxAuthorLength + '" autocomplete="nickname" ' +
      'placeholder="' + attr(t("authorPlaceholder", { name: CFG.anonymousName })) + '" ' +
      'value="' + attr(rememberedAuthor()) + '" data-role="author">' +
      "</label>" +
      (CFG.emojiPicker.length
        ? '<button type="button" class="md-comment__emoji" data-act="emoji" title="' +
          attr(t("addReaction")) + '" aria-label="' + attr(t("addReaction")) + '">' +
          icon("emoticon") + "</button>"
        : "") +
      // Empty and hidden, but always present: `setHint` fills it in when a
      // submission is rejected, and validation must never have nowhere to go.
      '<span class="md-comment__hint" data-role="hint" hidden></span>' +
      (isReply
        ? '<button type="button" class="md-button" data-act="cancel">' + esc(t("cancel")) + "</button>"
        : "") +
      '<button type="button" class="md-button" data-act="preview">' + esc(t("preview")) + "</button>" +
      // `type="button"`, so pressing Enter anywhere in the form cannot submit
      // it: the only way to post is to press this button.
      //
      // A reply's primary action is labelled `回复`, not `发表评论`: it is the
      // thing the reader just asked for, and at phone widths the shorter label
      // is also what keeps the row from overflowing.
      '<button type="button" class="md-button md-button--primary" data-act="submit" ' +
      'data-role="submit">' + esc(t(isReply ? "reply" : "submit")) + "</button>" +
      // The picker is a child of the bar so it can be pinned to it as an
      // overlay. In the flow it used to push the whole composer taller, which
      // is exactly what the emoji trigger is meant to avoid.
      '<div class="md-comment__picker" data-role="picker" hidden></div>' +
      "</div>" +
      '<div class="md-comment__preview" data-role="preview" hidden></div>' +
      "</form>"
    );
  }

  /* ====================================================================== *
   * rendering
   * ====================================================================== */
  function renderShell() {
    state.root.innerHTML =
      // The count deliberately lives only in the stats bar below: showing it
      // here too duplicated the same number a few pixels apart.
      '<div class="md-comment__head">' +
      '<h2 class="md-comment__title">' + esc(CFG.title) + "</h2>" +
      "</div>" +
      '<div class="md-comment__stats" data-role="stats"></div>' +
      formHtml() +
      '<div class="md-comment__list-wrap" data-role="list">' +
      '<div class="md-comment__skeleton"></div>' +
      '<div class="md-comment__skeleton"></div>' +
      "</div>" +
      '<div class="md-comment__more" data-role="more"></div>' +
      '<div class="md-comment__empty" data-role="empty" hidden>' + esc(t("empty")) + "</div>" +
      // One tooltip for the whole widget: pills only carry `data-tip`, and
      // the handler positions this node next to whichever one is hovered.
      '<div class="md-comment__tip" data-role="tip" hidden></div>';
  }

  function renderStats() {
    var stats = $('[data-role="stats"]');
    if (stats) {
      stats.innerHTML = statsHtml();
      // Same reason as `refreshReactions`: the page-level pills were replaced.
      resyncTip();
    }
  }

  function renderList() {
    // The pills this tooltip describes are about to be replaced.
    hideTip();
    var wrap = $('[data-role="list"]');
    var more = $('[data-role="more"]');
    var empty = $('[data-role="empty"]');
    if (!wrap) {
      return;
    }

    // Drop source-view state for comments that are no longer in the list —
    // deleted threads, or a reload that replaced the whole page. Done here
    // rather than in each of those paths because this is the one place that
    // knows what the list now contains.
    var present = {};
    state.comments.forEach(function (comment) {
      present[comment.id] = true;
    });
    Object.keys(state.sourceOpen).forEach(function (id) {
      if (!present[id]) {
        delete state.sourceOpen[id];
      }
    });

    // Rebuild the tree that the API returns as a flat, page-ordered list.
    var roots = [];
    var replies = {};
    state.comments.forEach(function (comment) {
      if (comment.thread_id === comment.id) {
        roots.push(comment);
      } else {
        (replies[comment.thread_id] = replies[comment.thread_id] || []).push(comment);
      }
    });

    if (empty) {
      empty.hidden = roots.length > 0;
    }
    // The API returns roots newest-first; `oldest` renders the loaded set
    // chronologically instead. Pagination still walks backwards in time, so
    // "load more" appends older comments into the earlier positions.
    if (CFG.sortOrder === "oldest") {
      roots.reverse();
    }
    if (!roots.length) {
      wrap.innerHTML = "";
    } else {
      var html = '<ul class="md-comment__list">';
      roots.forEach(function (root) {
        html += commentHtml(root, replies[root.id]);
      });
      wrap.innerHTML = html + "</ul>";
    }

    if (more) {
      more.innerHTML = state.hasMore
        ? '<button type="button" class="md-button" data-act="more">' + esc(t("more")) + "</button>"
        : "";
    }
    renderStats();
  }

  function renderMessage(message, action) {
    var wrap = $('[data-role="list"]');
    if (!wrap) {
      return;
    }
    wrap.innerHTML =
      '<div class="md-comment__notice">' + esc(message) +
      (action
        ? '<button type="button" class="md-button" data-act="' + action + '">' +
          esc(t(action === "retry" ? "retry" : "more")) + "</button>"
        : "") +
      "</div>";
  }

  /* ====================================================================== *
   * data loading
   * ====================================================================== */
  function load(options) {
    var opts = options || {};
    if (state.loading) {
      return Promise.resolve();
    }
    state.loading = true;
    var offset = opts.append ? state.offset : 0;
    if (!opts.append) {
      renderMessage(t("loading"));
    }

    return request(
      "/comments?page=" + encodeURIComponent(state.page) +
      "&limit=" + encodeURIComponent(CFG.perPage) +
      "&offset=" + encodeURIComponent(offset) +
      "&visitor_id=" + encodeURIComponent(visitorId)
    )
      .then(function (data) {
        state.comments = opts.append ? state.comments.concat(data.comments) : data.comments;
        state.offset = offset + data.comments.filter(function (comment) {
          return comment.thread_id === comment.id;
        }).length;
        state.hasMore = !!data.has_more;
        state.stats = data.stats;
        renderList();
      })
      .catch(function (error) {
        renderMessage(error.message || t("networkError"), "retry");
      })
      .then(function () {
        state.loading = false;
      });
  }

  /**
   * Let the server own the reaction sets and length limits so a deployment
   * only has to be configured in one place.
   */
  function loadServerConfig() {
    if (!CFG.syncServerConfig) {
      return Promise.resolve();
    }
    return request("/config")
      .then(function (data) {
        if (data.comment_reactions && data.comment_reactions.length) {
          CFG.reactions = data.comment_reactions;
        }
        if (CFG.showPageReactions && data.page_reactions && data.page_reactions.length) {
          CFG.pageReactions = data.page_reactions;
        }
        if (data.emoji_picker && data.emoji_picker.length) {
          CFG.emojiPicker = data.emoji_picker;
        }
        if (CFG.maxContentLength === DEFAULTS.maxContentLength && data.max_content_length) {
          CFG.maxContentLength = data.max_content_length;
        }
        if (data.anonymous_name) {
          CFG.anonymousName = data.anonymous_name;
          // The composer was built before this reply arrived, and the
          // placeholder text embeds the name, so it has to be redrawn.
          $$('[data-role="author"]').forEach(function (input) {
            input.placeholder = t("authorPlaceholder", { name: CFG.anonymousName });
          });
        }
      })
      .catch(function () {
        /* the server config endpoint is optional */
      });
  }

  function loadIdentity() {
    var saved = rememberedAuthor();
    if (saved) {
      return Promise.resolve();
    }
    // In the anonymous modes there is nothing to look up: the name is added
    // server side, and pre-filling it would submit it as if it were typed.
    if (CFG.defaultAuthor === "anonymous") {
      return Promise.resolve();
    }
    return request("/whoami?visitor_id=" + encodeURIComponent(visitorId))
      .then(function (data) {
        if (data && data.author) {
          $$('[data-role="author"]').forEach(function (input) {
            if (!input.value) {
              input.value = data.author;
            }
          });
        }
      })
      .catch(function () {
        /* the field simply stays empty */
      });
  }

  /**
   * Register one page view. Deliberately not de-duplicated client side — a
   * refresh always counts. The server caps per-IP flooding, and a rejection is
   * silently ignored so the displayed number simply stays put.
   */
  function countView() {
    if (!CFG.countViews) {
      return;
    }
    request("/views", { method: "POST", body: { page: state.page } })
      .then(function (data) {
        if (state.stats && typeof data.views === "number") {
          state.stats.views = data.views;
          renderStats();
        }
      })
      .catch(function () {
        /* statistics are best-effort */
      });
  }

  /* ====================================================================== *
   * interactions
   * ====================================================================== */
  function findComment(id) {
    for (var i = 0; i < state.comments.length; i++) {
      if (state.comments[i].id === id) {
        return state.comments[i];
      }
    }
    return null;
  }

  function itemEl(id) {
    return state.root.querySelector('.md-comment__item[data-id="' + id + '"]');
  }

  function closePickers(except) {
    $$('[data-role="picker"]').forEach(function (panel) {
      if (panel !== except) {
        panel.hidden = true;
      }
    });
  }

  /**
   * Anchor a picker to the button that opened it.
   *
   * The panel used to be pinned to its container's left edge. In the composer
   * that put it ~306px away from the trigger, so it read as belonging to the
   * nickname field. Aligning to the trigger is what makes the connection
   * legible; clamping keeps it inside the widget, and the flip keeps it on
   * screen when there is no room below.
   */
  function placePicker(panel, trigger) {
    if (!trigger || !panel.offsetParent) {
      return;
    }
    var container = panel.offsetParent.getBoundingClientRect();
    var rect = trigger.getBoundingClientRect();
    var box = panel.getBoundingClientRect();
    var root = state.root.getBoundingClientRect();

    // Both sides are measured against the containing block, so the result does
    // not depend on how far the page has been scrolled.
    var left = rect.left - container.left;
    var min = root.left - container.left;
    var max = root.right - container.left - box.width;
    panel.style.left = Math.round(Math.max(min, Math.min(left, max))) + "px";

    // Prefer below; only flip over the trigger when the panel would otherwise
    // hang off the bottom of the viewport.
    var roomBelow = window.innerHeight - rect.bottom;
    panel.classList.toggle(
      "md-comment__picker--above",
      roomBelow < box.height + 12 && rect.top > box.height + 12
    );
  }

  function openPicker(panel, onPick, trigger) {
    if (!panel) {
      return;
    }
    var wasOpen = !panel.hidden;
    closePickers(null);
    if (wasOpen) {
      return; // clicking the trigger again closes the panel
    }
    if (!panel.dataset.populated) {
      panel.innerHTML = CFG.emojiPicker
        .map(function (emoji) {
          return '<button type="button" data-emoji="' + attr(emoji) + '">' + esc(emoji) + "</button>";
        })
        .join("");
      panel.dataset.populated = "1";
    }
    // Rebound on every open: the callback depends on the current trigger.
    panel.onclick = function (event) {
      var button = event.target.closest("button[data-emoji]");
      if (button) {
        panel.hidden = true;
        onPick(button.getAttribute("data-emoji"));
      }
    };
    // Unhide before measuring: a hidden element reports a zero-sized box.
    panel.hidden = false;
    placePicker(panel, trigger);
  }

  /** Apply a reaction locally first, then reconcile with the server. */
  function react(targetType, targetId, emoji) {
    toggleLocal(targetType, targetId, emoji);
    request("/reactions", {
      method: "POST",
      body: {
        target_type: targetType,
        target_id: targetId,
        emoji: emoji,
        visitor_id: visitorId,
        // Sent so the tooltip can name the reactor; the server falls back to
        // the caller's IP when this is blank.
        author: currentAuthor()
      }
    })
      .then(function (data) {
        var target = targetType === "page" ? state.stats : findComment(targetId);
        if (target) {
          target.reactions = data.reactions;
          target.my_reactions = data.my_reactions;
          target.reaction_users = data.reaction_users || {};
        }
        refreshReactions(targetType, targetId);
      })
      .catch(function (error) {
        toggleLocal(targetType, targetId, emoji); // roll back
        toast(error.message || t("error"), true);
      });
  }

  /** Keep the tooltip's name list in step with the optimistic count. */
  function toggleLocalActor(target, emoji, adding) {
    var me = currentAuthor();
    if (!me) {
      return;
    }
    var users = (target.reaction_users = target.reaction_users || {});
    var names = (users[emoji] = users[emoji] || []);
    var at = names.indexOf(me);
    if (adding && at === -1) {
      names.push(me);
    } else if (!adding && at !== -1) {
      names.splice(at, 1);
    }
  }

  function toggleLocal(targetType, targetId, emoji) {
    var target = targetType === "page" ? state.stats : findComment(targetId);
    if (!target) {
      return;
    }
    target.reactions = target.reactions || {};
    target.my_reactions = target.my_reactions || [];

    var at = target.my_reactions.indexOf(emoji);
    if (at === -1) {
      target.my_reactions.push(emoji);
      target.reactions[emoji] = (target.reactions[emoji] || 0) + 1;
      toggleLocalActor(target, emoji, true);
    } else {
      target.my_reactions.splice(at, 1);
      target.reactions[emoji] = Math.max((target.reactions[emoji] || 0) - 1, 0);
      toggleLocalActor(target, emoji, false);
    }
    refreshReactions(targetType, targetId);
  }

  function refreshReactions(targetType, targetId) {
    if (targetType === "page") {
      renderStats();
      return;
    }
    var el = itemEl(targetId);
    var comment = findComment(targetId);
    var row = el && el.querySelector('[data-role="reactions"]');
    if (!row || !comment) {
      return;
    }
    // Mirror the initial render: the pill row hides again once the last
    // reaction is removed, so the action row collapses back to two buttons.
    var counts = comment.reactions || {};
    var hasReactions = Object.keys(counts).some(function (emoji) {
      return counts[emoji] > 0;
    });
    row.innerHTML = hasReactions
      ? reactionRowHtml(counts, comment.my_reactions, "react", null, true, comment.reaction_users)
      : "";
    row.hidden = !hasReactions;
    // The pills just above are new nodes, so a tip that was showing for one of
    // them has lost its anchor. This is the case that used to leave it behind.
    resyncTip();
  }

  function setHint(form, message, isError) {
    var hint = form.querySelector('[data-role="hint"]');
    if (!hint) {
      return;
    }
    hint.textContent = message || "";
    hint.classList.toggle("md-comment__hint--error", !!isError);
    // An empty message hides the element, which is its resting state: the
    // composer shows no hint until something needs saying.
    hint.hidden = !message;
  }

  function submitForm(form, parentId) {
    var authorInput = form.querySelector('[data-role="author"]');
    var contentInput = form.querySelector('[data-role="content"]');
    var submitButton = form.querySelector('[data-role="submit"]');
    var author = (authorInput.value || "").trim();
    var content = (contentInput.value || "").trim();

    if (CFG.requireAuthor && !author) {
      setHint(form, t("requiredAuthor"), true);
      authorInput.classList.add("md-comment__field--error");
      authorInput.focus();
      return;
    }
    if (!content) {
      // Not `contentPlaceholder`: that is a long, friendly prompt, and echoing
      // it back as an error reads as though nothing went wrong.
      setHint(form, t("emptyContent"), true);
      contentInput.focus();
      return;
    }
    if (content.length > CFG.maxContentLength) {
      setHint(form, t("tooLong"), true);
      return;
    }

    authorInput.classList.remove("md-comment__field--error");
    submitButton.disabled = true;
    var originalLabel = submitButton.textContent;
    submitButton.textContent = t("submitting");

    request("/comments", {
      method: "POST",
      body: {
        page: state.page,
        author: author || null,
        content: content,
        parent_id: parentId || null,
        visitor_id: visitorId
      }
    })
      .then(function (data) {
        if (author && CFG.rememberAuthor) {
          storage(AUTHOR_KEY, author);
        }
        if (data.delete_token) {
          saveToken(data.comment.id, data.delete_token);
        }
        contentInput.value = "";
        setHint(form, "", false);

        var comment = data.comment;
        if (!parentId) {
          // Keep the optimistic insert on the same side the list is sorted by.
          if (CFG.sortOrder === "oldest") {
            state.comments.push(comment);
          } else {
            state.comments.unshift(comment);
          }
          state.offset += 1;
        } else {
          state.comments.push(comment);
        }
        if (state.stats) {
          state.stats.comments = (state.stats.comments || 0) + 1;
        }
        renderList();
        closeReplyForms();
        highlight(comment.id);
        toast(parentId ? t("replied") : t("posted"));
      })
      .catch(function (error) {
        setHint(form, error.message || t("error"), true);
        toast(error.message || t("error"), true);
      })
      .then(function () {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      });
  }

  function highlight(commentId) {
    var el = itemEl(commentId);
    if (!el) {
      return;
    }
    el.classList.add("is-highlight");
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(function () {
      el.classList.remove("is-highlight");
    }, 1600);
  }

  function openReplyForm(commentId) {
    var comment = findComment(commentId);
    var el = itemEl(commentId);
    var slot = el && el.querySelector(".md-comment__reply-slot");
    if (!slot) {
      return;
    }
    closeReplyForms();

    slot.innerHTML = formHtml({ small: true });
    slot.setAttribute("data-parent", commentId);

    var form = slot.querySelector("form");
    var authorInput = form.querySelector('[data-role="author"]');
    var mainAuthor = state.root.querySelector('.md-comment__form [data-role="author"]');
    if (!authorInput.value && mainAuthor) {
      authorInput.value = mainAuthor.value;
    }

    var contentInput = form.querySelector('[data-role="content"]');
    // Replying to a reply keeps the thread one level deep but quotes the
    // author so the context is not lost. Deployments can turn the quote off.
    if (CFG.replyQuote && comment.thread_id !== comment.id) {
      contentInput.value = "> **@" + comment.author + "** ";
      contentInput.setSelectionRange(contentInput.value.length, contentInput.value.length);
    }
    contentInput.focus();
  }

  function closeReplyForms() {
    $$(".md-comment__reply-slot").forEach(function (slot) {
      slot.innerHTML = "";
      slot.removeAttribute("data-parent");
    });
  }

  /**
   * Swap one comment between its rendered body and its Markdown source.
   *
   * Both nodes are already in the markup, so this only flips what is `hidden`.
   * That is deliberate: rendering the source into the body on demand would mean
   * rebuilding the item, and a rebuild closes the reply box if one is open in
   * this thread, scrolls nowhere and loses the reader's place for what is a
   * pure display change.
   *
   * The button keeps its icon in both states and carries `aria-pressed` plus a
   * label naming the *next* state — the tooltip is the only thing that has to
   * change, and it is also what tells a screen reader what clicking will do.
   */
  function toggleSource(item) {
    var body = item.querySelector('[data-role="body"]');
    var source = item.querySelector('[data-role="source"]');
    var button = item.querySelector('[data-act="source"]');
    if (!body || !source || !button) {
      return;
    }
    // Read the *new* state first and write that everywhere, rather than
    // inverting each thing separately: a comment shows exactly one of the two
    // views, and deriving both from one value is what keeps them consistent.
    var id = item.getAttribute("data-id");
    var open = !state.sourceOpen[id];
    if (open) {
      state.sourceOpen[id] = true;
    } else {
      delete state.sourceOpen[id];
    }

    body.hidden = open;
    source.hidden = !open;

    var label = open ? t("showRendered") : t("showSource");
    button.setAttribute("aria-pressed", open ? "true" : "false");
    button.setAttribute("title", label);
    button.setAttribute("aria-label", label);
    button.classList.toggle("is-active", open);
  }

  function previewToggle(form) {
    var panel = form.querySelector('[data-role="preview"]');
    var input = form.querySelector('[data-role="content"]');
    if (!panel) {
      return;
    }
    if (!panel.hidden) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;

    var content = (input.value || "").trim();
    if (!content) {
      panel.innerHTML = '<p class="md-comment__gone">' + esc(t("previewEmpty")) + "</p>";
      return;
    }
    panel.innerHTML = '<p class="md-comment__gone">' + esc(t("loading")) + "</p>";
    request("/preview", { method: "POST", body: { content: content } })
      .then(function (data) {
        // The same classes as a posted comment, so the preview matches exactly.
        panel.innerHTML =
          '<div class="md-typeset md-comment__body">' + (data.html || "") + "</div>";
      })
      .catch(function () {
        panel.innerHTML = '<p class="md-comment__gone">' + esc(t("error")) + "</p>";
      });
  }

  function removeComment(commentId) {
    var token = tokens()[commentId];
    request("/comments/" + encodeURIComponent(commentId), {
      method: "DELETE",
      headers: token ? { "X-Delete-Token": token } : {}
    })
      .then(function (data) {
        dropToken(commentId);
        var comment = findComment(commentId);
        if (data && data.mode === "hard") {
          // Nothing was attached to it, so the server dropped the row outright
          // and its reactions went with it — the item must leave the list.
          // `removed` can name more than one id: the last reply to go also
          // takes the empty tombstone that was only still there to hold it.
          var gone = data.removed && data.removed.length ? data.removed : [commentId];
          state.comments = state.comments.filter(function (item) {
            return gone.indexOf(item.id) === -1;
          });
          if (state.stats) {
            state.stats.comments = Math.max(0, (state.stats.comments || 0) - gone.length);
          }
        } else {
          if (comment) {
            // A tombstone: the text is gone, but the replies beneath it and the
            // reactions already collected on it deliberately stay visible.
            comment.deleted = true;
            comment.content = "";
            comment.content_html = "";
          }
          if (state.stats && state.stats.comments > 0) {
            state.stats.comments -= 1;
          }
        }
        renderList();
        toast(t("deleted"));
      })
      .catch(function (error) {
        toast(error.message || t("error"), true);
      });
  }

  function insertAtCursor(input, text) {
    var start = input.selectionStart || 0;
    var end = input.selectionEnd || 0;
    var value = input.value || "";
    input.value = value.slice(0, start) + text + value.slice(end);
    var position = start + text.length;
    input.setSelectionRange(position, position);
    input.focus();
  }

  /* ====================================================================== *
   * event wiring
   * ====================================================================== */
  function bind() {
    var root = state.root;

    root.addEventListener("submit", function (event) {
      // Posted through the button only. A form can also be submitted implicitly
      // — pressing Enter in the nickname field, for instance — and that is
      // exactly the accident this prevents, so a submit event is swallowed
      // rather than turned into a comment.
      event.preventDefault();
    });

    root.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-act]");
      if (!trigger || !root.contains(trigger)) {
        return;
      }
      var action = trigger.getAttribute("data-act");
      var form = trigger.closest("form");
      var item = trigger.closest(".md-comment__item");

      switch (action) {
        case "submit": {
          if (form) {
            var slot = form.closest(".md-comment__reply-slot");
            submitForm(form, slot ? slot.getAttribute("data-parent") : null);
          }
          break;
        }
        case "react": {
          if (item) {
            react("comment", item.getAttribute("data-id"), trigger.getAttribute("data-emoji"));
          }
          break;
        }
        case "page-react": {
          react("page", state.page, trigger.getAttribute("data-emoji"));
          break;
        }
        case "emoji": {
          var panel = form
            ? form.querySelector('[data-role="picker"]')
            : item && item.querySelector('[data-role="picker"]');
          if (!panel) {
            break;
          }
          if (form) {
            // Inside a form the picker types the emoji into the text.
            var contentInput = form.querySelector('[data-role="content"]');
            openPicker(panel, function (emoji) {
              if (contentInput) {
                insertAtCursor(contentInput, emoji);
              }
            }, trigger);
          } else {
            // In a reaction row the picker toggles a reaction instead.
            var commentId = item.getAttribute("data-id");
            openPicker(panel, function (emoji) {
              react("comment", commentId, emoji);
            }, trigger);
          }
          break;
        }
        case "preview": {
          if (form) {
            previewToggle(form);
          }
          break;
        }
        case "source": {
          if (item) {
            toggleSource(item);
          }
          break;
        }
        case "reply": {
          if (item) {
            openReplyForm(item.getAttribute("data-id"));
          }
          break;
        }
        case "cancel": {
          closeReplyForms();
          break;
        }
        case "delete": {
          // Two-step inline confirmation instead of a blocking native dialog.
          if (item) {
            if (trigger.dataset.armed) {
              removeComment(item.getAttribute("data-id"));
            } else {
              trigger.dataset.armed = "1";
              trigger.textContent = t("removeConfirm");
              window.setTimeout(function () {
                delete trigger.dataset.armed;
                trigger.textContent = t("remove");
              }, 4000);
            }
          }
          break;
        }
        case "more": {
          load({ append: true });
          break;
        }
        case "retry": {
          load();
          break;
        }
        default:
          break;
      }
    });

    document.addEventListener("click", function (event) {
      if (!event.target.closest(".md-comment")) {
        closePickers(null);
      }
    });

    root.addEventListener("input", function (event) {
      var input = event.target;
      if (input.name === "author") {
        input.classList.remove("md-comment__field--error");
      }
      // Typing dismisses a complaint about the previous attempt.
      var form = input.closest("form");
      if (form) {
        setHint(form, "", false);
      }
    });

    // Store the nickname as soon as the field is left, so somebody who types
    // their name and closes the tab without posting still finds it next time.
    // Submitting also stores it — this only closes the gap in between.
    root.addEventListener("change", function (event) {
      var input = event.target;
      if (input.name === "author" && CFG.rememberAuthor) {
        var value = (input.value || "").trim();
        if (value) {
          storage(AUTHOR_KEY, value);
        }
      }
    });

    // ------------------------------------------------------- reaction tip
    // The pointer's position is kept so `resyncTip` can ask the document what
    // is under it after a rebuild, instead of trusting a stale `state.tipFor`.
    root.addEventListener("mousemove", function (event) {
      state.tipX = event.clientX;
      state.tipY = event.clientY;
      // A tip that is already up re-checks itself on every move. `mouseout` is
      // not enough on its own: when a reaction is toggled the row is rebuilt,
      // and the browser's idea of what the pointer is over is gone with the old
      // node — so no `mouseout` ever arrives and the tip stays on screen after
      // the reader has moved away. Asking the document directly closes that
      // gap, and it costs one hit test only while a tip is showing.
      if (state.tipFor) {
        resyncTip();
      }
    });

    root.addEventListener("mouseover", function (event) {
      var pill = event.target.closest && event.target.closest("[data-tip]");
      if (pill && pill !== state.tipFor) {
        state.tipKeyboard = false;
        showTip(pill);
      }
    });

    root.addEventListener("mouseout", function (event) {
      var pill = event.target.closest && event.target.closest("[data-tip]");
      // Moving between a pill's emoji and its count re-fires `mouseout` on the
      // button itself, so only hide once the pointer really leaves it.
      if (pill && !pill.contains(event.relatedTarget)) {
        hideTip();
      }
    });

    // The pointer leaving the widget altogether is unambiguous, and it is the
    // one case `resyncTip` cannot see: no further move inside the widget
    // arrives to correct a tip the rebuild left behind.
    root.addEventListener("mouseleave", hideTip);

    // The same affordance for keyboard users, since a tooltip that only works
    // on hover is unreachable without a pointer.
    root.addEventListener("focusin", function (event) {
      var pill = event.target.closest && event.target.closest("[data-tip]");
      if (pill) {
        state.tipKeyboard = true;
        showTip(pill);
      }
    });

    root.addEventListener("focusout", function (event) {
      if (event.target.closest && event.target.closest("[data-tip]")) {
        hideTip();
      }
    });

    root.addEventListener("keydown", function (event) {
      // Escape dismisses transient UI. Nothing here submits: a comment is sent
      // by pressing the button, so no keystroke can post one by accident —
      // including the newline Enter inserts in the textarea.
      if (event.key === "Escape") {
        closeReplyForms();
        closePickers(null);
        hideTip();
      }
    });
  }

  /* ====================================================================== *
   * bootstrap
   * ====================================================================== */
  function mount() {
    // The MkDocs plugin appends a host element to the pages that opted in via
    // their metadata, and it carries the canonical page key. The host is the
    // *only* thing that decides whether a comment section exists at all, which
    // is what makes the per-page front matter authoritative.
    //
    // In particular there is no fallback selector that could mount without a
    // host: a CSS selector runs on every page it matches, so "mount wherever
    // this matches" is indistinguishable from "mount everywhere" — it silently
    // opted whole sites in, and the page metadata looked like it was doing
    // nothing. A page that wants comments says so in its own front matter.
    var host = document.querySelector(".md-comment-host");
    if (!host) {
      return;
    }
    var page = host.getAttribute("data-page") || currentPage();

    // `page_selector` only chooses *where* inside an opted-in page the widget
    // renders; it can no longer choose *whether*. Useful when the host lands at
    // the end of the prose but the comments belong in a dedicated container.
    var inside = CFG.pageSelector ? document.querySelector(CFG.pageSelector) : null;
    var container = inside || host;
    var existing = container.querySelector(".md-comment");

    if (existing && state.mountedFor === page && existing === state.root) {
      return; // same page re-rendered by instant loading
    }
    if (existing) {
      existing.parentNode.removeChild(existing);
    }

    var root = document.createElement("div");
    root.className = "md-comment";
    root.id = "md-comment";
    root.setAttribute("data-page", page);
    container.appendChild(root);

    state.root = root;
    state.page = page;
    state.comments = [];
    state.offset = 0;
    state.hasMore = false;
    state.stats = null;
    state.loading = false;
    state.mountedFor = page;

    applyTheme(root);
    renderShell();
    bind();
    countView();

    loadServerConfig().then(function () {
      loadIdentity();
      return load();
    });
  }

  function start() {
    mount();
  }

  // Material for MkDocs exposes `document$`, which emits again on every instant
  // navigation. Fall back to plain DOM events for other themes.
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(start);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.MkdocsComment = {
    reload: function () {
      state.mountedFor = null;
      start();
    },
    config: CFG,
    state: state
  };
})();
