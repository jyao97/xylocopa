"""Attention assistant — registries, condition DSL, and scheduler.

The condition tests carry most of the weight. `changed` is edge-triggered,
and the two ways to get it wrong are both user-visible disasters: fire on
every tick (notification spam) or fire the instant a watcher is created
(a phantom alert for something that never happened).
"""

import json
from datetime import datetime, timedelta

import pytest

from models import Agent, AgentStatus, AttentionJob, Task, TaskStatus

import attention  # noqa: F401  — populates registries
from attention.conditions import ConditionError, evaluate, summarize, validate
from attention.registry import ACTIONS, SIGNALS, TRIGGERS
from attention.scheduler import tick


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

def test_builtin_triggers_and_actions_registered():
    assert set(TRIGGERS) == {"at", "every", "signal", "probe"}
    assert set(ACTIONS) == {"notify", "message_agent", "dispatch_task", "run_prompt"}


def test_every_and_signal_are_recurring_at_is_not():
    assert TRIGGERS["every"].recurring is True
    assert TRIGGERS["signal"].recurring is True
    assert TRIGGERS["at"].recurring is False


def test_only_run_prompt_is_marked_costly():
    costly = {name for name, a in ACTIONS.items() if a.costly}
    assert costly == {"run_prompt"}


def test_describe_registries_lists_every_entry():
    from attention.registry import describe_registries
    text = describe_registries()
    for name in list(TRIGGERS) + list(ACTIONS) + list(SIGNALS):
        assert name in text, f"{name} missing from the compiler's capability catalogue"


# ---------------------------------------------------------------------------
# Condition validation
# ---------------------------------------------------------------------------

def test_validate_accepts_a_simple_leaf():
    validate({"signal": "agent.status", "op": "eq", "value": "IDLE", "agent_id": "a1"})


def test_validate_rejects_unknown_signal():
    with pytest.raises(ConditionError, match="unknown signal"):
        validate({"signal": "agent.__wat__", "op": "eq", "value": 1})


def test_validate_rejects_unknown_op():
    with pytest.raises(ConditionError, match="unknown op"):
        validate({"signal": "agent.status", "op": "regex", "value": ".*"})


def test_validate_requires_value_except_for_changed():
    with pytest.raises(ConditionError, match="requires a 'value'"):
        validate({"signal": "agent.status", "op": "eq"})
    validate({"signal": "agent.status", "op": "changed", "agent_id": "a1"})


def test_validate_rejects_in_with_scalar_value():
    with pytest.raises(ConditionError, match="requires 'value' to be a list"):
        validate({"signal": "agent.status", "op": "in", "value": "IDLE"})


def test_validate_rejects_empty_boolean_group():
    with pytest.raises(ConditionError, match="non-empty list"):
        validate({"all": []})


def test_validate_rejects_excessive_depth():
    node = {"signal": "agent.status", "op": "eq", "value": "IDLE"}
    for _ in range(8):
        node = {"not": node}
    with pytest.raises(ConditionError, match="nested too deep"):
        validate(node)


def test_validate_rejects_oversized_expression():
    leaf = {"signal": "agent.status", "op": "eq", "value": "IDLE"}
    with pytest.raises(ConditionError, match="too large"):
        validate({"all": [dict(leaf) for _ in range(60)]})


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent(db_session, sample_project):
    a = Agent(
        project=sample_project.name, name="watched",
        status=AgentStatus.EXECUTING, unread_count=0,
    )
    db_session.add(a)
    db_session.commit()
    return a


def test_eq_matches_agent_status(db_session, agent):
    cond = {"signal": "agent.status", "op": "eq", "value": "EXECUTING",
            "agent_id": agent.id}
    assert evaluate(cond, db_session, {}) is True
    cond["value"] = "IDLE"
    assert evaluate(cond, db_session, {}) is False


def test_missing_agent_evaluates_false_not_error(db_session):
    cond = {"signal": "agent.status", "op": "eq", "value": "IDLE",
            "agent_id": "nope00000000"}
    assert evaluate(cond, db_session, {}) is False


def test_numeric_ops_coerce(db_session, agent):
    agent.unread_count = 5
    db_session.commit()
    base = {"signal": "agent.unread_count", "agent_id": agent.id}
    assert evaluate({**base, "op": "gt", "value": 3}, db_session, {}) is True
    assert evaluate({**base, "op": "lte", "value": 5}, db_session, {}) is True
    assert evaluate({**base, "op": "gt", "value": 9}, db_session, {}) is False


def test_in_and_contains(db_session, agent):
    assert evaluate(
        {"signal": "agent.status", "op": "in", "value": ["IDLE", "EXECUTING"],
         "agent_id": agent.id}, db_session, {},
    ) is True
    agent.last_message_preview = "Finished the REFACTOR now"
    db_session.commit()
    assert evaluate(
        {"signal": "agent.last_message_preview", "op": "contains",
         "value": "refactor", "agent_id": agent.id}, db_session, {},
    ) is True


def test_boolean_groups(db_session, agent):
    agent.unread_count = 2
    db_session.commit()
    yes = {"signal": "agent.status", "op": "eq", "value": "EXECUTING",
           "agent_id": agent.id}
    no = {"signal": "agent.unread_count", "op": "gt", "value": 100,
          "agent_id": agent.id}
    assert evaluate({"all": [yes, no]}, db_session, {}) is False
    assert evaluate({"any": [yes, no]}, db_session, {}) is True
    assert evaluate({"not": no}, db_session, {}) is True


# --- the edge-trigger contract ---------------------------------------------

def test_changed_does_not_fire_on_first_sight(db_session, agent):
    """A brand-new watcher must not fire just because it has no baseline."""
    agent.last_message_at = datetime(2026, 7, 29, 10, 0, 0)
    db_session.commit()
    memo = {}
    cond = {"signal": "agent.last_message_at", "op": "changed",
            "agent_id": agent.id}
    assert evaluate(cond, db_session, memo) is False
    assert memo, "first evaluation must record a baseline"


def test_changed_fires_once_per_transition_not_every_tick(db_session, agent):
    """The notification-spam regression: firing while the value holds still."""
    agent.last_message_at = datetime(2026, 7, 29, 10, 0, 0)
    db_session.commit()
    memo = {}
    cond = {"signal": "agent.last_message_at", "op": "changed",
            "agent_id": agent.id}

    assert evaluate(cond, db_session, memo) is False   # baseline
    assert evaluate(cond, db_session, memo) is False   # unchanged
    assert evaluate(cond, db_session, memo) is False   # still unchanged

    agent.last_message_at = datetime(2026, 7, 29, 10, 5, 0)
    db_session.commit()
    assert evaluate(cond, db_session, memo) is True    # the transition

    # Value now holds at the new timestamp — must go quiet again.
    assert evaluate(cond, db_session, memo) is False
    assert evaluate(cond, db_session, memo) is False


def test_changed_keeps_separate_baselines_per_agent(db_session, sample_project):
    """Two watchers on the same signal must not share one memo slot."""
    a = Agent(project=sample_project.name, name="a", status=AgentStatus.IDLE,
              last_message_at=datetime(2026, 7, 29, 9, 0, 0))
    b = Agent(project=sample_project.name, name="b", status=AgentStatus.IDLE,
              last_message_at=datetime(2026, 7, 29, 9, 0, 0))
    db_session.add_all([a, b])
    db_session.commit()

    memo = {}
    ca = {"signal": "agent.last_message_at", "op": "changed", "agent_id": a.id}
    cb = {"signal": "agent.last_message_at", "op": "changed", "agent_id": b.id}
    evaluate(ca, db_session, memo)
    evaluate(cb, db_session, memo)
    assert len(memo) == 2, "each agent needs its own baseline"

    a.last_message_at = datetime(2026, 7, 29, 9, 30, 0)
    db_session.commit()
    assert evaluate(ca, db_session, memo) is True
    assert evaluate(cb, db_session, memo) is False, "b did not change"


def test_all_group_updates_every_changed_baseline(db_session, agent):
    """`all` must not short-circuit past a `changed` leaf.

    If it did, the leaf's baseline would go stale while a sibling was false,
    then report a spurious change the moment the sibling flipped true.
    """
    agent.status = AgentStatus.IDLE          # makes the guard leaf false
    agent.last_message_at = datetime(2026, 7, 29, 10, 0, 0)
    db_session.commit()

    guard = {"signal": "agent.status", "op": "eq", "value": "EXECUTING",
             "agent_id": agent.id}
    watch = {"signal": "agent.last_message_at", "op": "changed",
             "agent_id": agent.id}
    cond = {"all": [guard, watch]}
    memo = {}

    evaluate(cond, db_session, memo)                       # baseline recorded
    agent.last_message_at = datetime(2026, 7, 29, 10, 1, 0)
    db_session.commit()
    assert evaluate(cond, db_session, memo) is False       # guard still false

    # Guard flips true, but the message time has NOT moved since last look.
    agent.status = AgentStatus.EXECUTING
    db_session.commit()
    assert evaluate(cond, db_session, memo) is False, (
        "stale baseline leaked a phantom change"
    )


def test_summarize_is_human_readable():
    text = summarize({"all": [
        {"signal": "agent.status", "op": "eq", "value": "IDLE"},
        {"signal": "agent.last_message_at", "op": "changed"},
    ]})
    assert "agent.status eq" in text
    assert "changes" in text


# ---------------------------------------------------------------------------
# Aggregate signals
# ---------------------------------------------------------------------------

def test_aggregate_signals_count(db_session, sample_project):
    db_session.add_all([
        Agent(project=sample_project.name, name="x", status=AgentStatus.EXECUTING),
        Agent(project=sample_project.name, name="y", status=AgentStatus.IDLE,
              unread_count=3),
    ])
    db_session.add(Task(title="t", project_name=sample_project.name,
                        status=TaskStatus.REVIEW))
    db_session.commit()

    assert SIGNALS["agents.count"].read(db_session, {"status": "EXECUTING"}) == 1
    assert SIGNALS["agents.unread_total"].read(db_session, {}) == 3
    assert SIGNALS["tasks.count"].read(db_session, {"status": "REVIEW"}) == 1


# ---------------------------------------------------------------------------
# Trigger scheduling arithmetic
# ---------------------------------------------------------------------------

def test_at_trigger_initial_parses_iso():
    when = TRIGGERS["at"].initial({"at": "2026-08-01T12:00:00Z"}, datetime.utcnow())
    assert when == datetime(2026, 8, 1, 12, 0, 0)
    assert when.tzinfo is None, "column values must be naive UTC"


def test_at_trigger_retires_after_firing():
    job = AttentionJob(trigger_type="at", action_type="notify")
    assert TRIGGERS["at"].advance(job, datetime.utcnow(), None) is None


def test_every_enforces_minimum_interval():
    now = datetime(2026, 7, 29, 12, 0, 0)
    job = AttentionJob(
        trigger_type="every", action_type="notify",
        trigger_config=json.dumps({"interval_seconds": 1}),
        next_run_at=now,
    )
    nxt = TRIGGERS["every"].advance(job, now, None)
    assert nxt - now >= timedelta(seconds=60), "a 1s interval must be clamped"


def test_every_skips_missed_periods_instead_of_replaying():
    """After downtime, the user gets one notification — not a backlog burst."""
    cfg = json.dumps({"interval_seconds": 3600})
    last = datetime(2026, 7, 29, 0, 0, 0)
    now = last + timedelta(hours=10)
    job = AttentionJob(trigger_type="every", action_type="notify",
                       trigger_config=cfg, next_run_at=last)
    nxt = TRIGGERS["every"].advance(job, now, None)
    assert nxt > now, "next run must be in the future, not a missed slot"
    assert nxt - now <= timedelta(hours=1)


def test_every_stops_at_until():
    cfg = json.dumps({
        "interval_seconds": 3600,
        "until": "2026-07-29T02:00:00",
    })
    job = AttentionJob(trigger_type="every", action_type="notify",
                       trigger_config=cfg,
                       next_run_at=datetime(2026, 7, 29, 2, 0, 0))
    assert TRIGGERS["every"].advance(
        job, datetime(2026, 7, 29, 2, 0, 0), None,
    ) is None


def test_probe_trigger_is_never_scheduled():
    assert TRIGGERS["probe"].initial({}, datetime.utcnow()) is None
    job = AttentionJob(trigger_type="probe", action_type="notify")
    assert TRIGGERS["probe"].due(job, datetime.utcnow(), None) is False


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _notify_job(**kw):
    defaults = dict(
        kind="reminder", title="ping", trigger_type="at", action_type="notify",
        trigger_config=json.dumps({"at": "2026-01-01T00:00:00"}),
        action_config=json.dumps({"title": "ping", "body": "body"}),
        status=AttentionJob.STATUS_ACTIVE, max_fires=1,
        next_run_at=datetime.utcnow() - timedelta(minutes=1),
    )
    defaults.update(kw)
    return AttentionJob(**defaults)


@pytest.mark.asyncio
async def test_due_job_fires_and_retires(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("notify.notify",
                        lambda *a, **k: sent.append(a) or "SEND")
    job = _notify_job()
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 1
    db_session.refresh(job)
    assert len(sent) == 1
    assert job.fire_count == 1
    assert job.status == AttentionJob.STATUS_DONE
    assert job.next_run_at is None


@pytest.mark.asyncio
async def test_future_job_does_not_fire(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    job = _notify_job(next_run_at=datetime.utcnow() + timedelta(hours=2))
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 0
    assert sent == []
    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_ACTIVE


@pytest.mark.asyncio
async def test_paused_job_does_not_fire(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    db_session.add(_notify_job(status=AttentionJob.STATUS_PAUSED))
    db_session.commit()
    assert await tick(db_session) == 0
    assert sent == []


@pytest.mark.asyncio
async def test_expired_job_is_retired_without_firing(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    job = _notify_job(expires_at=datetime.utcnow() - timedelta(minutes=5))
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 0
    assert sent == []
    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_DONE


@pytest.mark.asyncio
async def test_recurring_job_reschedules_and_stays_active(db_session, monkeypatch):
    monkeypatch.setattr("notify.notify", lambda *a, **k: "SEND")
    job = _notify_job(
        trigger_type="every",
        trigger_config=json.dumps({"interval_seconds": 3600}),
        max_fires=None,
    )
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 1
    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_ACTIVE
    assert job.next_run_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_max_fires_retires_a_recurring_job(db_session, monkeypatch):
    monkeypatch.setattr("notify.notify", lambda *a, **k: "SEND")
    job = _notify_job(
        trigger_type="every",
        trigger_config=json.dumps({"interval_seconds": 60}),
        max_fires=2, fire_count=1,
    )
    db_session.add(job)
    db_session.commit()

    await tick(db_session)
    db_session.refresh(job)
    assert job.fire_count == 2
    assert job.status == AttentionJob.STATUS_DONE


@pytest.mark.asyncio
async def test_failing_action_marks_one_shot_errored(db_session, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("push endpoint gone")
    monkeypatch.setattr("notify.notify", _boom)
    job = _notify_job()
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 0
    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_ERROR
    assert "push endpoint gone" in (job.last_error or "")


@pytest.mark.asyncio
async def test_unknown_trigger_is_parked_not_retried(db_session):
    job = _notify_job(trigger_type="from-the-future")
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 0
    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_ERROR
    assert job.next_run_at is None, "a parked job must not stay due forever"


@pytest.mark.asyncio
async def test_signal_job_persists_baseline_without_firing(db_session, agent, monkeypatch):
    """First evaluation records the baseline and stays silent."""
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    agent.last_message_at = datetime(2026, 7, 29, 10, 0, 0)
    db_session.commit()

    job = _notify_job(
        trigger_type="signal", max_fires=None,
        trigger_config=json.dumps({"condition": {
            "signal": "agent.last_message_at", "op": "changed",
            "agent_id": agent.id,
        }}),
    )
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 0
    db_session.refresh(job)
    assert sent == []
    assert json.loads(job.last_value or "{}"), "baseline must persist"
    assert job.status == AttentionJob.STATUS_ACTIVE

    # Progress happens; make the job due again and confirm it fires once.
    agent.last_message_at = datetime(2026, 7, 29, 10, 6, 0)
    job.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    assert await tick(db_session) == 1
    assert len(sent) == 1

    # Nothing further changed — the next pass must stay quiet.
    job.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert await tick(db_session) == 0
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_tick_caps_fires_per_pass(db_session, monkeypatch):
    monkeypatch.setattr("notify.notify", lambda *a, **k: "SEND")
    from attention.scheduler import MAX_FIRES_PER_TICK
    for _ in range(MAX_FIRES_PER_TICK + 5):
        db_session.add(_notify_job())
    db_session.commit()

    fired = await tick(db_session)
    assert fired == MAX_FIRES_PER_TICK, "a backlog must not blow the 2s cadence"
