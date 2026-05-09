"""Display file writer — writes per-agent JSONL files for the frontend.

Each agent gets a `data/display/{agent_id}.jsonl` file containing one JSON
line per message, ordered by display_seq.  The frontend reads these files
to render the chat history without querying the DB.

Design:
    - Display file is append-only (rebuild appends new seq block)
    - File write happens BEFORE DB commit (safe: if DB fails, display_seq
      stays NULL, next flush retries; frontend deduplicates by id)
    - File writes use fcntl.flock to prevent interleaved lines

Functions:
    flush_agent            — append undisplayed messages to the display file
    update_last            — append a replacement line for a streaming update
    update_after_metadata_change — append a `_replace` line after meta_json
                                    patch on a delivered message
    pre_sent_create     — append a `_queued + _pre_sent` line (no DB row)
    pre_sent_update     — append a `_queued + _pre_sent + _replace` line
    pre_sent_cancel     — soft-cancel (status='cancelled', still visible)
    pre_sent_tombstone  — hard-delete (append `_deleted: true`)
    pre_sent_promote_to_sent — atomic _pre_sent → sent transition
    pre_sent_list / pre_sent_get — read helpers (in-memory)
    rebuild_agent          — rebuild the display file, preserving _pre_sent entries
    delete_agent           — remove the display file
    startup_rebuild_all    — rebuild all active agents on server startup
"""

import asyncio
import fcntl
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

from sqlalchemy import func, text

from config import _resolve
from database import SessionLocal
from models import Agent, AgentStatus, Message, MessageRole, MessageStatus

logger = logging.getLogger("orchestrator.display_writer")

DISPLAY_DIR = _resolve("data/display")


# ---- Pre-delivery in-memory index ----
#
# Pre-delivery messages (queued / scheduled / cancelled) live exclusively
# in the per-agent display file (no DB row). Until a message is promoted to
# "sent", the authoritative view of it lives here in memory (backed by the
# JSONL file on disk so it survives restart).
#
# `_pre_sent_index[agent_id][msg_id] = entry_dict` — latest state of each
# pre-sent entry. Mutations to the index are guarded by
# `_pre_sent_lock` (a threading.Lock; the existing fcntl.flock on the
# file only guards multi-process append ordering, not in-process index
# writes).
#
# The index is built lazily per agent on first access (see
# `_ensure_index_loaded`) or eagerly during `rebuild_agent` /
# `startup_rebuild_all`.
_pre_sent_index: dict[str, dict[str, dict]] = {}
_pre_sent_index_ready: set[str] = set()
_pre_sent_lock = threading.Lock()


_VALID_PRE_SOURCES = {"web", "task", "plan_continue"}
_VALID_PRE_STATUSES = {"queued", "scheduled", "cancelled"}


_ATTACHMENT_TAG_RE = re.compile(r'\n?\[Attached file: [^\]]+\]')
_ATTACHMENT_PATH_RE = re.compile(r'\[Attached file: ([^\]]+)\]')
_STOP_NOTE_RE = re.compile(r'^(Task dropped|Redo)\s*—\s*(.*)', re.DOTALL)
_TASK_NOTIFICATION_FIELD_RE = {
    f: re.compile(rf'<{f}>([\s\S]*?)</{f}>') for f in ('status', 'summary', 'result')
}


def _display_path(agent_id: str) -> str:
    """Return path: data/display/{agent_id}.jsonl"""
    return os.path.join(DISPLAY_DIR, f"{agent_id}.jsonl")


def transform_for_display(role: str | None, content: str | None,
                          metadata: dict | None) -> tuple[str | None, dict | None]:
    """Apply display transformations so the JSONL/API content matches what
    the UI should render without further frontend processing.

    Returns (content, metadata) — both may be modified copies.

    Transformations:
    - display_content override (e.g. retry agent first message)
    - USER messages: strip [Attached file: ...] tags, store paths in metadata
    - SYSTEM stop notes: strip prefix, store stop_action in metadata
    - Task notifications: parse XML into metadata.task_notification
    """
    if content is None:
        return content, metadata

    # 1. display_content override (already set by _prepare_dispatch)
    if isinstance(metadata, dict) and "display_content" in metadata:
        content = metadata["display_content"]

    # 2. USER: strip attachment tags, store paths
    if role == "USER":
        paths = _ATTACHMENT_PATH_RE.findall(content)
        if paths:
            content = _ATTACHMENT_TAG_RE.sub('', content).strip()
            metadata = dict(metadata) if metadata else {}
            metadata['attachments'] = paths

    # 3. SYSTEM stop notes: "Task dropped — reason" / "Redo — reason"
    if role == "SYSTEM":
        m = _STOP_NOTE_RE.match(content)
        if m:
            metadata = dict(metadata) if metadata else {}
            metadata['stop_action'] = 'dropped' if m.group(1) == 'Task dropped' else 'redo'
            content = m.group(2)

    # 4. Task notifications: parse XML fields into metadata
    if content.lstrip().startswith('<task-notification>'):
        tn = {}
        for field, regex in _TASK_NOTIFICATION_FIELD_RE.items():
            fm = regex.search(content)
            if fm:
                tn[field] = fm.group(1).strip()
        if tn:
            metadata = dict(metadata) if metadata else {}
            metadata['task_notification'] = tn

    return content, metadata


def _prepare_display_fields(msg: Message) -> tuple[str | None, str | None, dict | None]:
    """Parse meta_json, resolve tool_use_id, and apply display transforms.

    Returns (role_val, content, metadata).
    """
    metadata = None
    if msg.meta_json:
        try:
            metadata = json.loads(msg.meta_json)
        except (json.JSONDecodeError, ValueError):
            metadata = None

    # Extract tool_use_id from metadata if present, or use direct column
    tool_use_id = getattr(msg, "tool_use_id", None)
    if not tool_use_id and isinstance(metadata, dict):
        for item in metadata.get("interactive", []):
            if "tool_use_id" in item:
                tool_use_id = item["tool_use_id"]
                break

    role_val = msg.role.value if msg.role else None
    content, metadata = transform_for_display(role_val, msg.content, metadata)

    # Stash resolved tool_use_id back onto msg attr for serializers without
    # re-running the extraction. Non-persisting — just a local attribute.
    msg._resolved_tool_use_id = tool_use_id
    return role_val, content, metadata


def _serialize_message(msg: Message, seq: int, replace: bool = False) -> str:
    """Serialize a delivered Message to a JSON line for the display file.

    Fields: id, seq, role, kind, content, source, status, metadata,
    tool_use_id, created_at, completed_at, delivered_at.
    If replace=True, add "_replace": true.
    """
    role_val, content, metadata = _prepare_display_fields(msg)
    tool_use_id = getattr(msg, "_resolved_tool_use_id", None)

    obj = {
        "id": msg.id,
        "seq": seq,
        "role": role_val,
        "kind": msg.kind if hasattr(msg, "kind") else "message",
        "content": content,
        "source": msg.source,
        "status": msg.status.value if msg.status else None,
        "metadata": metadata,
        "tool_use_id": tool_use_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "completed_at": msg.completed_at.isoformat() if msg.completed_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
    }
    if replace:
        obj["_replace"] = True

    return json.dumps(obj, separators=(",", ":"))


def _serialize_queued(msg: Message, replace: bool = False) -> str:
    """Serialize a pre-sent (queued) Message as a `_queued` JSONL line.

    No `seq` field — queued entries are not part of the main partition.
    Carries the same fields the frontend's queued-bubble render consumes
    from the DB fallback MessageOut: id, role, content, status, source,
    metadata, created_at, scheduled_at, delivered_at (null), tool_use_id,
    kind.
    """
    role_val, content, metadata = _prepare_display_fields(msg)
    tool_use_id = getattr(msg, "_resolved_tool_use_id", None)

    obj = {
        "id": msg.id,
        "_queued": True,
        "role": role_val,
        "kind": msg.kind if hasattr(msg, "kind") else None,
        "content": content,
        "source": msg.source,
        "status": msg.status.value if msg.status else None,
        "metadata": metadata,
        "tool_use_id": tool_use_id,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "scheduled_at": msg.scheduled_at.isoformat() if msg.scheduled_at else None,
        "completed_at": msg.completed_at.isoformat() if msg.completed_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
    }
    if replace:
        obj["_replace"] = True
    return json.dumps(obj, separators=(",", ":"))


def _serialize_tombstone(message_id: str) -> str:
    """Serialize a tombstone marker removing `message_id` from display."""
    return json.dumps({"id": message_id, "_deleted": True}, separators=(",", ":"))


def _write_locked(path: str, lines: list[str]):
    """Append lines to file with exclusive flock to prevent interleaving."""
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for line in lines:
                f.write(line + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def flush_agent(agent_id: str):
    """Append all undisplayed messages (display_seq IS NULL) to the display file.

    Order of operations (file-first for safety):
    1. Query messages WHERE display_seq IS NULL
    2. Assign display_seq in memory
    3. Write to file (with flock)
    4. Commit display_seq to DB

    If file write fails → display_seq stays NULL → next flush retries.
    If DB commit fails → display_seq reverts to NULL → next flush retries,
    file has duplicate lines but frontend deduplicates by id.
    """
    db = SessionLocal()
    try:
        _MAX_TS = datetime(9999, 1, 1, tzinfo=timezone.utc)
        undisplayed = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.display_seq.is_(None),
                # Skip undelivered user messages — they enter the display
                # file only after delivered_at is set (by UserPromptSubmit
                # hook), so they sort after the preceding agent response.
                ~(
                    (Message.role == MessageRole.USER)
                    & (Message.delivered_at.is_(None))
                ),
            )
            .all()
        )

        def _sort_key(msg: Message) -> datetime:
            """Ordering: delivered_at if available, else created_at."""
            ts = msg.delivered_at or msg.created_at or _MAX_TS
            # Ensure tz-aware for consistent comparison (some DB rows
            # may have naive timestamps from older code paths).
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts

        undisplayed.sort(key=_sort_key)
        if not undisplayed:
            return

        # Get current max display_seq
        max_seq = db.query(func.max(Message.display_seq)).filter(
            Message.agent_id == agent_id,
        ).scalar()
        next_seq = (max_seq or 0) + 1

        # Ensure directory exists
        os.makedirs(DISPLAY_DIR, exist_ok=True)

        path = _display_path(agent_id)
        lines = []
        for msg in undisplayed:
            msg.display_seq = next_seq
            lines.append(_serialize_message(msg, next_seq))
            next_seq += 1

        # Write file FIRST — if this fails, display_seq stays uncommitted
        try:
            _write_locked(path, lines)
        except OSError:
            # File write failed — reset display_seq so next flush retries
            for msg in undisplayed:
                msg.display_seq = None
            db.expire_all()
            logger.exception("File write failed for agent %s display file", agent_id[:8])
            return

        # Commit display_seq to DB — file is already written
        db.commit()

        logger.debug(
            "Flushed %d messages to display file for agent %s (seq %d..%d)",
            len(undisplayed), agent_id[:8],
            undisplayed[0].display_seq, undisplayed[-1].display_seq,
        )

        # Single-source-of-truth WS signal: emit AFTER file write + DB commit
        # so the frontend's refetch always sees the new lines. Centralizing
        # here means callers can never forget to pair flush+emit. No-op
        # flushes (empty undisplayed) skip the emit — they returned early.
        from websocket import emit_new_message
        asyncio.ensure_future(
            emit_new_message(agent_id, undisplayed[-1].id)
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to flush display file for agent %s", agent_id[:8])
    finally:
        db.close()


def update_last(agent_id: str, message_id: str):
    """Append a replacement line for a message whose content/status changed.

    Used for:
    - Streaming content updates (agent response growing)
    - Delivery status updates (delivered_at set after promotion)

    If the message already has display_seq, appends with _replace=True so
    the frontend overwrites the stale entry.  If display_seq is None (message
    not yet in the display file), falls through to flush_agent which assigns
    a seq and writes the message for the first time.
    """
    _needs_flush = False
    _wrote_replace = False
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        if not msg or msg.agent_id != agent_id:
            return
        if msg.display_seq is None:
            _needs_flush = True
        else:
            os.makedirs(DISPLAY_DIR, exist_ok=True)
            path = _display_path(agent_id)
            line = _serialize_message(msg, msg.display_seq, replace=True)
            _write_locked(path, [line])
            _wrote_replace = True
    except Exception:
        logger.exception(
            "Failed to update display file for agent %s msg %s",
            agent_id[:8], message_id,
        )
    finally:
        db.close()

    if _needs_flush:
        flush_agent(agent_id)  # auto-emits inside
    elif _wrote_replace:
        # Replacement line written — still need to signal frontend refetch.
        from websocket import emit_new_message
        asyncio.ensure_future(emit_new_message(agent_id, message_id))


def update_after_metadata_change(agent_id: str, message_id: str):
    """Append a `_replace` line after the caller committed a meta_json
    patch on a post-delivery message (interactive AGENT card).

    Under the Phase 2 model, pre-sent messages have no DB row — so this
    helper only handles the post-delivery path and delegates to
    `update_last`. Silently no-ops if the row has been deleted between
    commit and call.
    """
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        if msg is None:
            # Defensive: row deleted between caller's commit and this call.
            return
        if msg.display_seq is None:
            # Defensive: caller passed a pre-sent id. Under the new
            # model this shouldn't happen (pre-sent has no DB row); log
            # loudly so we catch regressions rather than silently no-op.
            logger.warning(
                "update_after_metadata_change: msg %s has no display_seq "
                "(agent %s) — skipping. Pre-delivery metadata patches must "
                "go through pre_sent_update instead.",
                message_id, agent_id[:8],
            )
            return
    finally:
        db.close()

    update_last(agent_id, message_id)


# ---------------------------------------------------------------------------
# Pre-delivery API
# ---------------------------------------------------------------------------
#
# These functions maintain the in-memory `_pre_sent_index` and append
# matching JSONL lines to the per-agent display file. They do NOT touch the
# DB — a pre-sent entry has no DB row by design. The moment a message is
# sent, `pre_sent_promote_to_sent` transfers ownership from file-only to
# file+DB. See the plan for the full state machine.
#
# All functions are process-safe (fcntl.flock) and thread-safe
# (threading.Lock on the index dict).


def _scan_file_into_index(agent_id: str) -> dict[str, dict]:
    """Read the agent's display file once and return the pre-sent
    index state implied by the file (last-occurrence-wins by id, entries
    with `_pre_sent: true` (or legacy `_pre: true`), dropping tombstoned
    ones).

    Returns an empty dict if the file does not exist. Does NOT mutate the
    shared index — callers do that under `_pre_sent_lock`.
    """
    path = _display_path(agent_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except OSError:
        logger.exception("pre_sent: failed to read %s", path)
        return {}

    seen: dict[str, dict] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        mid = obj.get("id")
        if not mid:
            continue
        seen[mid] = obj

    result: dict[str, dict] = {}
    for mid, obj in seen.items():
        if obj.get("_deleted"):
            continue
        if not (obj.get("_pre_sent") or obj.get("_pre")):
            continue
        # Normalise: in-memory state always uses _pre_sent going forward.
        if "_pre" in obj and "_pre_sent" not in obj:
            obj = dict(obj)
            obj["_pre_sent"] = obj.pop("_pre")
        result[mid] = obj
    return result


def _ensure_index_loaded(agent_id: str) -> None:
    """Populate `_pre_sent_index[agent_id]` from disk if not yet loaded.

    Idempotent: subsequent calls are O(1). Holds `_pre_sent_lock` for
    the duration of the load so concurrent calls don't double-scan.
    """
    with _pre_sent_lock:
        if agent_id in _pre_sent_index_ready:
            return
        loaded = _scan_file_into_index(agent_id)
        _pre_sent_index[agent_id] = loaded
        _pre_sent_index_ready.add(agent_id)


def _validate_pre_sent_entry(entry: dict) -> None:
    """Validate a caller-supplied pre-sent entry dict.

    Raises ValueError on missing/invalid required fields.
    """
    required = ("id", "role", "content", "source", "status", "created_at")
    for field in required:
        if field not in entry:
            raise ValueError(f"pre_sent entry missing required field: {field}")
    if entry["role"] != "USER":
        raise ValueError(
            f"pre_sent entry role must be 'USER', got {entry['role']!r}"
        )
    if entry["source"] not in _VALID_PRE_SOURCES:
        raise ValueError(
            f"pre_sent entry source must be one of {_VALID_PRE_SOURCES}, "
            f"got {entry['source']!r}"
        )
    if entry["status"] not in _VALID_PRE_STATUSES:
        raise ValueError(
            f"pre_sent entry status must be one of {_VALID_PRE_STATUSES}, "
            f"got {entry['status']!r}"
        )


def pre_sent_create(agent_id: str, entry: dict) -> str:
    """Append a new pre-sent entry to the agent's display file and
    insert it into the in-memory index.

    Precondition: `entry` is a dict with the required fields (id, role,
    content, source, status, created_at). Optional fields (scheduled_at,
    metadata) may be present.

    Fills defaults (_queued=True, _pre_sent=True, delivered_at=None,
    completed_at=None, tool_use_id=None) if absent.

    Returns the entry id.

    Raises:
        ValueError: if required fields are missing or invalid.
    """
    _validate_pre_sent_entry(entry)
    _ensure_index_loaded(agent_id)

    full = dict(entry)
    full.setdefault("_queued", True)
    full["_queued"] = True  # enforce
    full["_pre_sent"] = True
    full.setdefault("scheduled_at", None)
    full.setdefault("metadata", None)
    full.setdefault("delivered_at", None)
    full.setdefault("completed_at", None)
    full.setdefault("tool_use_id", None)

    msg_id = full["id"]
    line = json.dumps(full, separators=(",", ":"))

    os.makedirs(DISPLAY_DIR, exist_ok=True)
    path = _display_path(agent_id)
    _write_locked(path, [line])

    with _pre_sent_lock:
        bucket = _pre_sent_index.setdefault(agent_id, {})
        bucket[msg_id] = full
        _pre_sent_index_ready.add(agent_id)

    return msg_id


def pre_sent_update(agent_id: str, msg_id: str, patch: dict) -> None:
    """Merge `patch` into the existing pre-sent entry and append a
    `_queued + _pre_sent + _replace` line.

    Only these fields are mergeable: content, scheduled_at, metadata,
    status. Other fields in `patch` are ignored.

    Precondition: entry must exist in the index (no DB row, still
    _pre_sent).

    Raises:
        KeyError: if msg_id is not a pre-sent entry for this agent.
    """
    _ensure_index_loaded(agent_id)

    merged: dict
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        current = bucket.get(msg_id)
        if current is None:
            raise KeyError(
                f"pre_sent_update: no pre-sent entry for msg_id="
                f"{msg_id} on agent {agent_id[:8]}"
            )
        if not (current.get("_pre_sent") or current.get("_pre")):
            raise KeyError(
                f"pre_sent_update: msg {msg_id} is not a pre-sent "
                "entry (no _pre_sent marker) — caller must use update_last"
            )
        merged = dict(current)
        # Drop legacy field if present so the rewritten line carries the
        # canonical _pre_sent marker only.
        merged.pop("_pre", None)
        for field in ("content", "scheduled_at", "metadata", "status"):
            if field in patch:
                merged[field] = patch[field]
        merged["_queued"] = True
        merged["_pre_sent"] = True
        merged["_replace"] = True
        bucket[msg_id] = {k: v for k, v in merged.items() if k != "_replace"}

    # Write outside the index lock (flock only); reuse the same serialized
    # form we just built, which includes _replace.
    os.makedirs(DISPLAY_DIR, exist_ok=True)
    path = _display_path(agent_id)
    _write_locked(path, [json.dumps(merged, separators=(",", ":"))])


def pre_sent_cancel(agent_id: str, msg_id: str) -> None:
    """Soft-cancel a queued/scheduled pre-sent entry.

    Patches status='cancelled' and appends a _replace line. The entry
    remains visible (grey bubble) until `pre_sent_tombstone` is called.

    Raises:
        KeyError: if the entry doesn't exist.
        ValueError: if the current status is not 'queued' or 'scheduled'.
    """
    _ensure_index_loaded(agent_id)
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        current = bucket.get(msg_id)
        if current is None:
            raise KeyError(
                f"pre_sent_cancel: no pre-sent entry for msg_id="
                f"{msg_id} on agent {agent_id[:8]}"
            )
        cur_status = current.get("status")
        if cur_status not in ("queued", "scheduled"):
            raise ValueError(
                f"pre_sent_cancel: current status is {cur_status!r}, "
                "only 'queued' or 'scheduled' can be cancelled"
            )
    pre_sent_update(agent_id, msg_id, {"status": "cancelled"})


def pre_sent_tombstone(agent_id: str, msg_id: str) -> None:
    """Hard-delete a cancelled pre-sent entry.

    Appends `{"id": msg_id, "_deleted": True}` tombstone and removes the
    id from the in-memory index.

    Raises:
        KeyError: if the entry doesn't exist.
        ValueError: if current status is not 'cancelled' (caller must
            cancel first).
    """
    _ensure_index_loaded(agent_id)
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        current = bucket.get(msg_id)
        if current is None:
            raise KeyError(
                f"pre_sent_tombstone: no pre-sent entry for msg_id="
                f"{msg_id} on agent {agent_id[:8]}"
            )
        if current.get("status") != "cancelled":
            raise ValueError(
                "pre_sent_tombstone: entry must be 'cancelled' first "
                f"(current status={current.get('status')!r})"
            )
        bucket.pop(msg_id, None)

    os.makedirs(DISPLAY_DIR, exist_ok=True)
    path = _display_path(agent_id)
    _write_locked(path, [_serialize_tombstone(msg_id)])


def pre_sent_list(agent_id: str) -> list[dict]:
    """Return the current pre-sent entries for an agent as a list.

    Cheap — reads the in-memory index. Returns shallow copies of each
    entry dict, so callers can mutate freely without affecting index
    state. Order is dict-insertion order, which matches creation order
    for entries that have not been updated out of band.
    """
    _ensure_index_loaded(agent_id)
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        return [dict(v) for v in bucket.values()]


def pre_sent_get(agent_id: str, msg_id: str) -> dict | None:
    """Return a shallow copy of a pre-sent entry, or None."""
    _ensure_index_loaded(agent_id)
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        current = bucket.get(msg_id)
        if current is None:
            return None
        return dict(current)


_RETRY_MARKER_ID = "__retry__"


def write_retry_marker(
    agent_id: str,
    attempts: list[dict],
    current_idx: int,
) -> None:
    """Write a seq=0 retry_marker entry to the agent's display file.

    Renders as the first item in the chat history — the orange "Attempts"
    card showing #1 #2 ... pills. Idempotent: if the file already starts
    with a retry_marker line, skip.

    `attempts`: ordered list of {"agent_id": str, "created_at": str ISO,
                                  "status": str|None}
    `current_idx`: 0-based position of THIS agent in `attempts`.

    No-op when `current_idx < 1` (attempt #1 has no prior attempts to show).

    seq=0 reserves the slot before any real message (which start at seq=1
    via _promote_pre_sent_to_sent / flush_agent's MAX(seq)+1 allocation).
    """
    if not attempts or current_idx < 1:
        return

    path = _display_path(agent_id)

    # Idempotent: skip if file's first line is already a retry_marker.
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line:
                obj = json.loads(first_line)
                if obj.get("kind") == "retry_marker":
                    return
        except (OSError, json.JSONDecodeError):
            pass

    entry = {
        "id": _RETRY_MARKER_ID,
        "seq": 0,
        "role": "SYSTEM",
        "kind": "retry_marker",
        "content": "",
        "source": "system",
        "status": "COMPLETED",
        "metadata": {
            "attempts": attempts,
            "current_idx": current_idx,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "delivered_at": None,
        "scheduled_at": None,
        "tool_use_id": None,
    }
    line = json.dumps(entry, separators=(",", ":"))

    os.makedirs(DISPLAY_DIR, exist_ok=True)

    # Make the marker physically the first line of the file. For new agents
    # the file doesn't exist yet → fast path, just write line. For backfill
    # on an existing file → atomic rewrite (read all, write new+old) under
    # flock so concurrent writers don't see a half-written file.
    if not os.path.exists(path):
        _write_locked(path, [line])
        return

    # Backfill: rewrite the file with marker prepended. Holding flock on
    # the original file blocks all other writers for the duration of the
    # rewrite; tmpfile + os.replace makes the swap atomic to readers.
    import tempfile
    with open(path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            existing = f.read()
            tmp = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False,
                dir=os.path.dirname(path), prefix=".retry-rewrite-",
            )
            try:
                tmp.write(line + "\n")
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                tmp.write(existing)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp.close()
                os.replace(tmp.name, path)
            except Exception:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def write_retry_marker_for_agent(db, agent) -> None:
    """Convenience wrapper: query task siblings and write marker if applicable.

    Call right after `db.add(agent); db.flush()` for any agent that may be
    a retry (i.e. has task_id set). No-op for attempt #1 or non-task agents.
    """
    if not agent.task_id:
        return
    rows = (
        db.query(Agent)
        .filter(Agent.task_id == agent.task_id)
        .order_by(Agent.created_at.asc())
        .all()
    )
    if len(rows) <= 1:
        return
    current_idx = next((i for i, a in enumerate(rows) if a.id == agent.id), -1)
    if current_idx < 1:
        return
    attempts = [
        {
            "agent_id": a.id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "status": a.status.value if a.status else None,
        }
        for a in rows
    ]
    write_retry_marker(agent.id, attempts, current_idx)


def pre_sent_promote_to_sent(
    agent_id: str,
    msg_id: str,
    seq: int,
    sent_line: dict,
) -> None:
    """Atomically transition a pre-sent entry to sent (DB-backed).

    Under one flock:
      1. append `{"id": msg_id, "_deleted": True}` — evicts the _pre_sent
         line from the reader's queued partition
      2. append `sent_line` — the regular delivered-partition entry (must
         carry a seq and not be _queued/_pre_sent)

    Also removes msg_id from the in-memory index.

    Precondition: the caller INSERTed the DB row for this message already.
    This function only writes the file.

    Raises:
        ValueError: if `sent_line` has no `id` or its id doesn't match
            `msg_id`, or if it carries _queued / _pre_sent markers.
    """
    if sent_line.get("id") != msg_id:
        raise ValueError(
            f"pre_sent_promote_to_sent: sent_line id "
            f"{sent_line.get('id')!r} does not match msg_id {msg_id!r}"
        )
    if sent_line.get("_queued") or sent_line.get("_pre_sent") or sent_line.get("_pre"):
        raise ValueError(
            "pre_sent_promote_to_sent: sent_line must not carry "
            "_queued or _pre_sent markers"
        )
    # seq is informational here — the caller embedded it into sent_line
    # already; we don't double-write.
    _ = seq

    _ensure_index_loaded(agent_id)
    with _pre_sent_lock:
        bucket = _pre_sent_index.get(agent_id, {})
        bucket.pop(msg_id, None)

    os.makedirs(DISPLAY_DIR, exist_ok=True)
    path = _display_path(agent_id)
    tombstone = _serialize_tombstone(msg_id)
    sent_serialized = json.dumps(sent_line, separators=(",", ":"))
    # Single _write_locked call → single flock acquisition for both lines.
    _write_locked(path, [tombstone, sent_serialized])


def rebuild_agent(agent_id: str):
    """Rebuild display file from scratch (read-before-truncate).

    Truncates the existing file and re-flushes all messages with fresh
    display_seq values.  This prevents stale append-only blocks from
    accumulating and ensures the file reflects the current DB state.

    Read-before-truncate: before truncating, reads the current file and
    preserves any pre-sent (`_pre_sent: true`, or legacy `_pre: true`)
    entries that are not tombstoned — these have no DB row by design, so
    rebuilding solely from the DB would drop them. After the DB-driven
    flush completes, the preserved entries are appended back and the
    in-memory pre-sent index is rebuilt for this agent.
    """
    path = _display_path(agent_id)

    # 1. Read-before-truncate: snapshot surviving _pre_sent entries
    #    (the scan normalises legacy _pre lines to _pre_sent).
    preserved_pre: dict[str, dict] = _scan_file_into_index(agent_id)

    db = SessionLocal()
    try:
        # Reset display_seq to NULL so flush_agent picks them all up
        db.execute(
            text("UPDATE messages SET display_seq = NULL WHERE agent_id = :aid"),
            {"aid": agent_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to reset display_seq for agent %s", agent_id[:8])
        return
    finally:
        db.close()

    # Truncate existing file — flush_agent will write a clean file
    try:
        with open(path, "w") as f:
            f.truncate(0)
    except OSError:
        pass  # file may not exist yet — flush_agent will create it

    flush_agent(agent_id)

    # 2. Re-append preserved _pre_sent entries and rebuild the in-memory index.
    if preserved_pre:
        os.makedirs(DISPLAY_DIR, exist_ok=True)
        pre_lines = [
            json.dumps(entry, separators=(",", ":"))
            for entry in preserved_pre.values()
        ]
        try:
            _write_locked(path, pre_lines)
        except OSError:
            logger.exception(
                "rebuild_agent: failed to re-append _pre_sent entries for agent %s",
                agent_id[:8],
            )

    with _pre_sent_lock:
        _pre_sent_index[agent_id] = dict(preserved_pre)
        _pre_sent_index_ready.add(agent_id)


def delete_agent(agent_id: str):
    """Remove display file for a deleted/stopped agent."""
    path = _display_path(agent_id)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to delete display file %s: %s", path, e)


def startup_rebuild_all():
    """On server startup, rebuild display files for all active agents.

    Query agents WHERE status NOT IN ('STOPPED', 'ERROR'), rebuild each.
    After rebuild, ensure every active agent's pre-sent index is
    initialised (read-only scan is cheap for any agent the rebuild step
    already loaded).
    """
    db = SessionLocal()
    try:
        agents = (
            db.query(Agent.id)
            .filter(Agent.status.notin_([AgentStatus.STOPPED, AgentStatus.ERROR]))
            .all()
        )
    finally:
        db.close()

    if not agents:
        logger.info("No active agents — skipping display file rebuild")
        return

    logger.info("Rebuilding display files for %d active agents", len(agents))
    for (aid,) in agents:
        try:
            rebuild_agent(aid)
        except Exception:
            logger.exception("Failed to rebuild display file for agent %s", aid[:8])

    # Safety net: for agents whose rebuild failed or was skipped, still
    # load the pre-sent index so the reader endpoint serves a correct
    # queued partition on first request.
    for (aid,) in agents:
        try:
            _ensure_index_loaded(aid)
        except Exception:
            logger.exception(
                "Failed to load pre_sent index for agent %s", aid[:8]
            )

    logger.info("Display file rebuild complete")
