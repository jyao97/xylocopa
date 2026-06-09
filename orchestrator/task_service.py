"""Shared task-domain logic for the REST routers and the MCP server.

The two surfaces deliberately differ in UX — HTTPException vs friendly
strings, WebSocket emission, title auto-generation, cwd inference — but the
core mapping from a validated TaskCreate payload to a Task row must stay
identical on both sides. Keep it here so a new Task field needs one edit,
not two (routers/tasks.py + mcp_server.py).

Kept dependency-light on purpose: mcp_server.py runs as a standalone
process and must not pull in the FastAPI app, websocket, or dispatcher
modules.
"""

from models import Task, TaskStatus
from schemas import TaskCreate


def build_task(
    payload: TaskCreate, *, status: TaskStatus, title: str | None = None,
) -> Task:
    """Construct a Task ORM row from a validated TaskCreate payload.

    *title* overrides ``payload.title`` (the REST layer auto-generates a
    title when the payload's is blank). The row is not added to a session.

    ``deferred_to`` is intentionally not mapped — neither surface sets it
    at creation time today.
    """
    return Task(
        title=title if title is not None else payload.title,
        description=payload.description,
        project_name=payload.project_name,
        model=payload.model,
        effort=payload.effort,
        priority=payload.priority,
        skip_permissions=payload.skip_permissions,
        sync_mode=payload.sync_mode,
        use_worktree=payload.use_worktree,
        use_tmux=payload.use_tmux,
        notify_at=payload.notify_at,
        status=status,
    )
