"""Tests for dropbox sync hooks in routers/hooks.py.

Verifies that hook_agent_tool_activity and hook_agent_stop fire
note_agent_activity with the correct arguments for write-like tools,
and do NOT fire for read-only tools or failures.
"""

import asyncio

import pytest


AGENT_ID = "aaaa-bbbb-0001"


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_post_tool_use_write_calls_note_activity(client, monkeypatch):
    """PostToolUse with a Write tool calls _note_dropbox_activity(agent_id, 'write')."""
    from routers import hooks

    # Bypass the DB-dependent resolver
    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []
    original = hooks._note_dropbox_activity

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-tool-activity",
        json={
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "session_id": "sess-001",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0] == (AGENT_ID, "write")


@pytest.mark.anyio
async def test_post_tool_use_edit_calls_note_activity(client, monkeypatch):
    """PostToolUse with an Edit tool calls _note_dropbox_activity."""
    from routers import hooks

    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-tool-activity",
        json={
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "session_id": "sess-001",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0] == (AGENT_ID, "write")


@pytest.mark.anyio
async def test_post_tool_use_bash_calls_note_activity(client, monkeypatch):
    """PostToolUse with Bash tool calls _note_dropbox_activity."""
    from routers import hooks

    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-tool-activity",
        json={
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": "sess-001",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0] == (AGENT_ID, "write")


@pytest.mark.anyio
async def test_post_tool_use_read_does_not_call(client, monkeypatch):
    """PostToolUse with a Read tool does NOT call _note_dropbox_activity."""
    from routers import hooks

    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-tool-activity",
        json={
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "session_id": "sess-001",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 0


@pytest.mark.anyio
async def test_post_tool_use_failure_does_not_call(client, monkeypatch):
    """PostToolUseFailure does NOT call _note_dropbox_activity."""
    from routers import hooks

    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-tool-activity",
        json={
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Write",
            "session_id": "sess-001",
            "tool_error": "something failed",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 0


@pytest.mark.anyio
async def test_agent_stop_calls_note_activity(client, monkeypatch):
    """hook_agent_stop calls _note_dropbox_activity(agent_id, 'stop')."""
    from routers import hooks

    monkeypatch.setattr(hooks, "_resolve_agent_for_hook", lambda req, body: AGENT_ID)
    monkeypatch.setattr(hooks, "_is_subprocess_session", lambda *a, **kw: False)

    calls = []

    async def _capture(aid, reason):
        calls.append((aid, reason))

    monkeypatch.setattr(hooks, "_note_dropbox_activity", _capture)

    resp = await client.post(
        "/api/hooks/agent-stop",
        json={
            "session_id": "sess-001",
        },
        headers={"X-Tmux-Pane": "%201"},
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert calls[0] == (AGENT_ID, "stop")
