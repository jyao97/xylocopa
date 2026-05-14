"""Periodic reconcile sweep that turns discovered JSONL sessions into
``CCSession`` rows.

The discovery side (:mod:`cc_session_discovery`) is read-only. This
module is the only writer for ``cc_sessions``. It is intentionally
idempotent — re-running on the same disk state is a no-op until tokens
or end timestamps grow.

For each xylo agent we:

1. List all JSONLs in the agent's project (and any worktree subdir).
2. For each top-level JSONL (``parent_jsonl_uuid is None``), check the
   ``.owner`` sidecar; only insert/update if it points at THIS agent.
3. For each sub-session JSONL (with a ``parent_jsonl_uuid``), link it to
   its parent JSONL's ``session_id``. Insert it only if that parent
   already belongs to (or now belongs to) this agent.
4. Insert missing rows; update existing rows whose token totals or
   ``ended_at`` grew.

Subagents (``Agent.is_subagent == True``) are skipped — they are
metadata rows that share the parent xylo agent's CC session and don't
own their own JSONL.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from cc_session_discovery import (
    discover_project_sessions,
    find_owner_for_top_session,
    link_sub_to_parent,
)
from database import SessionLocal
from models import Agent, CCSession, Project

logger = logging.getLogger(__name__)


def _parse_iso(ts: str | None) -> datetime | None:
    """Best-effort ISO-8601 parse → tz-naive UTC datetime.

    SQLAlchemy's default DateTime column on SQLite stores naive timestamps,
    so we strip tzinfo to keep the storage shape consistent with the rest
    of the schema (``utils.utcnow`` writes a tz-aware UTC datetime, but
    SQLite drops tzinfo on insert and reads back naive).
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        # Python 3.11+ accepts trailing 'Z' since 3.11.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


_TOKEN_FIELDS = (
    "total_input_tokens",
    "total_output_tokens",
    "total_cache_creation_tokens",
    "total_cache_creation_5m_tokens",
    "total_cache_creation_1h_tokens",
    "total_cache_read_tokens",
)


def _row_needs_update(row: CCSession, md: dict) -> bool:
    """Return True if the row has stale token counts vs *md*.

    We only compare the four token totals + turn_count + ended_at. We
    deliberately do NOT compare ``model``, ``parent_session_id``,
    ``project_path``, ``worktree``, etc. — those are immutable session
    metadata and reconcile should never overwrite them once written.
    """
    for f in _TOKEN_FIELDS:
        new_v = int(md.get(f, 0) or 0)
        if new_v != int(getattr(row, f) or 0):
            return True
    new_turns = int(md.get("turn_count", 0) or 0)
    if new_turns != int(row.turn_count or 0):
        return True
    new_ended = _parse_iso(md.get("ended_at"))
    if new_ended is not None and row.ended_at is None:
        return True
    if new_ended is not None and row.ended_at is not None:
        if new_ended > row.ended_at:
            return True
    return False


def _apply_update(row: CCSession, md: dict) -> None:
    for f in _TOKEN_FIELDS:
        setattr(row, f, int(md.get(f, 0) or 0))
    row.turn_count = int(md.get("turn_count", 0) or 0)
    new_ended = _parse_iso(md.get("ended_at"))
    if new_ended is not None:
        if row.ended_at is None or new_ended > row.ended_at:
            row.ended_at = new_ended


def _build_row(
    md: dict,
    *,
    agent_id: str,
    project_path: str,
    worktree: str | None,
    is_subagent_session: bool,
    parent_session_id: str | None,
) -> CCSession:
    return CCSession(
        session_id=md["session_id"],
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        parent_jsonl_uuid=md.get("parent_jsonl_uuid"),
        project_path=project_path,
        worktree=worktree,
        is_subagent_session=is_subagent_session,
        started_at=_parse_iso(md.get("started_at")),
        ended_at=_parse_iso(md.get("ended_at")),
        end_reason="reconciled",
        model=md.get("model"),
        total_input_tokens=int(md.get("total_input_tokens", 0) or 0),
        total_output_tokens=int(md.get("total_output_tokens", 0) or 0),
        total_cache_creation_tokens=int(
            md.get("total_cache_creation_tokens", 0) or 0
        ),
        total_cache_creation_5m_tokens=int(
            md.get("total_cache_creation_5m_tokens", 0) or 0
        ),
        total_cache_creation_1h_tokens=int(
            md.get("total_cache_creation_1h_tokens", 0) or 0
        ),
        total_cache_read_tokens=int(md.get("total_cache_read_tokens", 0) or 0),
        turn_count=int(md.get("turn_count", 0) or 0),
    )


def reconcile_agent(
    agent_id: str,
    *,
    db: SASession | None = None,
) -> dict[str, int]:
    """Sweep one xylo agent's JSONLs into ``cc_sessions``.

    For each JSONL in the agent's project (+ any worktree subdir):

    - Top-level (no ``parentUuid``): owned only if the ``.owner`` sidecar
      file resolves to this agent. Foreign sessions are skipped.
    - Sub-session: owned if its ``parent_jsonl_uuid`` traces back to a
      JSONL that THIS agent owns.

    Inserts new rows, updates existing rows whose tokens/ended_at have
    grown, leaves everything else alone.

    Returns ``{discovered, inserted, updated, skipped}`` counts. The dict
    keys are stable so callers can aggregate across multiple agents.

    *db* may be passed in to share a session across many agents in a
    single sweep; if omitted we allocate one ``SessionLocal()`` and
    commit at the end.
    """
    counts = {"discovered": 0, "inserted": 0, "updated": 0, "skipped": 0}

    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        agent = db.get(Agent, agent_id)
        if agent is None or agent.is_subagent:
            # Subagents share their parent's CC session — no JSONL of
            # their own. Missing agents are caller error; just bail.
            return counts

        project = db.get(Project, agent.project) if agent.project else None
        if project is None or not project.path:
            return counts

        all_md = discover_project_sessions(project.path, worktree=agent.worktree)
        counts["discovered"] = len(all_md)
        if not all_md:
            return counts

        # First pass: classify each JSONL by ownership.
        # owned_top_ids = top-level sessions owned by THIS agent (by .owner sidecar)
        # md_by_sid     = lookup for fast parent resolution
        owned_top_ids: set[str] = set()
        md_by_sid: dict[str, dict] = {}
        for md in all_md:
            md_by_sid[md["session_id"]] = md
            # Skip subdir-subagent JSONLs in this pass — they're path-linked
            # not owner-linked, and we'll fold them in below once the parent
            # set is known.
            if md.get("is_subagent_session"):
                continue
            if md.get("parent_jsonl_uuid") is None:
                owner = find_owner_for_top_session(
                    md["session_id"], md["session_dir"]
                )
                if owner == agent_id:
                    owned_top_ids.add(md["session_id"])

        # Second pass: walk parent chains for sub-sessions to determine
        # which sub-sessions transitively belong to one of *agent_id*'s
        # owned top-level sessions. We resolve parent links lazily via
        # link_sub_to_parent so even cross-file chains (rare but legal)
        # work correctly.
        parent_cache: dict[str, str | None] = {}

        def _resolve_parent_sid(md: dict) -> str | None:
            sid = md["session_id"]
            if sid in parent_cache:
                return parent_cache[sid]
            psid = link_sub_to_parent(md, all_md)
            parent_cache[sid] = psid
            return psid

        # Assemble the final write set.
        owned_sub_ids: set[str] = set()
        sub_parent_map: dict[str, str] = {}  # sub_sid -> parent_sid
        # Path-linked subagent JSONLs (from <parent_sid>/subagents/agent-*.jsonl):
        # parent_session_id is authoritative from the directory layout —
        # if that parent is owned, the subagent JSONL belongs to the same
        # xylo agent.
        for md in all_md:
            if not md.get("is_subagent_session"):
                continue
            psid = md.get("parent_session_id")
            sid = md["session_id"]
            if psid and psid in owned_top_ids:
                owned_sub_ids.add(sid)
                sub_parent_map[sid] = psid

        # Repeat until fixed-point in case sub-sessions chain (sub of sub).
        all_owned: set[str] = set(owned_top_ids) | owned_sub_ids
        changed = True
        while changed:
            changed = False
            for md in all_md:
                sid = md["session_id"]
                if sid in all_owned:
                    continue
                if md.get("is_subagent_session"):
                    continue  # already handled above
                if md.get("parent_jsonl_uuid") is None:
                    continue
                psid = _resolve_parent_sid(md)
                if psid and psid in all_owned:
                    owned_sub_ids.add(sid)
                    sub_parent_map[sid] = psid
                    all_owned.add(sid)
                    changed = True

        # Third pass: write rows.
        write_md: list[tuple[dict, bool, str | None]] = []  # (md, is_sub, parent_sid)
        for sid in owned_top_ids:
            write_md.append((md_by_sid[sid], False, None))
        for sid in owned_sub_ids:
            write_md.append((md_by_sid[sid], True, sub_parent_map.get(sid)))

        # Fetch existing rows in one query.
        if write_md:
            sids = [m[0]["session_id"] for m in write_md]
            existing = {
                r.session_id: r
                for r in db.execute(
                    select(CCSession).where(CCSession.session_id.in_(sids))
                ).scalars().all()
            }
        else:
            existing = {}

        for md, is_sub, parent_sid in write_md:
            sid = md["session_id"]
            row = existing.get(sid)
            if row is None:
                # Worktree value: if the JSONL came from a worktree subdir,
                # fold that into the row. A simple heuristic: if
                # session_dir != session_source_dir(project.path), assume
                # it's a worktree dir and stash its basename.
                row_worktree = agent.worktree
                # Insert.
                new_row = _build_row(
                    md,
                    agent_id=agent_id,
                    project_path=project.path,
                    worktree=row_worktree,
                    is_subagent_session=is_sub,
                    parent_session_id=parent_sid,
                )
                db.add(new_row)
                counts["inserted"] += 1
            else:
                if _row_needs_update(row, md):
                    _apply_update(row, md)
                    counts["updated"] += 1
                else:
                    counts["skipped"] += 1

        if own_db:
            db.commit()
    except Exception:
        if own_db:
            try:
                db.rollback()
            except Exception:
                pass
        logger.exception("reconcile_agent: failed for agent_id=%s", agent_id)
        # Fall through with whatever counts we accumulated.
    finally:
        if own_db:
            db.close()

    return counts


def reconcile_all(*, db: SASession | None = None) -> dict[str, int]:
    """Iterate every non-subagent xylo agent and call :func:`reconcile_agent`.

    Returns aggregated counts keyed identically to :func:`reconcile_agent`,
    plus an ``agents`` count.
    """
    totals = {
        "agents": 0,
        "discovered": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
    }
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        agent_ids = [
            a_id for (a_id,) in db.execute(
                select(Agent.id).where(Agent.is_subagent == False)  # noqa: E712
            ).all()
        ]
    except Exception:
        if own_db:
            db.close()
        logger.exception("reconcile_all: failed to list agents")
        return totals

    try:
        for aid in agent_ids:
            try:
                # Reuse the same DB session — fewer connections, one
                # commit at the very end.
                c = reconcile_agent(aid, db=db)
            except Exception:
                logger.exception("reconcile_all: agent %s failed", aid[:8])
                continue
            totals["agents"] += 1
            totals["discovered"] += c["discovered"]
            totals["inserted"] += c["inserted"]
            totals["updated"] += c["updated"]
            totals["skipped"] += c["skipped"]
            # Flush + expunge after each agent so the shared identity_map
            # doesn't accumulate every CCSession row across 500+ agents
            # (was the dominant slow-leak driver at 80k JSONL scale).
            if own_db:
                try:
                    db.flush()
                except Exception:
                    db.rollback()
                    logger.exception("reconcile_all: flush failed for agent %s", aid[:8])
                    continue
                db.expunge_all()
        if own_db:
            db.commit()
    except Exception:
        if own_db:
            try:
                db.rollback()
            except Exception:
                pass
        logger.exception("reconcile_all: commit failed")
    finally:
        if own_db:
            db.close()

    return totals


__all__ = ["reconcile_agent", "reconcile_all"]
