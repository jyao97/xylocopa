"""Slash command allowlist and lifecycle management.

Each command declares its own lifecycle:
- delivered_by  — which hook marks it as delivered
- completed_by  — which hook marks it as completed
- changes_session — whether it creates a new session ID
- args          — "required", "optional", or None
- description   — brief human-readable description

When Claude Code adds new slash commands, add an entry to COMMANDS below.
Ground-truth reference: https://code.claude.com/docs/en/commands
Empirically verified against Claude Code v2.1.140 (2026-05-13).
"""

import asyncio
import logging
from dataclasses import dataclass

from utils import utcnow as _utcnow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-command configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandConfig:
    delivered_by: str          # Hook that marks delivery (USP, PreCompact, SessionStart, etc.)
    completed_by: str          # Hook that marks completion (Stop, PostCompact, SessionStart, SessionEnd, CronDelete, etc.)
    changes_session: bool      # Whether this command creates a new session ID
    args: str | None           # "required", "optional", or None
    description: str           # Brief human-readable description


# COMMANDS only lists slash commands whose lifecycle differs from the
# default USP→Stop path.  Model-invoking commands (/init, /review, /commit,
# /security-review, /insights, /simplify, /debug, /batch, /claude-api, and
# anything new Claude Code ships) are handled by the default path and need
# NO entry here — Stop hook's mark_completed picks them up via the
# polarity-True default in completes_on_stop().
#
# To add a new command:
#   - Default lifecycle (model-invoking, completed on Stop)?  Do nothing.
#     Optionally add to skills.BUNDLED_SKILLS for the picker UI.
#   - UI-only (no hook fires, can't be tracked)?  Add to KNOWN_PROBLEMATIC.
#   - Special lifecycle (new hook, long-running, session-changing)?  Add an
#     entry here.
COMMANDS: dict[str, CommandConfig] = {
    "/clear": CommandConfig(
        delivered_by="SessionStart",
        completed_by="SessionStart",  # atomic — delivered and completed in same hook
        changes_session=True,
        args=None,
        description="Clear conversation and start new session",
    ),
    "/compact": CommandConfig(
        delivered_by="PreCompact",
        completed_by="PostCompact",
        changes_session=True,
        args="optional",
        description="Compact conversation context",
    ),
    "/loop": CommandConfig(
        delivered_by="USP",
        completed_by="SessionEnd|CronDelete",  # NOT Stop — Stop fires after each iteration
        changes_session=False,
        args="required",
        description="Run a repeating loop task",
    ),
    "/goal": CommandConfig(
        delivered_by="USP",
        completed_by="SessionEnd|CronDelete",  # NOT Stop — Stop fires after each round; Haiku judges goal
        changes_session=False,
        args="optional",  # `/goal` with no arg shows current/last goal; `/goal clear` cancels
        description="Run until a goal condition is met",
    ),
}


# ---------------------------------------------------------------------------
# Known-problematic slash commands
# ---------------------------------------------------------------------------
# Two flavours, both unsafe from /api/messages:
#   1. Side-channel built-ins that don't write JSONL or fire USP/Stop —
#      message stays PENDING forever (e.g. /help, /config, /usage).
#   2. Session-destroying / session-mutating commands that kill or rewrite
#      the agent before the lifecycle can complete (/exit, /quit, /stop,
#      /tui, /rewind, /teleport).  Typing these directly in the tmux
#      terminal is fine; sending them from the chat input is not.
#
# Asymmetric cost rule for new additions: if the docs description is not
# clearly model-invoking ("Claude analyzes...", "generates a...", etc.),
# default to listing here.  Mis-blocking a model-invoking command shows
# a visible rejection; mis-allowing a UI-only one strands the message in
# PENDING forever, which is worse.  Trim back if a real use-case appears.
#
# Verified against Claude Code v2.1.140 (2026-05-13) via
# tools/sync_slash_commands.py.
KNOWN_PROBLEMATIC: frozenset[str] = frozenset({
    # Originally-tracked
    "/agents", "/auth", "/btw", "/config", "/context", "/doctor",
    "/exit", "/quit",  # session-destroying — kills agent, SENT row stuck
    "/help", "/ide", "/install", "/login", "/logout", "/model",
    "/permissions", "/plugin", "/resume", "/status",
    # v2.1.140 catalog sweep — UI panels / config / wizards / handoffs
    "/add-dir", "/branch", "/chrome", "/color", "/copy", "/cost",
    "/desktop", "/diff", "/effort", "/export", "/extra-usage",
    "/fast", "/feedback", "/focus", "/heapdump", "/hooks",
    "/install-github-app", "/install-slack-app", "/keybindings",
    "/mcp", "/memory", "/mobile", "/passes", "/powerup",
    "/privacy-settings", "/radio", "/release-notes", "/reload-plugins",
    "/remote-control", "/remote-env", "/rewind", "/sandbox",
    "/scroll-speed", "/setup-bedrock", "/setup-vertex", "/skills",
    "/stats", "/statusline",  # /statusline: mixed mode (auto vs describe);
                              # block conservatively — can lift if a user
                              # reports needing it from web
    "/stickers", "/stop", "/tasks", "/teleport",
    "/terminal-setup", "/theme", "/tui", "/upgrade", "/usage",
    "/voice", "/web-setup",
})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse(content: str) -> tuple[str, str]:
    """Extract (command, args) from message content.

    >>> parse("/compact focus on API layer")
    ('/compact', 'focus on API layer')
    >>> parse("/help")
    ('/help', '')
    >>> parse("hello world")
    ('', 'hello world')
    """
    text = (content or "").strip()
    if not text.startswith("/"):
        return "", text
    parts = text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return cmd, args


def is_slash_command(content: str) -> bool:
    return (content or "").strip().startswith("/")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def classify(content: str) -> CommandConfig | None:
    """Return the command config if allowed, None if blocked."""
    cmd, _ = parse(content)
    return COMMANDS.get(cmd)


def is_allowed(content: str) -> bool:
    """Check if a slash command is allowed from web UI.

    Regular (non-slash) messages are always allowed. For slash commands we
    use a hybrid gate: anything in COMMANDS has a known lifecycle and is
    allowed; anything in KNOWN_PROBLEMATIC is rejected; everything else
    (custom skills, project commands, plugins) defaults to allowed and
    relies on USP+Stop being the right lifecycle.
    """
    if not is_slash_command(content):
        return True
    cmd, _ = parse(content)
    if cmd in COMMANDS:
        return True
    return cmd not in KNOWN_PROBLEMATIC


def rejection_message(content: str) -> str:
    """User-friendly error for blocked commands."""
    cmd, _ = parse(content)
    return f"{cmd} can only be used directly in the terminal"


# ---------------------------------------------------------------------------
# Lifecycle query helpers
# ---------------------------------------------------------------------------

def completes_on_stop(content: str) -> bool:
    """Return True if this command should be marked completed when Stop fires.

    Default is True: any slash command not listed in COMMANDS as a
    lifecycle exception is assumed to follow the standard USP→Stop path.
    This is the safe polarity — the alternative ("unknown ⇒ skip Stop")
    silently leaves model-invoking commands stuck EXECUTING when Claude
    Code ships a new slash command before xylocopa learns about it.

    Returns False only for the named exceptions in COMMANDS:
    - /loop, /goal     — completed by SessionEnd or CronDelete
    - /compact         — completed by PostCompact
    - /clear           — completed atomically by SessionStart
    """
    cmd, _ = parse(content)
    cfg = COMMANDS.get(cmd)
    if not cfg:
        return True
    return cfg.completed_by == "Stop"


# ---------------------------------------------------------------------------
# Lifecycle helpers — delivery + completion
# ---------------------------------------------------------------------------

def mark_delivered(agent_id: str, content: str) -> str | None:
    """Mark the matching undelivered slash command message as delivered.

    Called by hooks that confirm a slash command was received:
    - PreCompact  -> /compact
    - SessionStart(source=clear) -> /clear
    - Stop hook   -> fallback for USP-delivered commands

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

    cmd, _ = parse(content)
    if not cmd:
        return None

    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.source == "web",
                Message.delivered_at.is_(None),
                Message.content.startswith(cmd),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug("mark_delivered: no undelivered %s for %s", cmd, agent_id[:8])
            return None

        now = _utcnow()
        msg.delivered_at = now
        # Transition QUEUED → EXECUTING so the UI drops the "pending" muted
        # styling and the single-check delivered state becomes visible against
        # the saturated bubble.  completion happens later (PostCompact etc.).
        if msg.status == MessageStatus.SENT:
            msg.status = MessageStatus.EXECUTING
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_delivered, emit_message_update
        asyncio.ensure_future(emit_message_delivered(agent_id, msg.id))
        asyncio.ensure_future(emit_message_update(agent_id, msg.id))
        logger.info("slash_commands: %s delivered for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_completed(agent_id: str) -> str | None:
    """Mark the oldest EXECUTING slash command as completed + delivered.

    Called by the Stop hook as a catch-all for commands whose completed_by
    is "Stop".  Skips /loop and /goal (completed by SessionEnd/CronDelete)
    and any command whose completed_by is not "Stop".

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.status == MessageStatus.EXECUTING,
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg or not is_slash_command(msg.content or ""):
            return None

        # Skip commands that are not completed by Stop (e.g. /loop, /goal, /compact, /clear)
        if not completes_on_stop(msg.content):
            cmd, _ = parse(msg.content)
            logger.info(
                "slash_commands: skipping mark_completed for %s — "
                "not completed by Stop (agent=%s, msg=%s)",
                cmd, agent_id[:8], msg.id,
            )
            return None

        now = _utcnow()
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        # Also mark delivered if USP didn't (safety net).
        if not msg.delivered_at:
            msg.delivered_at = now
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_executed, emit_message_update
        asyncio.ensure_future(emit_message_update(agent_id, msg.id))
        asyncio.ensure_future(emit_message_executed(agent_id, msg.id))
        if msg.delivered_at == now:
            from websocket import emit_message_delivered
            asyncio.ensure_future(emit_message_delivered(agent_id, msg.id))

        cmd, _ = parse(msg.content)
        logger.info("slash_commands: %s completed for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_delivered_and_completed(agent_id: str, content: str) -> str | None:
    """Atomically mark a slash command as both delivered and completed.

    Used for commands where delivery and completion happen in the same hook
    (e.g. /clear via SessionStart).

    Returns the message ID if found, None otherwise.
    """
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

    cmd, _ = parse(content)
    if not cmd:
        return None

    db = SessionLocal()
    try:
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.source == "web",
                Message.status == MessageStatus.EXECUTING,
                Message.content.startswith(cmd),
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug("mark_delivered_and_completed: no EXECUTING %s for %s", cmd, agent_id[:8])
            return None

        now = _utcnow()
        msg.delivered_at = now
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        # Session-changing slash commands never appear in post-change JSONL.
        # Set synthetic jsonl_uuid so promotion queries skip them.
        cmd_cfg = COMMANDS.get(cmd)
        if cmd_cfg and cmd_cfg.changes_session and not msg.jsonl_uuid:
            msg.jsonl_uuid = f"slash-{msg.id[:8]}"
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import (
            emit_message_delivered,
            emit_message_executed,
            emit_message_update,
        )
        asyncio.ensure_future(emit_message_delivered(agent_id, msg.id))
        asyncio.ensure_future(emit_message_executed(agent_id, msg.id))
        asyncio.ensure_future(emit_message_update(agent_id, msg.id))

        logger.info("slash_commands: %s delivered+completed for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()


def mark_long_running_completed(agent_id: str) -> str | None:
    """Mark an EXECUTING long-running slash command (/loop, /goal) as completed.

    Called from SessionEnd hook or when CronDelete is detected in JSONL.
    This is the only way long-running commands get completed — the Stop hook
    explicitly skips them because Stop fires after each iteration/round,
    but SessionEnd is terminal.

    The prefix set is derived from COMMANDS: any command whose completed_by
    contains "SessionEnd" is treated as long-running. To add a new one,
    just add it to COMMANDS — no change needed here.

    Returns the message ID if found, None otherwise.
    """
    from sqlalchemy import or_
    from database import SessionLocal
    from models import Message, MessageRole, MessageStatus

    long_running_prefixes = [
        cmd for cmd, cfg in COMMANDS.items()
        if "SessionEnd" in cfg.completed_by
    ]
    if not long_running_prefixes:
        return None

    db = SessionLocal()
    try:
        prefix_filter = or_(
            *[Message.content.startswith(p) for p in long_running_prefixes]
        )
        msg = (
            db.query(Message)
            .filter(
                Message.agent_id == agent_id,
                Message.role == MessageRole.USER,
                Message.status == MessageStatus.EXECUTING,
                prefix_filter,
            )
            .order_by(Message.created_at.desc())
            .first()
        )
        if not msg:
            logger.debug(
                "mark_long_running_completed: no EXECUTING long-running cmd for %s",
                agent_id[:8],
            )
            return None

        now = _utcnow()
        msg.status = MessageStatus.COMPLETED
        msg.completed_at = now
        if not msg.delivered_at:
            msg.delivered_at = now
        db.commit()

        from display_writer import update_last
        update_last(agent_id, msg.id)

        from websocket import emit_message_executed, emit_message_update
        asyncio.ensure_future(emit_message_update(agent_id, msg.id))
        asyncio.ensure_future(emit_message_executed(agent_id, msg.id))
        if msg.delivered_at == now:
            from websocket import emit_message_delivered
            asyncio.ensure_future(emit_message_delivered(agent_id, msg.id))

        cmd, _ = parse(msg.content)
        logger.info("slash_commands: %s completed for %s (msg=%s)", cmd, agent_id[:8], msg.id)
        return msg.id
    finally:
        db.close()
