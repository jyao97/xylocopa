"""The three registries that make the attention layer extensible.

Adding a capability should never require touching the scheduler, the API
router, or the frontend's job list. It should be one decorated function.

    TRIGGERS  — when does a job fire?
    ACTIONS   — what happens when it fires?
    SIGNALS   — what app state can a condition read?

`describe_registries()` renders the registries as text for the NL compiler
prompt, so a newly registered trigger/action/signal automatically becomes
something the assistant knows how to produce. That is the single most
important property here: the LLM prompt is *derived* from the registries
rather than duplicated alongside them, so the two can't drift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger("orchestrator.attention")


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

class DueFn(Protocol):
    def __call__(self, job: Any, now: datetime, db: Any) -> bool: ...


class AdvanceFn(Protocol):
    def __call__(self, job: Any, now: datetime, db: Any) -> datetime | None: ...


@dataclass(frozen=True)
class Trigger:
    """When a job fires.

    due(job, now, db)
        True if the job should fire right now. Called only when
        ``next_run_at <= now``, so cheap triggers can just ``return True``
        and let ``next_run_at`` do the work. Condition triggers do their
        real evaluation here.

    advance(job, now, db)
        The next ``next_run_at`` after a fire (or after a `due()` that
        returned False). Return None to retire the job.

    initial(config, now)
        The first ``next_run_at`` at creation time.

    A trigger MUST NOT perform I/O beyond cheap indexed DB reads: `due` runs
    on every dispatcher tick for every due job.
    """
    name: str
    description: str
    config_schema: str          # human-readable, fed to the NL compiler
    due: DueFn
    advance: AdvanceFn
    initial: Callable[[dict, datetime], datetime | None]
    # Recurring triggers keep firing; one-shots retire after the first fire.
    recurring: bool = False


TRIGGERS: dict[str, Trigger] = {}


def register_trigger(trigger: Trigger) -> Trigger:
    if trigger.name in TRIGGERS:
        raise ValueError(f"duplicate trigger {trigger.name!r}")
    TRIGGERS[trigger.name] = trigger
    return trigger


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class RunFn(Protocol):
    def __call__(self, job: Any, db: Any) -> Awaitable[str]: ...


@dataclass(frozen=True)
class Action:
    """What happens when a job fires.

    run(job, db) -> str
        Coroutine. Returns a short outcome string for the log / last_error
        surface. Raising is allowed: the scheduler catches, records
        ``last_error`` and — for one-shot jobs — marks the job errored
        rather than retrying forever.
    """
    name: str
    description: str
    config_schema: str
    run: RunFn
    # Actions that spend tokens are opt-in for recurring triggers so a
    # mis-compiled "every 2 minutes" job can't quietly burn budget.
    costly: bool = False


ACTIONS: dict[str, Action] = {}


def register_action(action: Action) -> Action:
    if action.name in ACTIONS:
        raise ValueError(f"duplicate action {action.name!r}")
    ACTIONS[action.name] = action
    return action


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """A named, cheap, read-only view of app state a condition may reference.

    Conditions are JSON expressions over these names — never user code and
    never a free-form SQL/Python string. That keeps `signal` triggers safe
    (nothing to inject), testable (each signal is a pure function), and
    cheap (no LLM call at evaluation time).

    read(db, params) -> value
        `params` comes from the condition node, so a signal can be
        parameterized (e.g. ``{"signal": "agent.status", "agent_id": "…"}``).
        Return None when the referenced entity doesn't exist; the evaluator
        treats None as "condition not satisfied" rather than an error.
    """
    name: str
    description: str
    value_kind: str             # "str" | "int" | "datetime" | "bool"
    read: Callable[[Any, dict], Any]
    params: list[str] = field(default_factory=list)


SIGNALS: dict[str, Signal] = {}


def register_signal(signal: Signal) -> Signal:
    if signal.name in SIGNALS:
        raise ValueError(f"duplicate signal {signal.name!r}")
    SIGNALS[signal.name] = signal
    return signal


# ---------------------------------------------------------------------------
# Prompt surface
# ---------------------------------------------------------------------------

def describe_registries() -> str:
    """Render the registries as the capability catalogue for the compiler.

    Called at compile time (not per tick), so string building cost is
    irrelevant. Keeping this derived from the live registries is what makes
    "register one function" genuinely sufficient to extend the assistant.
    """
    lines: list[str] = ["TRIGGERS:"]
    for t in TRIGGERS.values():
        tag = " (recurring)" if t.recurring else ""
        lines.append(f"  {t.name}{tag} — {t.description}")
        lines.append(f"      trigger_config: {t.config_schema}")

    lines.append("")
    lines.append("ACTIONS:")
    for a in ACTIONS.values():
        tag = " (spends tokens)" if a.costly else ""
        lines.append(f"  {a.name}{tag} — {a.description}")
        lines.append(f"      action_config: {a.config_schema}")

    lines.append("")
    lines.append("SIGNALS (readable by the `signal` trigger's condition):")
    for s in SIGNALS.values():
        p = f" params: {', '.join(s.params)}" if s.params else ""
        lines.append(f"  {s.name} -> {s.value_kind} — {s.description}{p}")

    return "\n".join(lines)
