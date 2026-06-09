"""Tests for sync_engine status-signal gating + full-scan drift exclusion.

Covers the 2026-06-03 auto-compact spurious-IDLE incident:
1. slash_signal turns are intentionally never imported, so drift audits
   (sync_full_scan missing_in_db, realtime snapshot) must not count them
   as missing — counting them rewound the pointer on every full scan.
2. Replayed turns (UUID-dedup'd against existing DB rows) must not feed
   the status-signal accumulator — a replayed historical stop_hook flipped
   EXECUTING→IDLE mid-task and re-fired stop-hook ops.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from database import init_db, SessionLocal
from models import (
    Agent,
    AgentStatus,
    Message,
    MessageRole,
    MessageStatus,
    Project,
)
from sync_engine import SyncContext, sync_import_new_turns, sync_full_scan

init_db()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class StubAD:
    """Minimal agent_dispatcher stand-in for the sync entry points."""

    def __init__(self):
        self.stop_generating_calls: list[str] = []
        self.dispatch_calls: list[str] = []
        self._sync_contexts: dict = {}

    def _emit(self, coro):
        coro.close()

    def _stop_generating(self, agent_id):
        self.stop_generating_calls.append(agent_id)

    def _is_agent_in_use(self, agent_id, pane=None):
        return True  # skip unread-bump + push-notify side paths

    def _maybe_notify_message(self, *args, **kwargs):
        pass

    def _bump_unread_and_notify_interactive(self, *args, **kwargs):
        pass

    async def dispatch_pending_message(self, agent_id, delay=0):
        self.dispatch_calls.append(agent_id)


@pytest.fixture(autouse=True)
def _quiet_side_effects(monkeypatch):
    """Resume-hint refresh spawns an LLM subprocess — irrelevant here."""
    import routers.projects as rp

    async def _noop(agent_id):
        return None

    monkeypatch.setattr(rp, "_refresh_resume_hint", _noop)


def _run(coro):
    """asyncio.run + drain fire-and-forget tasks before loop close."""
    async def _main():
        result = await coro
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return result

    return asyncio.run(_main())


def _mk_agent(status=AgentStatus.EXECUTING) -> str:
    db = SessionLocal()
    pname = f"proj-{uuid.uuid4().hex[:8]}"
    db.add(Project(name=pname, display_name=pname, path=f"/tmp/{pname}"))
    db.commit()
    agent = Agent(project=pname, name="test-agent", status=status,
                  session_id=uuid.uuid4().hex)
    db.add(agent)
    db.commit()
    agent_id = agent.id
    db.close()
    return agent_id


def _status(agent_id) -> AgentStatus:
    db = SessionLocal()
    status = db.get(Agent, agent_id).status
    db.close()
    return status


def _set_status(agent_id, status):
    db = SessionLocal()
    db.get(Agent, agent_id).status = status
    db.commit()
    db.close()


_TS = "2026-06-09T00:00:0{}Z"


def _entry_user(uid, text, ts=1):
    return {"type": "user", "uuid": uid, "timestamp": _TS.format(ts),
            "message": {"role": "user", "content": text}}


def _entry_assistant(uid, text, ts=2):
    return {"type": "assistant", "uuid": uid, "timestamp": _TS.format(ts),
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def _entry_stop_hook(uid, ts=3):
    return {"type": "system", "subtype": "stop_hook_summary",
            "content": "stop hook summary", "uuid": uid,
            "timestamp": _TS.format(ts)}


def _entry_slash(uid, cmd="/compact", ts=4):
    wrapper = (f"<command-name>{cmd}</command-name>"
               f"<command-message>{cmd[1:]}</command-message>"
               f"<command-args></command-args>")
    return {"type": "user", "uuid": uid, "timestamp": _TS.format(ts),
            "message": {"role": "user", "content": wrapper}}


def _write_jsonl(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _ctx(agent_id, jsonl_path) -> SyncContext:
    return SyncContext(agent_id=agent_id, session_id="test-session",
                       project_path="/tmp", jsonl_path=str(jsonl_path))


# ---------------------------------------------------------------------------
# Signal gating (fix 2)
# ---------------------------------------------------------------------------

def test_live_stop_hook_flips_idle(tmp_path):
    """First import of a stop_hook is a live signal: EXECUTING → IDLE."""
    agent_id = _mk_agent(AgentStatus.EXECUTING)
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        _entry_user("u1", "question"),
        _entry_assistant("a1", "answer"),
        _entry_stop_hook("s1"),
    ])
    ad = StubAD()
    result = _run(sync_import_new_turns(ad, _ctx(agent_id, jsonl)))

    assert result == "new_turns"
    assert _status(agent_id) == AgentStatus.IDLE
    assert ad.stop_generating_calls == [agent_id]


def test_replayed_stop_hook_keeps_executing(tmp_path):
    """Pointer rewind replays history — replayed stop_hooks must not flip
    the agent IDLE or re-fire stop-hook ops (the auto-compact incident)."""
    agent_id = _mk_agent(AgentStatus.EXECUTING)
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        _entry_user("u1", "question"),
        _entry_assistant("a1", "answer"),
        _entry_stop_hook("s1"),
    ])
    ctx = _ctx(agent_id, jsonl)
    _run(sync_import_new_turns(StubAD(), ctx))
    assert _status(agent_id) == AgentStatus.IDLE

    # New task starts (UserPromptSubmit hook flips EXECUTING), then a
    # full-scan drift repair rewinds the pointer to 0.
    _set_status(agent_id, AgentStatus.EXECUTING)
    ctx.last_turn_count = 0
    ctx.last_offset = 0
    ctx.last_content_hash = ""

    ad = StubAD()
    _run(sync_import_new_turns(ad, ctx))

    assert _status(agent_id) == AgentStatus.EXECUTING
    assert ad.stop_generating_calls == []
    assert ad.dispatch_calls == []


def test_promotion_counts_as_live_signal(tmp_path):
    """A sent→delivered promotion is a live event: its user-turn signal
    must survive the replay gate and flip IDLE → EXECUTING."""
    agent_id = _mk_agent(AgentStatus.IDLE)
    db = SessionLocal()
    db.add(Message(agent_id=agent_id, role=MessageRole.USER,
                   content="run the tests", status=MessageStatus.SENT,
                   source="web"))
    db.commit()
    db.close()

    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [_entry_user("u9", "run the tests")])
    _run(sync_import_new_turns(StubAD(), _ctx(agent_id, jsonl)))

    assert _status(agent_id) == AgentStatus.EXECUTING
    db = SessionLocal()
    user_msgs = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.role == MessageRole.USER,
    ).all()
    db.close()
    assert len(user_msgs) == 1  # promoted, not duplicated
    assert user_msgs[0].jsonl_uuid == "u9"


def test_trailing_assistant_after_stop_hook_still_idle(tmp_path):
    """The accumulator's original purpose: a trailing assistant entry after
    stop_hook_summary (both genuinely new) must still land IDLE."""
    agent_id = _mk_agent(AgentStatus.EXECUTING)
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        _entry_assistant("a1", "working"),
        _entry_stop_hook("s1", ts=2),
        _entry_assistant("a2", "trailing entry", ts=3),
    ])
    _run(sync_import_new_turns(StubAD(), _ctx(agent_id, jsonl)))

    assert _status(agent_id) == AgentStatus.IDLE


# ---------------------------------------------------------------------------
# Full-scan drift exclusion (fix 1)
# ---------------------------------------------------------------------------

def test_full_scan_ignores_unimported_slash_signal(tmp_path):
    """slash_signal turns never get DB rows by design — full scan must not
    count them as missing, and must not rewind the pointer for them."""
    agent_id = _mk_agent(AgentStatus.IDLE)
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        _entry_user("u1", "question"),
        _entry_slash("sl1", ts=2),
        _entry_assistant("a1", "answer", ts=3),
        _entry_stop_hook("s1", ts=4),
    ])
    ctx = _ctx(agent_id, jsonl)
    _run(sync_import_new_turns(StubAD(), ctx))
    assert ctx.last_turn_count == 4

    result = _run(sync_full_scan(StubAD(), ctx, reason="compact"))

    assert result["missing_in_db"] == 0
    assert ctx.last_turn_count == 4  # pointer NOT rewound


def test_full_scan_compact_keeps_legacy_slash_rows(tmp_path):
    """Legacy DB rows holding slash_signal uuids (imported before the skip
    existed) must NOT become purge-eligible orphans on a compact scan."""
    agent_id = _mk_agent(AgentStatus.IDLE)
    db = SessionLocal()
    db.add(Message(agent_id=agent_id, role=MessageRole.USER,
                   content="/compact", status=MessageStatus.COMPLETED,
                   source="cli", jsonl_uuid="sl1", kind="slash_signal"))
    db.commit()
    db.close()

    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [
        _entry_user("u1", "question"),
        _entry_slash("sl1", ts=2),
    ])
    ctx = _ctx(agent_id, jsonl)
    _run(sync_import_new_turns(StubAD(), ctx))
    result = _run(sync_full_scan(StubAD(), ctx, reason="compact"))

    assert result["missing_in_db"] == 0
    db = SessionLocal()
    legacy = db.query(Message).filter(
        Message.agent_id == agent_id,
        Message.jsonl_uuid == "sl1",
    ).first()
    db.close()
    assert legacy is not None  # not purged as an orphan
