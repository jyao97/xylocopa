"""Bookmarked messages — long-press a chat bubble to save it.

Each bookmark stores: the message id, an optional user note, and a 4o-mini
generated summary (with emoji). Media references (image/file paths) are
extracted from the message and ±2 neighbors and cached as `media_json`.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime

from pydantic import BaseModel
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from route_helpers import get_project_or_404
from models import (
    Agent, BookmarkedMessage, Message, MessageRole,
)

logger = logging.getLogger(__name__)

# Set during lifespan in main.py; needed because these endpoints are
# sync `def` and run in AnyIO's thread pool, which has no event loop —
# so asyncio.ensure_future fails. Use run_coroutine_threadsafe instead.
_main_event_loop: asyncio.AbstractEventLoop | None = None


def _emit_ws(coro):
    """Schedule a WS coroutine onto the main event loop from a worker thread."""
    loop = _main_event_loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        coro.close()  # avoid "coroutine was never awaited" warning

router = APIRouter()


# ===========================================================================
# Pydantic schemas
# ===========================================================================

class BookmarkOut(BaseModel):
    message_id: str
    agent_id: str
    project: str
    user_note: str | None
    summary: str | None
    summary_emoji: str | None
    media: list[dict]
    kind: str
    created_at: datetime
    # Display helpers — derived
    title: str
    body: str
    agent_name: str | None


class BookmarkUpdate(BaseModel):
    user_note: str | None = None


# ===========================================================================
# Media extraction (from meta_json + content)
# ===========================================================================

_ATTACHED_RE = re.compile(r"\[Attached file:\s*([^\]]+)\]")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")


def _extract_media_from_message(msg: Message) -> list[dict]:
    """Pull out image/file references from a single message.

    Returns a list of {kind: "image"|"file", path: str, source: "tool"|"attachment"}.
    """
    out: list[dict] = []

    # User-attached files (in content or meta_json display_content)
    text_blob = msg.content or ""
    if msg.meta_json:
        try:
            meta = json.loads(msg.meta_json)
            text_blob += "\n" + (meta.get("display_content") or "")
            # Tool-call paths
            for inter in (meta.get("interactive") or []):
                ti = inter.get("tool_input") or {}
                fp = ti.get("file_path") or ti.get("path")
                if fp:
                    kind = "image" if str(fp).lower().endswith(_IMAGE_EXT) else "file"
                    out.append({"kind": kind, "path": str(fp), "source": "tool"})
        except (json.JSONDecodeError, AttributeError):
            pass

    for m in _ATTACHED_RE.finditer(text_blob):
        path = m.group(1).strip()
        kind = "image" if path.lower().endswith(_IMAGE_EXT) else "file"
        out.append({"kind": kind, "path": path, "source": "attachment"})

    # Dedupe by path while preserving order
    seen = set()
    unique = []
    for item in out:
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        unique.append(item)
    return unique


def _collect_neighborhood_media(db: Session, msg: Message, span: int = 2) -> list[dict]:
    """Get media from msg + N messages before/after in the same agent's stream."""
    neighbors = (
        db.query(Message)
        .filter(Message.agent_id == msg.agent_id)
        .filter(Message.created_at >= msg.created_at)
        .order_by(Message.created_at.asc())
        .limit(span + 1)
        .all()
    )
    before = (
        db.query(Message)
        .filter(Message.agent_id == msg.agent_id)
        .filter(Message.created_at < msg.created_at)
        .order_by(Message.created_at.desc())
        .limit(span)
        .all()
    )
    all_msgs = list(reversed(before)) + neighbors
    media: list[dict] = []
    seen_paths = set()
    for m in all_msgs:
        for item in _extract_media_from_message(m):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            media.append(item)
    return media


def _classify_kind(msg: Message, media: list[dict]) -> str:
    """Decide if a bookmark should display as message / image / file based on content."""
    if not msg.content or not msg.content.strip():
        # Empty body — likely just an attachment or tool result
        if any(m["kind"] == "image" for m in media):
            return "image"
        if media:
            return "file"
    return "message"


# ===========================================================================
# Summarizer (background task, gpt-4o-mini)
# ===========================================================================

_BOOKMARK_SYSTEM = """You read a small slice of an agent's conversation centered on
ONE bookmarked message and produce a tab-label-style title.

ALWAYS respond in English regardless of the source language.

Goal: the shortest phrase that uniquely identifies this bookmark when
the user scans a list. Think browser tab, not sentence.

Hard limits:
- 3 to 6 words
- under 40 characters (aim for 25-35)
- no trailing period
- noun phrase or terse past-tense verb phrase
- no filler: drop "the", "a", "discussion of", "notes on", "user asked"

Use the surrounding context to pick the *specific* anchor (a number,
file, name, decision) — don't restate the whole message.

Good titles:
  "worktree CWD startswith fix"
  "p99 −38% after PR #142"
  "stash unlinked sessions"
  "rejected masonry layout"
  "TruFor A2_det cross-model table"

Bad titles (avoid):
  "Discussion about the bug"          (vague + filler)
  "Fixed a problem with CWD matching" (too long, no anchor)
  "Yes, that approach works."         (trailing period, no anchor)

Pick an emoji that matches the content:
- bug fix: 🐛 🔧
- ship / done: 🚀 🎉
- decision / call: 🎯 ✅
- research / idea: 💡 🔍
- design / UI: 🎨 ✨
- data / metric: 📊 📈
- docs / write: 📝
- file / code: 📄 💾
"""


async def _call_llm_for_bookmark(snapshot: dict) -> tuple[str | None, str | None]:
    """Returns (emoji, summary). Mirrors _call_llm_for_hint pattern."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.warning("openai package missing — skipping bookmark summary")
        return None, None

    parts = []
    if snapshot.get("task"):
        parts.append(f"AGENT TASK:\n  {snapshot['task']}")
    parts.append(f"BOOKMARKED MESSAGE (the focus):\n  {snapshot['target']}")
    if snapshot.get("context"):
        parts.append(
            "SURROUNDING CONTEXT (a few neighbors, chronological):\n"
            + "\n".join(f"  {t}" for t in snapshot["context"])
        )
    parts.append(
        "Produce a 3-6 word tab-label title for the BOOKMARKED MESSAGE. "
        "Use the surrounding context to pick the most specific anchor "
        "(a number, file, name, or decision) instead of restating the message."
    )
    user_prompt = "\n\n".join(parts)

    client = AsyncOpenAI(api_key=api_key)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _BOOKMARK_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "bookmark_summary",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string", "maxLength": 50},
                                "emoji": {"type": "string", "maxLength": 8},
                            },
                            "required": ["summary", "emoji"],
                            "additionalProperties": False,
                        },
                    },
                },
                max_tokens=120,
                temperature=0.3,
            ),
            timeout=10.0,
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("emoji"), data.get("summary")
    except Exception as e:
        logger.warning("bookmark summary LLM failed: %s", e)
        return None, None


def _build_summary_snapshot(db: Session, msg: Message, span: int = 2) -> dict:
    """Collect target message + ±span neighbors as text snapshot."""
    def _clip(t: str, n: int = 400) -> str:
        t = " ".join((t or "").split())
        return t[:n] + "…" if len(t) > n else t

    target = _clip(msg.content or "", 600)

    before = (
        db.query(Message)
        .filter(Message.agent_id == msg.agent_id)
        .filter(Message.created_at < msg.created_at)
        .filter(Message.role.in_([MessageRole.USER, MessageRole.AGENT]))
        .order_by(Message.created_at.desc())
        .limit(span)
        .all()
    )
    after = (
        db.query(Message)
        .filter(Message.agent_id == msg.agent_id)
        .filter(Message.created_at > msg.created_at)
        .filter(Message.role.in_([MessageRole.USER, MessageRole.AGENT]))
        .order_by(Message.created_at.asc())
        .limit(span)
        .all()
    )

    def _label(m: Message) -> str:
        role = "user" if m.role == MessageRole.USER else "agent"
        return f"{role}: {_clip(m.content or '')}"

    context = [_label(m) for m in reversed(before) if m.content] + \
              [_label(m) for m in after if m.content]

    agent = db.get(Agent, msg.agent_id)
    task = (agent.name or "").strip() if agent else ""
    if task.startswith("You are ") or len(task) > 200:
        task = ""

    return {"task": _clip(task, 200), "target": target, "context": context}


_summary_in_flight: set[str] = set()


async def _summarize_bookmark(message_id: str):
    """Background task: generate summary + emoji for a fresh bookmark."""
    if message_id in _summary_in_flight:
        return
    _summary_in_flight.add(message_id)
    try:
        db = SessionLocal()
        try:
            bm = db.get(BookmarkedMessage, message_id)
            if bm is None:
                return
            msg = db.get(Message, message_id)
            if msg is None:
                return
            snapshot = _build_summary_snapshot(db, msg)
            emoji, summary = await _call_llm_for_bookmark(snapshot)
            if not summary:
                return
            bm.summary = summary
            if emoji:
                bm.summary_emoji = emoji
            db.commit()
        finally:
            db.close()
    finally:
        _summary_in_flight.discard(message_id)


# ===========================================================================
# Helpers
# ===========================================================================

def _to_out(bm: BookmarkedMessage, msg: Message | None, agent: Agent | None) -> BookmarkOut:
    media = []
    if bm.media_json:
        try:
            media = json.loads(bm.media_json)
        except json.JSONDecodeError:
            media = []

    # Title rules:
    #   image kind  → first image filename
    #   file kind   → first file path
    #   message     → first ~50 chars of message body, or summary if body empty
    title = ""
    if bm.kind == "image":
        img = next((m for m in media if m.get("kind") == "image"), None)
        title = (img or {}).get("path", "").split("/")[-1] or "(image)"
    elif bm.kind == "file":
        f = next((m for m in media if m.get("kind") == "file"), None)
        title = (f or {}).get("path", "") or "(file)"
    else:
        body = (msg.content if msg else "") or ""
        body = " ".join(body.split())
        title = body[:60] + ("…" if len(body) > 60 else "") or (bm.summary or "(message)")

    # Body — what shows below the title
    #   priority: user_note > summary > message body
    body_text = bm.user_note or bm.summary or ""
    if not body_text and msg and msg.content and bm.kind != "message":
        body_text = " ".join((msg.content or "").split())[:120]

    return BookmarkOut(
        message_id=bm.message_id,
        agent_id=bm.agent_id,
        project=bm.project,
        user_note=bm.user_note,
        summary=bm.summary,
        summary_emoji=bm.summary_emoji,
        media=media,
        kind=bm.kind,
        created_at=bm.created_at,
        title=title or "(untitled)",
        body=body_text,
        agent_name=(agent.name[:60] if agent and agent.name else None),
    )


# ===========================================================================
# Endpoints
# ===========================================================================

@router.get("/api/projects/{name}/bookmarks", response_model=list[BookmarkOut])
def list_bookmarks(name: str, db: Session = Depends(get_db)):
    get_project_or_404(db, name)

    rows = (
        db.query(BookmarkedMessage)
        .filter(BookmarkedMessage.project == name)
        .order_by(BookmarkedMessage.created_at.desc())
        .all()
    )
    out = []
    for bm in rows:
        msg = db.get(Message, bm.message_id)
        agent = db.get(Agent, bm.agent_id)
        out.append(_to_out(bm, msg, agent))
    return out


@router.post("/api/projects/{name}/messages/{message_id}/bookmark", response_model=BookmarkOut)
def create_bookmark(
    name: str,
    message_id: str,
    background: BackgroundTasks,
    payload: BookmarkUpdate | None = None,
    db: Session = Depends(get_db),
):
    get_project_or_404(db, name)
    msg = db.get(Message, message_id)
    if msg is None:
        raise HTTPException(404, f"Message not found: {message_id}")
    agent = db.get(Agent, msg.agent_id)
    if agent is None or agent.project != name:
        raise HTTPException(400, "Message does not belong to this project")

    from websocket import emit_project_update

    existing = db.get(BookmarkedMessage, message_id)
    if existing:
        # Idempotent: update user_note if provided, return existing
        if payload and payload.user_note is not None:
            existing.user_note = payload.user_note.strip() or None
            db.commit()
            _emit_ws(emit_project_update(name))
        return _to_out(existing, msg, agent)

    media = _collect_neighborhood_media(db, msg)
    kind = _classify_kind(msg, media)

    bm = BookmarkedMessage(
        message_id=message_id,
        agent_id=msg.agent_id,
        project=name,
        user_note=(payload.user_note.strip() if payload and payload.user_note else None) or None,
        summary=None,
        summary_emoji=None,
        media_json=json.dumps(media) if media else None,
        kind=kind,
    )
    db.add(bm)
    db.commit()
    db.refresh(bm)
    _emit_ws(emit_project_update(name))

    # Fire-and-forget LLM summary
    background.add_task(_summarize_bookmark, message_id)

    return _to_out(bm, msg, agent)


@router.patch("/api/projects/{name}/bookmarks/{message_id}", response_model=BookmarkOut)
def update_bookmark(
    name: str,
    message_id: str,
    payload: BookmarkUpdate,
    db: Session = Depends(get_db),
):
    bm = db.get(BookmarkedMessage, message_id)
    if bm is None or bm.project != name:
        raise HTTPException(404, "Bookmark not found")
    # Setting user_note to empty string clears it (back to AI summary)
    if payload.user_note is not None:
        cleaned = payload.user_note.strip()
        bm.user_note = cleaned or None
    db.commit()
    from websocket import emit_project_update
    _emit_ws(emit_project_update(name))
    msg = db.get(Message, message_id)
    agent = db.get(Agent, bm.agent_id)
    return _to_out(bm, msg, agent)


@router.delete("/api/projects/{name}/messages/{message_id}/bookmark")
def delete_bookmark(name: str, message_id: str, db: Session = Depends(get_db)):
    bm = db.get(BookmarkedMessage, message_id)
    if bm is None or bm.project != name:
        return {"deleted": False}
    db.delete(bm)
    db.commit()
    from websocket import emit_project_update
    _emit_ws(emit_project_update(name))
    return {"deleted": True}
