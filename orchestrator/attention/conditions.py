"""Condition DSL — a tiny JSON expression language over named SIGNALS.

Why a DSL instead of letting the LLM emit code or a SQL fragment: the
compiler output is untrusted (it is LLM-generated text derived from user
input). A closed grammar over a fixed signal registry means there is
nothing to inject, every node is validated before it is ever stored, and
evaluation is pure Python that costs microseconds on the 2s tick.

Grammar (all nodes are dicts):

    leaf   {"signal": <name>, "op": <op>, "value": <literal>, ...params}
    all    {"all": [node, ...]}          — every child true
    any    {"any": [node, ...]}          — at least one child true
    not    {"not": node}

Operators:

    eq ne gt gte lt lte      — scalar comparison
    in                       — value is a list, signal must be a member
    contains                 — case-insensitive substring (str signals)
    changed                  — TRUE when the signal differs from the value
                               observed on the previous evaluation
    became                   — TRUE only on the evaluation where the signal
                               *transitions into* `value` (rising/falling edge)

Both `changed` and `became` are edge-triggered: state lives in
``AttentionJob.last_value`` (a dict keyed by signal fingerprint) which the
evaluator reads and rewrites through the ``memo`` mapping it is handed. The
first evaluation only records a baseline and reports False — otherwise every
watcher would fire once the instant it was created.

Prefer `became` over `changed` for anything user-facing. `changed` on
`agent.last_message_at` looks like "notify me when the agent makes progress"
but actually fires on every intermediate message inside a single turn — it
sent 8 pushes for one agent turn in testing. The agent-finished-a-turn edge
is `agent.is_generating became false` (equivalently `agent.status became
"IDLE"`), which is exactly what the stop hook writes, once per turn.
"""

from __future__ import annotations

import logging
from datetime import datetime

from attention.registry import SIGNALS

logger = logging.getLogger("orchestrator.attention")

SCALAR_OPS = {"eq", "ne", "gt", "gte", "lt", "lte"}
# Ops that consult the previous evaluation's value and therefore need a memo
# slot. `changed` needs no `value`; `became` does.
EDGE_OPS = {"changed", "became"}
ALL_OPS = SCALAR_OPS | {"in", "contains"} | EDGE_OPS

MAX_DEPTH = 5
MAX_NODES = 40


class ConditionError(ValueError):
    """Malformed condition — raised by validate(), never by evaluate()."""


# ---------------------------------------------------------------------------
# Validation — run once, before a job is stored
# ---------------------------------------------------------------------------

def validate(node, _depth: int = 0, _budget: list[int] | None = None) -> None:
    """Raise ConditionError unless `node` is a well-formed condition.

    Called from the API layer on every create/update, so a bad compile can
    never reach the scheduler. Depth and node budgets stop a pathological
    (or adversarial) nested expression from making tick evaluation costly.
    """
    if _budget is None:
        _budget = [MAX_NODES]
    _budget[0] -= 1
    if _budget[0] < 0:
        raise ConditionError(f"condition too large (max {MAX_NODES} nodes)")
    if _depth > MAX_DEPTH:
        raise ConditionError(f"condition nested too deep (max {MAX_DEPTH})")
    if not isinstance(node, dict):
        raise ConditionError(f"condition node must be an object, got {type(node).__name__}")

    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ConditionError(f"{key!r} must be a non-empty list")
        for child in children:
            validate(child, _depth + 1, _budget)
        return

    if "not" in node:
        validate(node["not"], _depth + 1, _budget)
        return

    name = node.get("signal")
    if not name:
        raise ConditionError("leaf node needs a 'signal' key")
    if name not in SIGNALS:
        raise ConditionError(
            f"unknown signal {name!r} — known: {', '.join(sorted(SIGNALS))}"
        )
    op = node.get("op")
    if op not in ALL_OPS:
        raise ConditionError(
            f"unknown op {op!r} for signal {name!r} — known: {', '.join(sorted(ALL_OPS))}"
        )
    if op == "in" and not isinstance(node.get("value"), list):
        raise ConditionError("op 'in' requires 'value' to be a list")
    if op != "changed" and "value" not in node:
        raise ConditionError(f"op {op!r} requires a 'value'")


# ---------------------------------------------------------------------------
# Evaluation — runs on the dispatcher tick
# ---------------------------------------------------------------------------

def _fingerprint(node: dict) -> str:
    """Stable key for a leaf's `changed` memo slot.

    Includes the params so two leaves watching different agents through the
    same signal don't share one baseline.
    """
    name = node.get("signal", "?")
    extras = sorted(
        f"{k}={v}" for k, v in node.items()
        if k not in ("signal", "op", "value")
    )
    return name + ("|" + ",".join(extras) if extras else "")


def _coerce(raw, expected):
    """Best-effort align a signal value with the literal it's compared to.

    Datetimes arrive as datetime objects but the compiler emits ISO strings,
    and SQLite hands back naive UTC while `utcnow()` is aware — normalize
    both sides rather than letting a TypeError kill the tick.
    """
    if isinstance(raw, datetime):
        raw = raw.replace(tzinfo=None)
        if isinstance(expected, str):
            try:
                exp = datetime.fromisoformat(expected.replace("Z", "+00:00"))
                return raw, exp.replace(tzinfo=None)
            except ValueError:
                return raw.isoformat(), expected
    if isinstance(expected, bool) and not isinstance(raw, bool):
        return bool(raw), expected
    if isinstance(expected, (int, float)) and isinstance(raw, str):
        try:
            return type(expected)(raw), expected
        except (TypeError, ValueError):
            return raw, expected
    return raw, expected


def _serialize(value):
    """Reduce a signal value to something JSON-storable for the memo."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def evaluate(node, db, memo: dict) -> bool:
    """Evaluate a validated condition. Never raises on data problems.

    `memo` is the job's ``last_value`` dict, mutated in place: the caller
    persists it after evaluation so `changed` leaves have a baseline next
    tick. A missing signal (deleted agent) yields False, which retires
    watchers naturally instead of erroring in a loop.
    """
    if "all" in node:
        # Note: not short-circuited — every `changed` leaf must update its
        # baseline on every tick, otherwise a leaf sitting behind a false
        # sibling would go stale and fire spuriously once the sibling flips.
        results = [evaluate(c, db, memo) for c in node["all"]]
        return all(results)
    if "any" in node:
        results = [evaluate(c, db, memo) for c in node["any"]]
        return any(results)
    if "not" in node:
        return not evaluate(node["not"], db, memo)

    signal = SIGNALS.get(node.get("signal", ""))
    if signal is None:
        return False

    params = {k: v for k, v in node.items() if k not in ("signal", "op")}
    try:
        raw = signal.read(db, params)
    except Exception:
        logger.warning("signal %s read failed", signal.name, exc_info=True)
        return False

    op = node.get("op")

    if op in EDGE_OPS:
        key = _fingerprint(node)
        current = _serialize(raw)
        had_baseline = key in memo
        previous = memo.get(key)
        memo[key] = current
        # First sight records the baseline only — a watcher must not fire
        # the moment it is created just because it has never looked before.
        if not had_baseline:
            return False
        if op == "changed":
            return current != previous
        # became: only the transition INTO `value` counts. Staying at the
        # target value must stay silent, and so must leaving it.
        target = _serialize(node.get("value"))
        return current != previous and current == target

    if raw is None:
        return False

    expected = node.get("value")

    if op == "in":
        try:
            return _serialize(raw) in [_serialize(v) for v in expected]
        except TypeError:
            return False

    if op == "contains":
        return str(expected).lower() in str(raw).lower()

    left, right = _coerce(raw, expected)
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
    except TypeError:
        # Mismatched types (e.g. datetime vs int) — treat as unsatisfied
        # rather than taking down the dispatcher tick.
        logger.debug(
            "condition type mismatch: %s %s %r vs %r",
            node.get("signal"), op, left, right,
        )
        return False
    return False


def summarize(node) -> str:
    """One-line human rendering, used for the panel's job subtitle."""
    if not isinstance(node, dict):
        return "invalid condition"
    if "all" in node:
        return " and ".join(summarize(c) for c in node["all"])
    if "any" in node:
        return " or ".join(summarize(c) for c in node["any"])
    if "not" in node:
        return f"not ({summarize(node['not'])})"
    name = node.get("signal", "?")
    op = node.get("op", "?")
    if op == "changed":
        return f"{name} changes"
    if op == "became":
        return f"{name} becomes {node.get('value')!r}"
    return f"{name} {op} {node.get('value')!r}"
