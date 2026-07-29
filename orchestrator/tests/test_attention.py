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


def test_became_fires_only_on_transition_into_the_target(db_session, agent):
    """The push-storm fix: one fire per completed turn, not per message.

    `changed` on agent.last_message_at sent 8 notifications for a single
    agent turn. The end-of-turn edge is is_generating → False, and it must
    fire on entry only — not while the agent stays idle, and not when it
    starts generating again.
    """
    memo = {}
    cond = {"signal": "agent.is_generating", "op": "became", "value": False,
            "agent_id": agent.id}

    agent.generating_msg_id = "m1"          # mid-turn
    db_session.commit()
    assert evaluate(cond, db_session, memo) is False   # baseline
    assert evaluate(cond, db_session, memo) is False   # still generating

    agent.generating_msg_id = None          # stop hook fires
    db_session.commit()
    assert evaluate(cond, db_session, memo) is True    # the one fire

    # Stays idle — must go quiet, or we are back to spamming.
    assert evaluate(cond, db_session, memo) is False
    assert evaluate(cond, db_session, memo) is False

    # Next turn starts: leaving the target value must NOT fire.
    agent.generating_msg_id = "m2"
    db_session.commit()
    assert evaluate(cond, db_session, memo) is False

    # ...and completing that turn fires exactly once again.
    agent.generating_msg_id = None
    db_session.commit()
    assert evaluate(cond, db_session, memo) is True


def test_became_on_a_string_status(db_session, agent):
    memo = {}
    cond = {"signal": "agent.status", "op": "became", "value": "ERROR",
            "agent_id": agent.id}
    agent.status = AgentStatus.EXECUTING
    db_session.commit()
    assert evaluate(cond, db_session, memo) is False

    agent.status = AgentStatus.ERROR
    db_session.commit()
    assert evaluate(cond, db_session, memo) is True
    assert evaluate(cond, db_session, memo) is False, "must not repeat while in ERROR"


def test_became_does_not_fire_on_first_sight_even_if_already_at_target(db_session, agent):
    """Creating a watch while the condition already holds must stay silent."""
    agent.generating_msg_id = None          # already idle
    db_session.commit()
    memo = {}
    cond = {"signal": "agent.is_generating", "op": "became", "value": False,
            "agent_id": agent.id}
    assert evaluate(cond, db_session, memo) is False
    assert evaluate(cond, db_session, memo) is False


def test_became_requires_a_value():
    with pytest.raises(ConditionError, match="requires a 'value'"):
        validate({"signal": "agent.status", "op": "became"})


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


def test_daily_at_is_local_wall_clock_not_utc():
    """`daily_at: "09:00"` must land on 09:00 LOCAL, whatever the offset.

    Regression: the compiler originally UTC-converted the hour while this
    trigger read it as local, so a "9am digest" silently scheduled itself
    for late afternoon while the confirmation text still claimed 9am.
    """
    from datetime import timezone as _tz

    stored = TRIGGERS["every"].initial({"daily_at": "09:00"}, datetime.utcnow())
    assert stored is not None
    # Reinterpret the naive-UTC column value back into local time.
    local = stored.replace(tzinfo=_tz.utc).astimezone()
    assert (local.hour, local.minute) == (9, 0), (
        f"expected 09:00 local, got {local:%H:%M %Z}"
    )


def test_daily_at_has_no_microsecond_drift():
    stored = TRIGGERS["every"].initial({"daily_at": "09:00"}, datetime.utcnow())
    assert stored.second == 0 and stored.microsecond == 0


def test_daily_at_rejects_out_of_range_hour():
    assert TRIGGERS["every"].initial({"daily_at": "31:00"}, datetime.utcnow()) is None


def test_weekdays_filter_lands_on_an_allowed_day():
    """weekdays uses Monday=0..Sunday=6, matching datetime.weekday()."""
    from datetime import timezone as _tz

    for target in range(7):
        stored = TRIGGERS["every"].initial(
            {"daily_at": "09:00", "weekdays": [target]}, datetime.utcnow(),
        )
        assert stored is not None, f"no run time for weekday {target}"
        local = stored.replace(tzinfo=_tz.utc).astimezone()
        assert local.weekday() == target
        assert (local.hour, local.minute) == (9, 0)


def test_weekdays_advance_stays_within_the_filter():
    from datetime import timezone as _tz

    cfg = json.dumps({"daily_at": "09:00", "weekdays": [0, 1, 2, 3, 4]})
    job = AttentionJob(trigger_type="every", action_type="notify",
                       trigger_config=cfg, next_run_at=datetime.utcnow())
    nxt = TRIGGERS["every"].advance(job, datetime.utcnow(), None)
    local = nxt.replace(tzinfo=_tz.utc).astimezone()
    assert local.weekday() <= 4, "advance must skip the weekend"


def test_ignores_empty_or_bad_weekdays():
    """A malformed weekdays list means 'any day', not 'never'."""
    for bad in ([], "mon", [9, 12], None):
        stored = TRIGGERS["every"].initial(
            {"daily_at": "09:00", "weekdays": bad}, datetime.utcnow(),
        )
        assert stored is not None, f"weekdays={bad!r} should fall back to any day"


def test_preview_first_run_matches_what_the_scheduler_will_use():
    """The confirmation preview must come from the trigger, not from prose."""
    from attention.compiler import preview_first_run

    spec = {"trigger_type": "at", "trigger_config": {"at": "2026-08-01T12:00:00Z"}}
    assert preview_first_run(spec) == "2026-08-01T12:00:00"

    daily = {"trigger_type": "every", "trigger_config": {"daily_at": "09:00"}}
    preview = preview_first_run(daily)
    scheduled = TRIGGERS["every"].initial({"daily_at": "09:00"}, datetime.utcnow())
    assert preview == scheduled.isoformat()


def test_preview_returns_none_for_unschedulable_specs():
    from attention.compiler import preview_first_run

    assert preview_first_run({"trigger_type": "probe", "trigger_config": {}}) is None
    assert preview_first_run({"trigger_type": "nope", "trigger_config": {}}) is None


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
async def test_cooldown_coalesces_a_burst(db_session, monkeypatch):
    """min_interval_seconds is the backstop against notification spam."""
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    job = _notify_job(
        trigger_type="every",
        trigger_config=json.dumps({"interval_seconds": 60}),
        max_fires=None, min_interval_seconds=600,
    )
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 1
    assert len(sent) == 1

    # Force it due again well inside the cooldown window.
    job.next_run_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert await tick(db_session) == 0
    assert len(sent) == 1, "second fire inside the cooldown must be dropped"

    db_session.refresh(job)
    assert job.status == AttentionJob.STATUS_ACTIVE
    assert job.next_run_at is not None, "cooldown must not retire the job"
    assert job.fire_count == 1, "a coalesced fire must not count"


@pytest.mark.asyncio
async def test_cooldown_allows_a_fire_once_the_window_passes(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr("notify.notify", lambda *a, **k: sent.append(a) or "SEND")
    job = _notify_job(
        trigger_type="every",
        trigger_config=json.dumps({"interval_seconds": 60}),
        max_fires=None, min_interval_seconds=60,
        last_fired_at=datetime.utcnow() - timedelta(seconds=300),
    )
    db_session.add(job)
    db_session.commit()

    assert await tick(db_session) == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_signal_jobs_get_a_default_cooldown(db_session, sample_project):
    """A watch created through the API must not be able to spam by default."""
    from routers.attention import DEFAULT_SIGNAL_COOLDOWN_SECONDS, JobCreate, create_job

    a = Agent(project=sample_project.name, name="w", status=AgentStatus.IDLE)
    db_session.add(a)
    db_session.commit()

    out = create_job(JobCreate(
        kind="watch", title="watch it",
        trigger_type="signal",
        trigger_config={"condition": {
            "signal": "agent.is_generating", "op": "became",
            "value": False, "agent_id": a.id,
        }},
        action_type="notify", action_config={"title": "t", "body": "b"},
    ), db_session)
    assert out["min_interval_seconds"] == DEFAULT_SIGNAL_COOLDOWN_SECONDS

    # Clock-driven triggers carry their own period, so no floor is imposed.
    out2 = create_job(JobCreate(
        kind="reminder", title="later",
        trigger_type="at",
        trigger_config={"at": (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"},
        action_type="notify", action_config={"title": "t", "body": "b"},
    ), db_session)
    assert out2["min_interval_seconds"] is None


@pytest.mark.asyncio
async def test_tick_caps_fires_per_pass(db_session, monkeypatch):
    monkeypatch.setattr("notify.notify", lambda *a, **k: "SEND")
    from attention.scheduler import MAX_FIRES_PER_TICK
    for _ in range(MAX_FIRES_PER_TICK + 5):
        db_session.add(_notify_job())
    db_session.commit()

    fired = await tick(db_session)
    assert fired == MAX_FIRES_PER_TICK, "a backlog must not blow the 2s cadence"


# ---------------------------------------------------------------------------
# Conversational chat (bubble)
# ---------------------------------------------------------------------------

def _fake_claude(reply: dict):
    """A _claude_p stand-in returning a fixed JSON reply."""
    return lambda prompt: (0, json.dumps(reply), "")


@pytest.mark.asyncio
async def test_chat_plain_reply_has_no_spec(db_session, monkeypatch):
    from attention import compiler

    monkeypatch.setattr(
        compiler, "_claude_p", _fake_claude({"say": "I can remind you, watch agents, and send digests.", "job": None}),
    )
    out = await compiler.chat_request(
        [{"role": "user", "content": "what can you do?"}], db_session,
    )
    assert out["spec"] is None
    assert "remind" in out["say"]


@pytest.mark.asyncio
async def test_chat_job_proposal_is_validated_and_previewed(db_session, monkeypatch):
    from attention import compiler

    at = (datetime.utcnow() + timedelta(hours=2)).replace(microsecond=0)
    monkeypatch.setattr(compiler, "_claude_p", _fake_claude({
        "say": "Got it — I'll ping you in 2 hours.",
        "job": {
            "kind": "reminder", "title": "Check the build",
            "trigger_type": "at", "trigger_config": {"at": at.isoformat() + "Z"},
            "action_type": "notify", "action_config": {"title": "Check the build"},
            "confirm": "I'll notify you in two hours.",
        },
    }))
    out = await compiler.chat_request(
        [{"role": "user", "content": "remind me in 2h to check the build"}],
        db_session,
    )
    spec = out["spec"]
    assert spec is not None
    assert spec["trigger_type"] == "at"
    # source_text is the user's words, not the model's — it is what a
    # created job shows under "You said:".
    assert spec["source_text"] == "remind me in 2h to check the build"
    # preview comes from trigger arithmetic, and must match the given time
    assert spec["preview_next_run_at"] == at.isoformat()


@pytest.mark.asyncio
async def test_chat_invalid_job_degrades_to_conversation(db_session, monkeypatch):
    from attention import compiler

    monkeypatch.setattr(compiler, "_claude_p", _fake_claude({
        "say": "Setting that up.",
        "job": {
            "kind": "watch", "title": "bad",
            "trigger_type": "definitely_not_a_trigger", "trigger_config": {},
            "action_type": "notify", "action_config": {},
        },
    }))
    out = await compiler.chat_request(
        [{"role": "user", "content": "watch the thing"}], db_session,
    )
    # No hard failure mid-conversation: the say survives, annotated with why
    # the proposal was rejected, and no spec is offered for confirmation.
    assert out["spec"] is None
    assert "Setting that up." in out["say"]
    assert "unknown trigger" in out["say"]


@pytest.mark.asyncio
async def test_chat_requires_a_final_user_turn(db_session):
    from attention.compiler import CompileError, chat_request

    with pytest.raises(CompileError):
        await chat_request([], db_session)
    with pytest.raises(CompileError):
        await chat_request(
            [{"role": "assistant", "content": "hello there"}], db_session,
        )


@pytest.mark.asyncio
async def test_chat_prompt_carries_history_and_pending_jobs(db_session, monkeypatch):
    from attention import compiler

    db_session.add(_notify_job())
    db_session.commit()

    seen = {}

    def spy(prompt):
        seen["prompt"] = prompt
        return (0, json.dumps({"say": "ok", "job": None}), "")

    monkeypatch.setattr(compiler, "_claude_p", spy)
    await compiler.chat_request([
        {"role": "user", "content": "watch my agent"},
        {"role": "assistant", "content": "which agent do you mean?"},
        {"role": "user", "content": "the camera one"},
    ], db_session)

    prompt = seen["prompt"]
    # Both sides of the exchange must reach the model — the whole point of
    # chat over one-shot compile is that "the camera one" has context.
    assert "which agent do you mean?" in prompt
    assert "the camera one" in prompt
    assert "Pending jobs" in prompt
