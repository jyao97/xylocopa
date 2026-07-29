"""Named, cheap, read-only views of app state that conditions may reference.

Every signal is a pure function of (db, params). None means "the referenced
thing doesn't exist" — the evaluator treats that as unsatisfied rather than
an error, so deleting an agent quietly stops its watchers instead of
spamming ``last_error``.

Cost discipline: these run inside the 2s dispatcher tick. Each read must be
a single indexed lookup or a COUNT. No JSONL parsing, no subprocess, no
network. Anything expensive belongs in an *action*, not a signal.

To add a signal: write the reader, wrap it in ``Signal``, pass it to
``register_signal``. It becomes usable in conditions and is automatically
advertised to the NL compiler via ``describe_registries()``.
"""

from __future__ import annotations

import logging

from models import Agent, Task
from attention.registry import Signal, register_signal

logger = logging.getLogger("orchestrator.attention")


# ---------------------------------------------------------------------------
# Agent signals
# ---------------------------------------------------------------------------

def _agent(db, params):
    aid = params.get("agent_id")
    return db.get(Agent, aid) if aid else None


register_signal(Signal(
    name="agent.status",
    description="Agent lifecycle status: STARTING | IDLE | EXECUTING | ERROR | STOPPED",
    value_kind="str",
    params=["agent_id"],
    read=lambda db, p: (
        a.status.value if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.unread_count",
    description="Number of unread assistant messages waiting in that agent's chat",
    value_kind="int",
    params=["agent_id"],
    read=lambda db, p: (
        a.unread_count if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.last_message_at",
    description=(
        "Timestamp of the agent's most recent message. The canonical "
        "'did this agent make progress' signal — pair it with op=changed"
    ),
    value_kind="datetime",
    params=["agent_id"],
    read=lambda db, p: (
        a.last_message_at if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.last_message_preview",
    description="First 200 chars of the agent's most recent message",
    value_kind="str",
    params=["agent_id"],
    read=lambda db, p: (
        a.last_message_preview if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.is_generating",
    description="True while the agent is mid-turn producing output",
    value_kind="bool",
    params=["agent_id"],
    read=lambda db, p: (
        a.is_generating if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.has_pending_suggestions",
    description="True when the agent has unreviewed progress-insight suggestions",
    value_kind="bool",
    params=["agent_id"],
    read=lambda db, p: (
        a.has_pending_suggestions if (a := _agent(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="agent.context_percent",
    description="Percent of the agent's context window consumed (0-100)",
    value_kind="int",
    params=["agent_id"],
    read=lambda db, p: (
        int(a.context_percent) if (a := _agent(db, p)) is not None
        and a.context_percent is not None else None
    ),
))


# ---------------------------------------------------------------------------
# Task signals
# ---------------------------------------------------------------------------

def _task(db, params):
    tid = params.get("task_id")
    return db.get(Task, tid) if tid else None


register_signal(Signal(
    name="task.status",
    description=(
        "Task status: INBOX | PLANNING | PENDING | EXECUTING | REVIEW | "
        "MERGING | CONFLICT | COMPLETE | REJECTED | CANCELLED | FAILED | TIMEOUT"
    ),
    value_kind="str",
    params=["task_id"],
    read=lambda db, p: (
        t.status.value if (t := _task(db, p)) is not None else None
    ),
))

register_signal(Signal(
    name="task.attempt_number",
    description="How many times this task has been retried",
    value_kind="int",
    params=["task_id"],
    read=lambda db, p: (
        getattr(t, "attempt_number", 0) if (t := _task(db, p)) is not None else None
    ),
))


# ---------------------------------------------------------------------------
# Aggregate signals — no entity id needed
# ---------------------------------------------------------------------------

def _count_agents_by_status(db, params):
    status = params.get("value") or params.get("status")
    q = db.query(Agent.id)
    if params.get("project"):
        q = q.filter(Agent.project == params["project"])
    if status:
        q = q.filter(Agent.status == status)
    return q.count()


register_signal(Signal(
    name="agents.count",
    description=(
        "How many agents match an optional status and/or project — e.g. "
        "params {status: EXECUTING} counts running agents"
    ),
    value_kind="int",
    params=["status (optional)", "project (optional)"],
    read=_count_agents_by_status,
))


def _count_unread(db, params):
    q = db.query(Agent.unread_count).filter(Agent.unread_count > 0)
    if params.get("project"):
        q = q.filter(Agent.project == params["project"])
    return sum(row[0] for row in q.all())


register_signal(Signal(
    name="agents.unread_total",
    description="Total unread messages across all agents (optionally one project)",
    value_kind="int",
    params=["project (optional)"],
    read=_count_unread,
))


def _count_tasks_by_status(db, params):
    status = params.get("value") or params.get("status")
    q = db.query(Task.id)
    if params.get("project"):
        q = q.filter(Task.project_name == params["project"])
    if status:
        q = q.filter(Task.status == status)
    return q.count()


register_signal(Signal(
    name="tasks.count",
    description="How many tasks match an optional status and/or project",
    value_kind="int",
    params=["status (optional)", "project (optional)"],
    read=_count_tasks_by_status,
))
