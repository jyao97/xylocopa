"""Tests for routers.hooks._await_jsonl_flush — wait_for marker + Phase 2 loop.

Background:
The original Phase 1 used "any grew" as the predicate.  For the Stop hook
caller specifically, this caused a race: a late-flushed pre-Stop entry
(typically the assistant turn) would land within the 150ms window and
trick Phase 1 into returning True before CC wrote stop_hook_summary.
Sync then ran without _saw_stop_hook, leaving the agent stuck EXECUTING.

Production data (2026-05-12): ~6% of Stop hooks had >1s delay, ~1% had
>15 minute delay — bimodal "happy path 250ms" vs "race victim minutes"
distribution.

These tests guard the fix:
  - With wait_for=None (legacy callers): unchanged any-grew behavior.
  - With wait_for=<marker>:
      * Phase 1 returns True only if the marker appears in new bytes.
      * Phase 2 loops on watchdog wakes — a non-marker write that wakes
        the watchdog must NOT cause an early True; we keep waiting until
        the marker arrives or the budget runs out.

The real filesystem watchdog (wait_for_jsonl_flush) is mocked.  The unit
under test is the Phase 1/Phase 2 control flow inside _await_jsonl_flush;
the watchdog primitive has its own behavior contract that these tests
don't re-validate.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import hooks as hooks_mod
from routers.hooks import _await_jsonl_flush


# ---------------------------------------------------------------------------
# Minimal fakes — _await_jsonl_flush only touches ad._sync_contexts[id].jsonl_path
# ---------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, jsonl_path: str):
        self.jsonl_path = jsonl_path


class _FakeAd:
    def __init__(self, agent_id: str, jsonl_path: str):
        self._sync_contexts = {agent_id: _FakeCtx(jsonl_path)}


@pytest.fixture
def jsonl_env(tmp_path):
    """Empty JSONL file + fake ad/ctx wiring."""
    path = tmp_path / "session.jsonl"
    path.write_bytes(b"")
    agent_id = "test1234"
    ad = _FakeAd(agent_id, str(path))
    return str(path), ad, agent_id


@pytest.fixture
def fake_watchdog(monkeypatch):
    """Replace agent_dispatcher.wait_for_jsonl_flush with a programmable
    fake.  Each call awaits an item from `events`, or times out per the
    asked timeout.  Set events.put(True) to simulate a watchdog wake;
    no put → real timeout.

    Returns a controller dict:
      ``events``: asyncio.Queue of bool wakes (True=woke, False=timeout)
      ``calls``:  list of timeouts each call was invoked with
    """
    queue: asyncio.Queue = asyncio.Queue()
    calls: list[float] = []

    async def fake_wait_for_jsonl_flush(jsonl_path: str, timeout: float = 10.0) -> bool:
        calls.append(timeout)
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return False

    import agent_dispatcher
    monkeypatch.setattr(
        agent_dispatcher, "wait_for_jsonl_flush", fake_wait_for_jsonl_flush,
    )
    return {"events": queue, "calls": calls}


def _append(path: str, data: bytes) -> None:
    with open(path, "ab") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Legacy any-grew behavior (wait_for=None) — guard against regression for the
# 10 callers that haven't been migrated.
# ---------------------------------------------------------------------------

class TestLegacyAnyGrew:
    @pytest.mark.anyio
    async def test_any_growth_returns_true_in_phase1(self, jsonl_env, fake_watchdog):
        """Pre-write before function entry would be in baseline; instead
        we write AFTER baseline snapshot, before Phase 1's 150ms check."""
        path, ad, agent_id = jsonl_env

        async def _writer():
            await asyncio.sleep(0.02)
            _append(path, b'{"random":"content"}\n')

        task = asyncio.create_task(_writer())
        try:
            result = await _await_jsonl_flush(ad, agent_id, timeout=2.0)
            assert result is True
        finally:
            await task

    @pytest.mark.anyio
    async def test_no_growth_falls_through_to_phase2_timeout(self, jsonl_env, fake_watchdog):
        """No file growth → Phase 1 falls through → Phase 2 watchdog never
        wakes (we don't push to the queue) → returns False on timeout."""
        _path, ad, agent_id = jsonl_env
        result = await _await_jsonl_flush(ad, agent_id, timeout=0.4)
        assert result is False


# ---------------------------------------------------------------------------
# wait_for marker semantics — the fix for the Stop hook race.
# ---------------------------------------------------------------------------

MARKER = b'"subtype":"stop_hook_summary"'


class TestWaitForMarker:
    @pytest.mark.anyio
    async def test_phase1_marker_found(self, jsonl_env, fake_watchdog):
        """Marker landing during the 150ms Phase 1 sleep → return True."""
        path, ad, agent_id = jsonl_env

        async def _writer():
            await asyncio.sleep(0.03)
            _append(path, b'{"type":"system","subtype":"stop_hook_summary","x":1}\n')

        task = asyncio.create_task(_writer())
        try:
            result = await _await_jsonl_flush(
                ad, agent_id, wait_for=MARKER, timeout=2.0,
            )
            assert result is True
        finally:
            await task

    @pytest.mark.anyio
    async def test_phase1_grew_without_marker_does_not_succeed(self, jsonl_env, fake_watchdog):
        """The race-reproducing case: file grew during Phase 1, but with
        non-marker content (late-flushed assistant entry).  Phase 1 must
        NOT return True — must fall through to Phase 2, which times out
        here because we never push a wake event."""
        path, ad, agent_id = jsonl_env

        async def _writer():
            await asyncio.sleep(0.03)
            _append(path, b'{"type":"assistant","content":"hello there"}\n')

        task = asyncio.create_task(_writer())
        try:
            result = await _await_jsonl_flush(
                ad, agent_id, wait_for=MARKER, timeout=0.5,
            )
            assert result is False, (
                "Phase 1 falsely succeeded on non-marker growth — "
                "this is exactly the race the fix is meant to prevent"
            )
        finally:
            await task

    @pytest.mark.anyio
    async def test_phase2_loop_waits_through_distraction_for_marker(
        self, jsonl_env, fake_watchdog,
    ):
        """Two watchdog wakes during Phase 2: the first one corresponds
        to a non-marker write (distraction); the second corresponds to
        the marker write.  Phase 2 must NOT return on the first wake;
        must continue until marker appears.

        Without the Phase 2 LOOP fix, this test would fail because the
        original code returned on the first wake regardless of content.
        """
        path, ad, agent_id = jsonl_env

        async def _orchestrator():
            # Past Phase 1 (150ms) — write distractor, wake watchdog once
            await asyncio.sleep(0.25)
            _append(path, b'{"type":"user","content":"distraction"}\n')
            await fake_watchdog["events"].put(True)
            # Another beat, then the real marker + a second wake
            await asyncio.sleep(0.1)
            _append(path, b'{"type":"system","subtype":"stop_hook_summary"}\n')
            await fake_watchdog["events"].put(True)

        task = asyncio.create_task(_orchestrator())
        try:
            result = await _await_jsonl_flush(
                ad, agent_id, wait_for=MARKER, timeout=3.0,
            )
            assert result is True, (
                "Phase 2 didn't catch the late marker — the watchdog loop "
                "must re-check after each wake until marker appears"
            )
            # Verify we actually went through Phase 2 more than once
            assert len(fake_watchdog["calls"]) >= 2, (
                "Phase 2 should have invoked the watchdog twice "
                f"(once per wake), got {len(fake_watchdog['calls'])}"
            )
        finally:
            await task

    @pytest.mark.anyio
    async def test_marker_present_at_entry_does_not_match(self, jsonl_env, fake_watchdog):
        """Edge case: the marker is already in the file before
        _await_jsonl_flush is called.  Since baseline is taken at function
        entry, the marker is part of baseline and does NOT count as new.
        Phase 1 sees no growth → falls through → Phase 2 times out."""
        path, ad, agent_id = jsonl_env
        _append(path, b'{"subtype":"stop_hook_summary","pre_existing":true}\n')

        result = await _await_jsonl_flush(
            ad, agent_id, wait_for=MARKER, timeout=0.4,
        )
        assert result is False, (
            "Phase should only match bytes written AFTER function entry"
        )


# ---------------------------------------------------------------------------
# anyio backend — match the rest of the test suite (asyncio only).
# ---------------------------------------------------------------------------

@pytest.fixture
def anyio_backend():
    return "asyncio"
