"""Built-in triggers: at · every · signal · probe.

Every trigger is three small functions (`initial`, `due`, `advance`) wrapped
in a `Trigger` and registered. The scheduler knows nothing about any
specific trigger — it only queries ``next_run_at`` and calls these hooks.

Timezone contract: SQLite stores naive UTC. `utils.utcnow()` returns an
*aware* UTC datetime. Every datetime this module writes to a column goes
through `_naive()` first, and every comparison is naive-vs-naive. Getting
this wrong is the classic offset-naive/aware TypeError that already bit
`routers/probes.py` (see its `now.replace(tzinfo=None)`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from attention.registry import Trigger, register_trigger

logger = logging.getLogger("orchestrator.attention")

# `signal` triggers re-evaluate on this cadence rather than every 2s tick.
# 10s keeps "agent made progress" feeling instant while capping condition
# evaluation at ~6 reads/min/job.
SIGNAL_POLL_SECONDS = 10

# Hard floor for `every` so a mis-compiled "every second" can't hammer the
# tick or the push endpoint.
MIN_INTERVAL_SECONDS = 60


def _naive(dt: datetime | None) -> datetime | None:
    """Drop tzinfo — everything persisted to SQLite is naive UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _parse_dt(raw) -> datetime | None:
    if isinstance(raw, datetime):
        return _naive(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return _naive(datetime.fromisoformat(raw.strip().replace("Z", "+00:00")))
    except ValueError:
        logger.warning("attention: unparseable datetime %r", raw)
        return None


def _config(job) -> dict:
    try:
        cfg = json.loads(job.trigger_config or "{}")
        return cfg if isinstance(cfg, dict) else {}
    except (TypeError, ValueError):
        logger.warning("attention: job %s has invalid trigger_config", job.id)
        return {}


# ---------------------------------------------------------------------------
# at — one-shot at an absolute time
# ---------------------------------------------------------------------------

def _at_initial(config: dict, now: datetime) -> datetime | None:
    return _parse_dt(config.get("at"))


def _at_due(job, now: datetime, db) -> bool:
    # next_run_at already gated this; nothing further to check.
    return True


def _at_advance(job, now: datetime, db) -> datetime | None:
    return None  # one-shot — retire


register_trigger(Trigger(
    name="at",
    description="Fire once at an absolute wall-clock time",
    config_schema='{"at": "<ISO-8601 UTC datetime>"}',
    initial=_at_initial,
    due=_at_due,
    advance=_at_advance,
    recurring=False,
))


# ---------------------------------------------------------------------------
# every — recurring interval, optionally anchored to a time of day
# ---------------------------------------------------------------------------

def _every_step(config: dict) -> timedelta:
    secs = config.get("interval_seconds")
    if isinstance(secs, (int, float)) and secs > 0:
        return timedelta(seconds=max(MIN_INTERVAL_SECONDS, int(secs)))
    # daily_at implies a 24h step
    return timedelta(days=1)


def _weekdays(config: dict) -> set[int] | None:
    """Allowed local weekdays as Python's Monday=0..Sunday=6, or None for any."""
    raw = config.get("weekdays")
    if not isinstance(raw, list) or not raw:
        return None
    days = {int(d) for d in raw if isinstance(d, (int, float)) and 0 <= int(d) <= 6}
    return days or None


def _local_offset() -> timedelta:
    """local wall clock − UTC, rounded to the minute.

    Recomputed on every call rather than cached so a DST boundary shifts
    subsequent runs instead of leaving a job an hour off for six months.
    Rounded because the two `now()` reads happen microseconds apart, and
    that jitter would otherwise show up in every stored run time.
    """
    delta = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
    return timedelta(minutes=round(delta.total_seconds() / 60))


def _next_daily(daily: str, config: dict, after_local: datetime) -> datetime | None:
    """Next local datetime matching HH:MM (and `weekdays`) strictly after `after_local`."""
    try:
        hh, mm = (int(x) for x in daily.split(":")[:2])
    except (TypeError, ValueError):
        logger.warning("attention: bad daily_at %r", daily)
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        logger.warning("attention: out-of-range daily_at %r", daily)
        return None

    allowed = _weekdays(config)
    candidate = after_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= after_local:
        candidate += timedelta(days=1)
    # At most 7 hops to land on an allowed weekday.
    for _ in range(7):
        if allowed is None or candidate.weekday() in allowed:
            return candidate
        candidate += timedelta(days=1)
    return None


def _every_initial(config: dict, now: datetime) -> datetime | None:
    now = _naive(now)
    start = _parse_dt(config.get("start_at"))
    if start and start > now:
        return start

    # `daily_at` is LOCAL wall-clock: users say "9am" meaning their own
    # clock, not UTC. Resolve against the local clock, then convert back to
    # the naive-UTC the column stores.
    if config.get("daily_at") is not None:
        daily = config["daily_at"]
        if not isinstance(daily, str) or ":" not in daily:
            logger.warning("attention: bad daily_at %r", daily)
            return None
        local_next = _next_daily(daily, config, datetime.now())
        # Return None rather than falling through to the interval branch: a
        # malformed daily_at silently becoming "24 hours from now" is a
        # wrong schedule the user has no way to notice. None makes the API
        # reject the job at creation time instead.
        if local_next is None:
            return None
        return _naive(local_next - _local_offset())

    return (now + _every_step(config)).replace(microsecond=0)


def _every_due(job, now: datetime, db) -> bool:
    return True


def _every_advance(job, now: datetime, db) -> datetime | None:
    cfg = _config(job)
    now = _naive(now)
    until = _parse_dt(cfg.get("until"))

    if cfg.get("daily_at") is not None:
        # Re-derive from the local clock each time so DST transitions and
        # `weekdays` filtering both stay correct across a long-lived job.
        daily = cfg["daily_at"]
        local_next = (
            _next_daily(daily, cfg, datetime.now())
            if isinstance(daily, str) and ":" in daily else None
        )
        nxt = _naive(local_next - _local_offset()) if local_next else None
    else:
        step = _every_step(cfg)
        base = _naive(job.next_run_at) or now
        nxt = (base + step).replace(microsecond=0)
        # If the server was asleep/restarting for several periods, skip
        # forward instead of replaying every missed slot as a notification burst.
        if nxt <= now:
            missed = int((now - nxt) / step) + 1
            nxt = nxt + step * missed

    if nxt is None:
        return None
    if until and nxt > until:
        return None
    return nxt


register_trigger(Trigger(
    name="every",
    description=(
        "Fire repeatedly — either every N seconds or at a fixed local "
        "time of day, optionally restricted to certain weekdays. Missed "
        "periods after downtime are skipped, not replayed"
    ),
    config_schema=(
        '{"interval_seconds": <int >= 60>}  OR  '
        '{"daily_at": "HH:MM", "weekdays": [<optional 0=Mon..6=Sun>]}  — '
        'daily_at is LOCAL wall-clock time, NOT UTC: write the hour the user '
        'actually said. Weekdays-only is [0,1,2,3,4]. '
        '(both forms accept optional "start_at" and "until" ISO-UTC datetimes)'
    ),
    initial=_every_initial,
    due=_every_due,
    advance=_every_advance,
    recurring=True,
))


# ---------------------------------------------------------------------------
# signal — fire when a condition over named SIGNALS becomes true
# ---------------------------------------------------------------------------

def _signal_initial(config: dict, now: datetime) -> datetime | None:
    # Evaluate on the next poll slot rather than immediately: a `changed`
    # leaf needs one pass to record its baseline anyway.
    return _naive(now) + timedelta(seconds=SIGNAL_POLL_SECONDS)


def _signal_due(job, now: datetime, db) -> bool:
    from attention.conditions import evaluate

    cfg = _config(job)
    condition = cfg.get("condition")
    if not isinstance(condition, dict):
        logger.warning("attention: job %s has no condition", job.id)
        return False

    try:
        memo = json.loads(job.last_value or "{}")
        if not isinstance(memo, dict):
            memo = {}
    except (TypeError, ValueError):
        memo = {}

    result = evaluate(condition, db, memo)
    # Persist the baseline regardless of outcome — `changed` leaves depend
    # on it advancing every evaluation, not only on fires.
    job.last_value = json.dumps(memo)
    return result


def _signal_advance(job, now: datetime, db) -> datetime | None:
    cfg = _config(job)
    poll = cfg.get("poll_seconds")
    step = int(poll) if isinstance(poll, (int, float)) and poll >= 5 else SIGNAL_POLL_SECONDS
    return _naive(now) + timedelta(seconds=step)


register_trigger(Trigger(
    name="signal",
    description=(
        "Fire when a JSON condition over named SIGNALS becomes true. "
        "Use op 'changed' for progress-style watches — it is edge-triggered, "
        "so it fires on transitions rather than continuously"
    ),
    config_schema=(
        '{"condition": {"signal": "<name>", "op": "eq|ne|gt|gte|lt|lte|in|'
        'contains|changed", "value": <literal>, ...signal params}, '
        '"poll_seconds": <optional int >= 5>}  — combine leaves with '
        '{"all": [...]}, {"any": [...]}, {"not": {...}}'
    ),
    initial=_signal_initial,
    due=_signal_due,
    advance=_signal_advance,
    recurring=True,
))


# ---------------------------------------------------------------------------
# probe — fired from outside, never by the clock
# ---------------------------------------------------------------------------

def _probe_initial(config: dict, now: datetime) -> datetime | None:
    return None  # never scheduled; an external POST fires it


def _probe_due(job, now: datetime, db) -> bool:
    return False


def _probe_advance(job, now: datetime, db) -> datetime | None:
    return None


register_trigger(Trigger(
    name="probe",
    description=(
        "Fired by an external HTTP POST rather than the clock — for CI "
        "webhooks and similar. Not schedulable; the scheduler ignores it"
    ),
    config_schema='{"probe_id": "<id of a Probe row>"}',
    initial=_probe_initial,
    due=_probe_due,
    advance=_probe_advance,
    recurring=True,
))


# ---------------------------------------------------------------------------
# Reserved seam: external calendar
# ---------------------------------------------------------------------------
# An external-calendar trigger fits here with no scheduler change. It would
# be a `Trigger` whose `due()` compares the current time against a cached
# event start pulled by a separate background refresher, and whose
# `advance()` returns the next cached event — i.e. the same shape as
# `every`, with the event list coming from an OAuth-backed sync instead of
# arithmetic. Deliberately NOT implemented: Google Calendar needs an OAuth
# flow, token storage and a new dependency, all of which need explicit
# approval per CLAUDE.md. In-app date/time picking covers today's use case
# through the `at` trigger.
