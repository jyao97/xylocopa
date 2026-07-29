"""Attention assistant — pluggable (trigger × action) job layer.

The attention FAB's assistant does not own any scheduling machinery of its
own. It composes primitives that already exist in the orchestrator:

  * the 2s dispatcher tick (`agent_dispatcher._tick`) drives evaluation
  * `notify.py` delivers push
  * `display_writer.pre_sent_create` injects chat messages
  * `task_service` creates/dispatches tasks
  * `claude -p --model claude-sonnet-5` is the LLM (no new dependency)

Everything user-visible is one `AttentionJob` row = one trigger + one
action, both resolved through the registries in `registry.py`.

Import order matters: `triggers`, `actions` and `signals` register
themselves as an import side effect, so importing this package is what
populates the registries. `scheduler` and `compiler` are NOT imported here
— they pull heavier dependencies (task_service, subprocess) and are
imported lazily at their call sites, matching the orchestrator's existing
late-import convention.
"""

from attention import actions, signals, triggers  # noqa: F401  (registration side effect)
from attention.registry import (
    ACTIONS,
    SIGNALS,
    TRIGGERS,
    Action,
    Trigger,
    describe_registries,
    register_action,
    register_signal,
    register_trigger,
)

__all__ = [
    "ACTIONS",
    "SIGNALS",
    "TRIGGERS",
    "Action",
    "Trigger",
    "describe_registries",
    "register_action",
    "register_signal",
    "register_trigger",
]
