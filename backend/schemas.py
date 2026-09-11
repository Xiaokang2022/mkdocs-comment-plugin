"""Pydantic request / response models of the public API."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------- #
# requests
# --------------------------------------------------------------------- #
class CommentCreate(BaseModel):
    page: str = Field(..., description="页面唯一标识（通常是 pathname）")
    content: str = Field(..., description="Markdown 原文")
    author: Optional[str] = Field(default=None, description="昵称，留空则使用 IP")
    parent_id: Optional[str] = Field(default=None, description="被回复评论的 id")
    visitor_id: Optional[str] = Field(default=None, description="客户端匿名标识")
    # Honeypot: bots love filling hidden fields.
    website: Optional[str] = Field(default=None, description="(反垃圾字段，请留空)")


class ReactionIn(BaseModel):
    target_type: Literal["comment", "page"] = "comment"
    target_id: str
    emoji: str
    visitor_id: Optional[str] = None
    # Stored with the reaction so the UI can list who reacted. Falls back to
    # the caller's IP, exactly like a comment author.
    author: Optional[str] = Field(default=None, description="点赞者昵称，留空则使用 IP")


class ViewIn(BaseModel):
    page: str


class PreviewIn(BaseModel):
    content: str


class DeleteIn(BaseModel):
    token: Optional[str] = None


# --------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------- #
class ReactionSummary(BaseModel):
    reactions: Dict[str, int] = Field(default_factory=dict)
    my_reactions: List[str] = Field(default_factory=list)


class CommentOut(BaseModel):
    id: str
    page: str
    parent_id: Optional[str] = None
    thread_id: str
    reply_to: Optional[str] = None
    author: str
    # The commenter's address, printed beside the name. Absent when the
    # deployment does not publish addresses, or when the author is a named one
    # who does not need identifying.
    author_ip: Optional[str] = None
    # True when `author` is the deployment's anonymous placeholder rather than a
    # name somebody chose; the widget styles and labels it differently.
    anonymous: bool = False
    content: str
    content_html: str
    created_at: str
    updated_at: Optional[str] = None
    deleted: bool = False
    reactions: Dict[str, int] = Field(default_factory=dict)
    my_reactions: List[str] = Field(default_factory=list)
    # emoji -> display names, in the order they reacted (for the hover tooltip).
    reaction_users: Dict[str, List[str]] = Field(default_factory=dict)


class CommentCreated(BaseModel):
    comment: CommentOut
    delete_token: Optional[str] = None


class PageStatsOut(BaseModel):
    page: str
    views: int = 0
    comments: int = 0
    reactions: Dict[str, int] = Field(default_factory=dict)
    my_reactions: List[str] = Field(default_factory=list)
    reaction_users: Dict[str, List[str]] = Field(default_factory=dict)


class CommentListOut(BaseModel):
    page: str
    total: int
    root_total: int
    limit: int
    offset: int
    has_more: bool
    stats: PageStatsOut
    comments: List[CommentOut]


class ReactionOut(BaseModel):
    target_type: str
    target_id: str
    reactions: Dict[str, int] = Field(default_factory=dict)
    my_reactions: List[str] = Field(default_factory=list)
    reaction_users: Dict[str, List[str]] = Field(default_factory=dict)
    active: bool = False


class PreviewOut(BaseModel):
    html: str


class ViewOut(BaseModel):
    page: str
    views: int


class WhoAmIOut(BaseModel):
    ip: str
    author: str
    visitor_id: str


class ServerConfigOut(BaseModel):
    comment_reactions: List[str]
    page_reactions: List[str]
    emoji_picker: List[str]
    max_content_length: int
    max_author_length: int
    allow_delete: bool
    default_author: str
    # Name an empty nickname box resolves to. The widget uses it for the
    # composer placeholder and for the anonymous avatar, so the text it shows
    # cannot disagree with the name the server actually stores.
    anonymous_name: str


class HealthOut(BaseModel):
    status: str
    version: str


class DeletedOut(BaseModel):
    id: str
    deleted: bool = True
    # "hard": the row is gone. "soft": it stays behind as a tombstone so the
    # replies and reactions attached to it survive.
    mode: Literal["soft", "hard"] = "soft"
    # Every id that left the database, which is not always just `id`: removing
    # the last reply of a deleted thread also clears the now-empty tombstone
    # above it, and the client has to drop both.
    removed: List[str] = Field(default_factory=list)
