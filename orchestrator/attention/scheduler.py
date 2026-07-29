"""The scheduler — one coroutine called from the dispatcher's 2s tick.

It knows nothing about reminders, watchers or digests. Its whole job:

    1. SELECT active jobs WHERE next_run_at <= now      (one indexed query)
    2. ask the trigger `due()`
    3. if due, await the action `run()`
    4. ask the trigger `advance()` for the next next_run_at

That is the entire coupling between this feature and the rest of the
orchestrator. A new trigger or action changes nothing here.

Failure policy: an action that raises records `last_error` and, for
non-recurring jobs, flips status to "error". Recurring jobs stay active but
keep the error visible — a transient push failure shouldn't silently kill a
daily digest, while a one-shot reminder that failed should stop pretending
it is still pending.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import AttentionJob
from utils import utcnow as _utcnow
from attention.registry import ACTIONS, TRIGGERS

logger = logging.getLogger("orchestrator.attention")

# Ceiling on how many jobs one tick will fire. Protects the 2s cadence if a
# clock jump or a long restart leaves a large backlog due at once — the
# remainder simply fires on the next tick.
MAX_FIRES_PER_TICK = 20


def _naive(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def tick(db: Session) -> int:
    """Run one scheduling pass. Returns the number of jobs fired.

    Never raises: the dispatcher's tick has a consecutive-failure breaker at
    10, and a single malformed job must not count against it.
    """
    now = _naive(_utcnow())

    try:
        due_jobs = (
            db.query(AttentionJob)
            .filter(
                AttentionJob.status == AttentionJob.STATUS_ACTIVE,
                AttentionJob.next_run_at != None,  # noqa: E711
                AttentionJob.next_run_at <= now,
            )
            .order_by(AttentionJob.next_run_at)
            .limit(MAX_FIRES_PER_TICK)
            .all()
        )
    except Exception:
        logger.exception("attention: failed to query due jobs")
        return 0

    if not due_jobs:
        return 0

    fired = 0
    for job in due_jobs:
        try:
            if await _process(job, now, db):
                fired += 1
        except Exception:
            logger.exception("attention: job %s failed unexpectedly", job.id)
            job.last_error = "internal error — see orchestrator log"
            # Park it rather than leaving it due, which would retry every
            # 2s forever and flood the log.
            job.status = AttentionJob.STATUS_ERROR
            job.next_run_at = None

    # The dispatcher's _tick commits at the end of the pass; flush now so a
    # later step in the same tick sees our writes.
    db.flush()
    return fired


async def _process(job: AttentionJob, now, db: Session) -> bool:
    """Evaluate and possibly fire one job. Returns True if the action ran."""
    # Expiry beats everything — an expired job never fires, even if due.
    if job.expires_at is not None and _naive(job.expires_at) <= now:
        logger.info("attention: job %s expired", job.id)
        job.status = AttentionJob.STATUS_DONE
        job.next_run_at = None
        return False

    trigger = TRIGGERS.get(job.trigger_type)
    if trigger is None:
        logger.warning(
            "attention: job %s references unknown trigger %r — parking",
            job.id, job.trigger_type,
        )
        job.status = AttentionJob.STATUS_ERROR
        job.last_error = f"unknown trigger {job.trigger_type!r}"
        job.next_run_at = None
        return False

    action = ACTIONS.get(job.action_type)
    if action is None:
        logger.warning(
            "attention: job %s references unknown action %r — parking",
            job.id, job.action_type,
        )
        job.status = AttentionJob.STATUS_ERROR
        job.last_error = f"unknown action {job.action_type!r}"
        job.next_run_at = None
        return False

    # `due` may mutate job.last_value (the `changed` baseline) — that write
    # must survive even when the condition is false, so it is never rolled
    # back below.
    try:
        is_due = trigger.due(job, now, db)
    except Exception:
        logger.exception("attention: trigger %s.due failed for job %s",
                         trigger.name, job.id)
        job.last_error = f"trigger {trigger.name} evaluation failed"
        job.next_run_at = trigger.advance(job, now, db) if trigger.recurring else None
        if job.next_run_at is None:
            job.status = AttentionJob.STATUS_ERROR
        return False

    if not is_due:
        job.next_run_at = trigger.advance(job, now, db)
        if job.next_run_at is None:
            job.status = AttentionJob.STATUS_DONE
        return False

    # ---- fire ----
    ran = False
    try:
        outcome = await action.run(job, db)
        job.last_error = None
        ran = True
        logger.info("attention: job %s fired (%s → %s): %s",
                    job.id, trigger.name, action.name, outcome)
    except Exception as exc:
        job.last_error = str(exc)[:500]
        logger.warning("attention: job %s action %s failed: %s",
                       job.id, action.name, exc)

    job.last_fired_at = now
    if ran:
        job.fire_count = (job.fire_count or 0) + 1

    # max_fires is the runaway backstop — checked after incrementing so a
    # max_fires=1 reminder retires immediately after its single fire.
    if job.max_fires is not None and job.fire_count >= job.max_fires:
        job.status = AttentionJob.STATUS_DONE
        job.next_run_at = None
        return ran

    job.next_run_at = trigger.advance(job, now, db)
    if job.next_run_at is None:
        # A one-shot whose action failed is an error, not a success.
        job.status = (
            AttentionJob.STATUS_DONE if ran else AttentionJob.STATUS_ERROR
        )
    return ran
