"""FastAPI application exposing the comment REST API.

Run it with::

    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import schemas
from database import Database
from markdown_render import plain_text, render_markdown
from security import (
    RateLimiter,
    client_ip,
    hash_token,
    make_visitor_id,
    new_delete_token,
    require_admin,
    verify_token,
)
from settings import settings

VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("mkdocs-comment")

# The database is told the anonymous placeholder so it can re-label rows that
# an older version published under the visitor's address, and keep that
# placeholder out of the nicknames it learns. `default_author: ip` means
# addresses really are names here, so the migration is switched off.
db = Database(
    settings.db_path,
    anonymous_name="" if settings.default_author == "ip" else settings.anonymous_name,
)
# Separate buckets: writing content and merely viewing a page have very
# different legitimate rates.
write_limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window)
view_limiter = RateLimiter(settings.view_rate_limit_requests, settings.view_rate_limit_window)

app = FastAPI(
    title="MkDocs Comment Plugin API",
    version=VERSION,
    description="为 mkdocs-comment-plugin 提供评论、表情互动与统计能力。",
    root_path=settings.root_path or "",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


# --------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------- #
def normalize_page(raw: str) -> str:
    page = (raw or "").strip()
    if not page:
        raise HTTPException(status_code=400, detail="page 参数不能为空。")
    if len(page) > settings.max_page_length:
        raise HTTPException(status_code=400, detail="page 参数过长。")
    return page


def resolve_visitor(request: Request, provided: Optional[str]) -> str:
    if provided and 0 < len(provided) <= 128 and provided.isascii():
        return provided
    return make_visitor_id(
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )


def serialize_comment(
    row: Dict[str, Any],
    reactions: Dict[str, int],
    mine: List[str],
    actors: Dict[str, List[str]],
) -> schemas.CommentOut:
    deleted = bool(row.get("deleted_at"))
    content = row.get("content") or ""
    # A root comment has thread_id == id; everything else is a reply, and its
    # `reply_to` is the author of the comment it points at (for the @mention).
    is_reply = row.get("thread_id") != row.get("id")
    reply_to = row.get("parent_author") if is_reply else None
    anonymous = settings.is_anonymous(row["author"])
    # The address is a separate field precisely so that hiding it is possible;
    # once it has been baked into the name there is no taking it back.
    author_ip = row.get("client_ip") if settings.shows_author_ip(anonymous) else None
    return schemas.CommentOut(
        id=row["id"],
        page=row["page"],
        parent_id=row["parent_id"],
        thread_id=row["thread_id"],
        reply_to=reply_to,
        # The author stays on a tombstone on purpose: replies quote it in their
        # `@mention`, so dropping it would break their context.
        author=row["author"],
        author_ip=author_ip,
        anonymous=anonymous,
        content="" if deleted else content,
        content_html="" if deleted else render_markdown(content),
        created_at=row["created_at"],
        updated_at=row.get("updated_at"),
        deleted=deleted,
        # Reactions deliberately survive deletion. The text is gone, but the
        # thread this row anchors is kept — so the reactions already collected
        # on it stay visible rather than silently vanishing.
        reactions=reactions,
        my_reactions=mine,
        reaction_users=actors,
    )


def hydrate(
    rows: List[Dict[str, Any]], visitor_id: str
) -> List[schemas.CommentOut]:
    ids = [row["id"] for row in rows]
    counts = db.reaction_counts("comment", ids)
    mine = db.my_reactions("comment", ids, visitor_id)
    actors = db.reaction_actors("comment", ids)
    return [
        serialize_comment(
            row,
            counts.get(row["id"], {}),
            mine.get(row["id"], []),
            actors.get(row["id"], {}),
        )
        for row in rows
    ]


def page_stats(page: str, visitor_id: str) -> schemas.PageStatsOut:
    counter = db.count_comments(page)
    return schemas.PageStatsOut(
        page=page,
        views=db.get_views(page),
        comments=counter["total"],
        reactions=db.reaction_counts("page", [page]).get(page, {}),
        my_reactions=db.my_reactions("page", [page], visitor_id).get(page, []),
        reaction_users=db.reaction_actors("page", [page]).get(page, {}),
    )


def guard_blocked_words(text: str) -> None:
    if not settings.blocked_words:
        return
    body = plain_text(text).lower()
    for word in settings.blocked_words:
        if word and word.lower() in body:
            raise HTTPException(status_code=400, detail="内容包含被禁止的关键词。")


def guard_honeypot(payload: schemas.CommentCreate) -> None:
    if payload.website:
        # Silently accept-but-drop would be nicer against bots, but an explicit
        # rejection keeps the API honest for legitimate clients.
        raise HTTPException(status_code=400, detail="请求被判定为垃圾内容。")


# --------------------------------------------------------------------- #
# meta endpoints
# --------------------------------------------------------------------- #
@app.get("/api/v1/health", response_model=schemas.HealthOut, tags=["meta"])
def health() -> schemas.HealthOut:
    return schemas.HealthOut(status="ok", version=VERSION)


@app.get("/api/v1/config", response_model=schemas.ServerConfigOut, tags=["meta"])
def server_config() -> schemas.ServerConfigOut:
    """Lets the frontend inherit the reaction sets configured server side."""
    return schemas.ServerConfigOut(
        comment_reactions=settings.comment_reactions,
        page_reactions=settings.page_reactions,
        emoji_picker=settings.emoji_picker,
        max_content_length=settings.max_content_length,
        max_author_length=settings.max_author_length,
        allow_delete=settings.allow_delete,
        default_author=settings.default_author,
        anonymous_name=settings.anonymous_name,
    )


@app.get("/api/v1/whoami", response_model=schemas.WhoAmIOut, tags=["meta"])
def whoami(request: Request, visitor_id: Optional[str] = Query(default=None)) -> schemas.WhoAmIOut:
    """Returns the caller's IP so the frontend can pre-fill the author box.

    ``author`` is empty in the anonymous modes: there is nothing to pre-fill,
    and the frontend must not submit a name on the visitor's behalf.
    """
    ip = client_ip(request, settings)
    return schemas.WhoAmIOut(
        ip=ip,
        author=settings.suggested_author(ip),
        visitor_id=resolve_visitor(request, visitor_id),
    )


# --------------------------------------------------------------------- #
# comments
# --------------------------------------------------------------------- #
@app.get("/api/v1/comments", response_model=schemas.CommentListOut, tags=["comments"])
def list_comments(
    request: Request,
    page: str = Query(..., description="页面唯一标识"),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    visitor_id: Optional[str] = Query(default=None),
) -> schemas.CommentListOut:
    page_key = normalize_page(page)
    visitor = resolve_visitor(request, visitor_id)

    roots = db.list_root_comments(page_key, limit, offset)
    replies = db.list_replies([row["id"] for row in roots])
    comments = hydrate(roots + replies, visitor)

    counter = db.count_comments(page_key)
    return schemas.CommentListOut(
        page=page_key,
        total=counter["total"],
        root_total=counter["root_total"],
        limit=limit,
        offset=offset,
        has_more=offset + len(roots) < counter["root_total"],
        stats=page_stats(page_key, visitor),
        comments=comments,
    )


@app.post(
    "/api/v1/comments",
    response_model=schemas.CommentCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["comments"],
)
def create_comment(payload: schemas.CommentCreate, request: Request) -> schemas.CommentCreated:
    write_limiter.check(client_ip(request, settings))
    guard_honeypot(payload)

    page = normalize_page(payload.page)
    content = (payload.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论内容不能为空。")
    if len(content) > settings.max_content_length:
        raise HTTPException(
            status_code=400,
            detail=f"评论内容超过 {settings.max_content_length} 字限制。",
        )
    guard_blocked_words(content)

    ip = client_ip(request, settings)
    nickname = (payload.author or "").strip()
    author = settings.display_author(nickname, ip)
    if len(author) > settings.max_author_length:
        raise HTTPException(
            status_code=400,
            detail=f"昵称超过 {settings.max_author_length} 字限制。",
        )

    token = new_delete_token() if settings.allow_delete else ""
    visitor = resolve_visitor(request, payload.visitor_id)
    try:
        row = db.create_comment(
            page=page,
            author=author,
            content=content,
            parent_id=payload.parent_id or None,
            delete_token_hash=hash_token(token) if token else "",
            client_ip=ip,
            user_agent=request.headers.get("user-agent", ""),
            visitor_id=visitor,
            visitor_name=nickname[: settings.max_author_length],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    serialized = hydrate([row], visitor)[0]
    logger.info("新评论 %s (%s) 于 %s", row["id"], author, page)
    return schemas.CommentCreated(comment=serialized, delete_token=token or None)


@app.delete(
    "/api/v1/comments/{comment_id}",
    response_model=schemas.DeletedOut,
    tags=["comments"],
)
def delete_comment(
    comment_id: str,
    request: Request,
    x_delete_token: Optional[str] = Header(default=None, alias="X-Delete-Token"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    hard: bool = Query(default=False, description="真实删除（需要管理员令牌）"),
) -> schemas.DeletedOut:
    row = db.get_comment(comment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="评论不存在。")

    is_admin = bool(settings.admin_token) and x_admin_token == settings.admin_token

    if hard:
        require_admin(x_admin_token, settings)
        removed = db.delete_comment_tree(comment_id)
        return schemas.DeletedOut(id=comment_id, mode="hard", removed=removed)

    if row.get("deleted_at"):
        raise HTTPException(status_code=409, detail="评论已被删除。")

    if not settings.allow_delete:
        raise HTTPException(status_code=403, detail="服务端已关闭删除功能。")

    if not is_admin:
        if not verify_token(x_delete_token or "", db.get_delete_token_hash(comment_id) or ""):
            raise HTTPException(status_code=403, detail="没有权限删除该评论。")

    # The trade-off between a tidy thread and a coherent one:
    #   * nothing replies to it -> drop the row outright. Nothing is lost, and
    #     deleting your own comment should not leave a placeholder behind.
    #   * something replies to it -> keep a tombstone. Otherwise the replies
    #     would be orphaned, and the reactions already collected on this
    #     comment would have nowhere left to display.
    if db.comment_has_children(comment_id):
        if not db.soft_delete_comment(comment_id):
            raise HTTPException(status_code=409, detail="评论已被删除。")
        return schemas.DeletedOut(id=comment_id, mode="soft")

    removed = db.delete_comment_only(comment_id)
    if not removed:
        raise HTTPException(status_code=409, detail="评论已被删除。")
    # `removed` can be longer than one: deleting the last reply also clears the
    # tombstone that was only still there to hold it, and the client has to drop
    # both from its list.
    return schemas.DeletedOut(id=comment_id, mode="hard", removed=removed)


@app.post("/api/v1/preview", response_model=schemas.PreviewOut, tags=["comments"])
def preview(payload: schemas.PreviewIn) -> schemas.PreviewOut:
    content = payload.content or ""
    if len(content) > settings.max_content_length * 2:
        raise HTTPException(status_code=400, detail="内容过长，无法预览。")
    return schemas.PreviewOut(html=render_markdown(content))


# --------------------------------------------------------------------- #
# reactions
# --------------------------------------------------------------------- #
@app.post("/api/v1/reactions", response_model=schemas.ReactionOut, tags=["reactions"])
def toggle_reaction(payload: schemas.ReactionIn, request: Request) -> schemas.ReactionOut:
    write_limiter.check(client_ip(request, settings))

    emoji = (payload.emoji or "").strip()
    if not emoji or len(emoji) > 16:
        raise HTTPException(status_code=400, detail="表情不合法。")

    if payload.target_type == "comment":
        allowed = settings.comment_reactions
        target_id = payload.target_id
        row = db.get_comment(target_id)
        if row is None or row.get("deleted_at"):
            raise HTTPException(status_code=404, detail="评论不存在。")
    else:
        allowed = settings.page_reactions
        target_id = normalize_page(payload.target_id)

    if allowed and emoji not in allowed and emoji not in settings.emoji_picker:
        raise HTTPException(status_code=400, detail="不支持的表情。")

    visitor = resolve_visitor(request, payload.visitor_id)
    ip = client_ip(request, settings)
    nickname = (payload.author or "").strip()[: settings.max_author_length]
    try:
        active = db.toggle_reaction(
            payload.target_type,
            target_id,
            emoji,
            visitor,
            display_name=settings.display_author(nickname, ip),
            nickname=nickname,
            client_ip=ip,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return schemas.ReactionOut(
        target_type=payload.target_type,
        target_id=target_id,
        reactions=db.reaction_counts(payload.target_type, [target_id]).get(target_id, {}),
        my_reactions=db.my_reactions(payload.target_type, [target_id], visitor).get(target_id, []),
        reaction_users=db.reaction_actors(payload.target_type, [target_id]).get(target_id, {}),
        active=active,
    )


# --------------------------------------------------------------------- #
# page views
# --------------------------------------------------------------------- #
@app.post("/api/v1/views", response_model=schemas.ViewOut, tags=["stats"])
def add_view(payload: schemas.ViewIn, request: Request) -> schemas.ViewOut:
    """Count a single page view.

    Every request counts (a browser refresh always increments), but a per-IP
    rate limit stops a single client from inflating the number. Clients
    hitting the limit get a ``429`` and should keep their current value.
    """
    page = normalize_page(payload.page)
    view_limiter.check(client_ip(request, settings))
    return schemas.ViewOut(page=page, views=db.increment_view(page))


@app.get("/api/v1/stats", response_model=schemas.PageStatsOut, tags=["stats"])
def get_stats(
    request: Request,
    page: str = Query(..., description="页面唯一标识"),
    visitor_id: Optional[str] = Query(default=None),
) -> schemas.PageStatsOut:
    page_key = normalize_page(page)
    return page_stats(page_key, resolve_visitor(request, visitor_id))


# --------------------------------------------------------------------- #
# error handling
# --------------------------------------------------------------------- #
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all so unexpected errors still carry CORS headers.

    Without this, ``ServerErrorMiddleware`` (which sits above the CORS
    middleware) turns the error into a bare 500 that the browser reports as a
    misleading CORS failure.
    """
    logger.exception("未处理的异常：%s %s", request.method, request.url.path)
    origin = request.headers.get("origin")
    headers = {}
    if origin and ("*" in settings.cors_origins or origin in settings.cors_origins):
        headers["Access-Control-Allow-Origin"] = origin if "*" not in settings.cors_origins else "*"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "服务端内部错误，请稍后再试。", "status": 500},
        headers=headers,
    )


@app.on_event("startup")
def on_startup() -> None:
    db.delete_orphan_reactions()
    logger.info("评论服务已启动，数据库：%s", settings.db_path)
    if settings.cors_origins == ["*"]:
        logger.warning("CORS 允许所有来源，生产环境建议设置 MKC_CORS_ORIGINS。")
