"""Database models for Xylocopa."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from utils import utcnow as _utcnow


class Base(DeclarativeBase):
    pass


class AgentMode(str, enum.Enum):
    INTERVIEW = "INTERVIEW"    # Chat only — no auto-execution
    AUTO = "AUTO"              # Execute immediately


class TaskStatus(str, enum.Enum):
    INBOX = "INBOX"
    PLANNING = "PLANNING"
    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    REVIEW = "REVIEW"
    MERGING = "MERGING"
    CONFLICT = "CONFLICT"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"  # displayed as "Dropped" in the UI
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class AgentStatus(str, enum.Enum):
    STARTING = "STARTING"
    IDLE = "IDLE"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


class MessageRole(str, enum.Enum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class MessageStatus(str, enum.Enum):
    SENT = "SENT"           # sent via tmux send-keys, awaiting JSONL delivery confirmation
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"  # soft-cancelled by user before dispatch; bubble stays visible (greyed)


def _new_uuid():
    return uuid.uuid4().hex[:12]


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    # New first-class fields
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_name: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0=normal, 1=high
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.INBOX, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    worktree_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    retry_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: previous failure info
    agent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    skip_permissions: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    use_worktree: Mapped[bool] = mapped_column(Boolean, default=True)
    use_tmux: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notify_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deferred_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # V1 legacy columns — kept because SQLite NOT NULL without DEFAULT.
    # Not used by any code; safe to drop once a migration rewrites the table.
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    project: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    mode: Mapped[AgentMode] = mapped_column(Enum(AgentMode), nullable=False, default=AgentMode.AUTO)
    retries: Mapped[int] = mapped_column(Integer, default=0)


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[AgentMode] = mapped_column(
        Enum(AgentMode), nullable=False, default=AgentMode.AUTO
    )
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus), nullable=False, default=AgentStatus.STARTING, index=True
    )
    branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    worktree: Mapped[str | None] = mapped_column(String(200), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    cli_sync: Mapped[bool] = mapped_column(Boolean, default=True)
    tmux_pane: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    effort: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    skip_permissions: Mapped[bool] = mapped_column(Boolean, default=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    deferred_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("tasks.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    is_subagent: Mapped[bool] = mapped_column(Boolean, default=False)
    claude_agent_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    generating_msg_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    has_pending_suggestions: Mapped[bool] = mapped_column(Boolean, default=False)
    insight_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # JSONL sync pointer — persisted so restart resumes where the last
    # sync stopped, instead of re-traversing the entire file (which
    # would re-fire all historical signals: stop_hook, push notify,
    # interactive-card dismissal, etc).
    sync_last_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_last_turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sync_last_content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # Persisted context-usage snapshot. Avoids re-scanning the JSONL +
    # MCP/memory/agent files on every chat-page open. Updated each time
    # `emit_context_usage` runs (post-turn, post-stop, sync_engine wake).
    # Headline columns are queryable for sort/filter; the JSON column
    # holds the full breakdown for the popover.
    context_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    context_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_generating(self) -> bool:
        return self.generating_msg_id is not None

    # Parent → child subagent relationship (self-referential)
    subagents: Mapped[list["Agent"]] = relationship(
        "Agent",
        back_populates="parent_agent",
        foreign_keys="[Agent.parent_id]",
        viewonly=True,
        lazy="select",
    )
    parent_agent: Mapped["Agent | None"] = relationship(
        "Agent",
        back_populates="subagents",
        foreign_keys="[Agent.parent_id]",
        remote_side="[Agent.id]",
        viewonly=True,
        lazy="select",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), nullable=False, default=MessageStatus.COMPLETED
    )
    stream_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "web" | "cli" | None
    jsonl_uuid: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)  # JSONL entry uuid for dedup
    meta_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)  # JSON string for interactive data
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatch_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # dispatch order per agent
    tool_use_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "text" | "tool_use" | None (legacy)
    display_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # display file sequence number


class Project(Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    git_remote: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=8)
    default_model: Mapped[str] = mapped_column(
        String(100), default="claude-opus-4-7"
    )
    archived: Mapped[bool] = mapped_column(default=False)
    auto_progress_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_insights: Mapped[bool] = mapped_column(Boolean, default=False)
    emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resume_emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resume_hint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resume_hint_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StarredSession(Base):
    __tablename__ = "starred_sessions"

    session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class BookmarkedMessage(Base):
    __tablename__ = "bookmarked_messages"
    __table_args__ = (
        Index("ix_bookmarks_project_created", "project", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary_emoji: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="message")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    p256dh_key: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProgressInsight(Base):
    __tablename__ = "progress_insights"
    __table_args__ = (
        Index("ix_progress_project_date", "project", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.name", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Link insight to the agent that produced it (NULL for cross-agent daily summaries)
    agent_id: Mapped[str | None] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AgentInsightSuggestion(Base):
    __tablename__ = "agent_insight_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    edited_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending/accepted/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


def _new_probe_token() -> str:
    import secrets
    return secrets.token_hex(16)  # 32 hex chars


class Probe(Base):
    __tablename__ = "probes"
    __table_args__ = (
        Index("ix_probes_agent_active", "agent_id", "fired_at", "expires_at"),
    )

    # Envelope markers — must be backend-controlled and never appear in
    # user-supplied `message`, otherwise an attacker (or a chat self-attack)
    # could synthesize a fake nested envelope and confuse the wake target
    # about where the trigger ends.
    ENVELOPE_PREFIX: str = "\U0001F514 Probe "
    ENVELOPE_FOOTER: str = "— end of probe trigger —"
    MAX_MESSAGE_LEN: int = 4000

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_uuid)
    token: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True, default=_new_probe_token,
    )
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    notify_user: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @classmethod
    def validate_message(cls, text: str | None) -> str | None:
        """Reject probe messages that could break the envelope contract.

        Returns an error reason (str) if invalid, or None if valid.
        Used at BOTH probe_create (input gate) AND trigger time (defense
        in depth — guards against direct DB tampering or future code paths
        that mutate `message` post-create).
        """
        if not text or not text.strip():
            return "message is empty"
        if len(text) > cls.MAX_MESSAGE_LEN:
            return (
                f"message exceeds {cls.MAX_MESSAGE_LEN} chars (got {len(text)})"
            )
        if cls.ENVELOPE_PREFIX in text:
            return (
                f"message must not contain the envelope prefix "
                f"{cls.ENVELOPE_PREFIX!r} (would synthesize a nested envelope)"
            )
        if cls.ENVELOPE_FOOTER in text:
            return (
                f"message must not contain the envelope footer "
                f"{cls.ENVELOPE_FOOTER!r} (would forge envelope termination)"
            )
        # Reject control characters except newline and tab — protects tmux
        # send-keys + Claude's prompt parsing from terminal escape injection.
        for i, ch in enumerate(text):
            cp = ord(ch)
            if cp < 0x20 and ch not in ("\n", "\t"):
                return (
                    f"message contains control char U+{cp:04X} at offset {i}"
                )
            if cp == 0x7F:  # DEL
                return f"message contains DEL char at offset {i}"
        return None


class SessionViewEvent(Base):
    """A single continuous interval during which a user was actively
    viewing one agent session. Written by the view-tracking tick loop.
    """
    __tablename__ = "session_view_events"
    __table_args__ = (
        Index("ix_view_events_started", "started_at"),
        Index("ix_view_events_project_started", "project", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    project: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class CCSession(Base):
    """A single Claude Code conversation session — either top-level (the
    xylo agent's own CLI session, possibly one of many across /compact
    /clear rotations) or a sub-session spawned by Task tool invocation
    inside another CC session.

    Top-level: parent_session_id IS NULL, is_subagent_session = False
    Sub-session: parent_session_id points to the calling CC session,
                 is_subagent_session = True

    agent_id always points to the OWNING xylo agent. For sub-sessions
    that's the same xylo agent that issued the Task tool call (xylo
    "subagent" rows themselves have session_id=None — they're metadata,
    not session owners).

    Token totals are populated either:
    - On rotation/end (writer in agent_dispatcher / sync_engine)
    - By reconcile sweep (cc_session_discovery scanning JSONL files)
    Updated rows on subsequent writes if usage grew.
    """
    __tablename__ = "cc_sessions"
    __table_args__ = (
        Index("ix_cc_sessions_agent_started", "agent_id", "started_at"),
        Index("ix_cc_sessions_parent", "parent_session_id"),
    )

    session_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Self-referential link to the CC session that spawned this one via
    # Task tool. NULL for top-level sessions.
    parent_session_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("cc_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )
    # The JSONL `parentUuid` field — points back to a specific entry in
    # the parent's JSONL (the tool_use that spawned us). Lets reconcile
    # find unmatched sub-sessions.
    parent_jsonl_uuid: Mapped[str | None] = mapped_column(String(50), nullable=True)

    project_path: Mapped[str] = mapped_column(String(500), nullable=False)
    worktree: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_subagent_session: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # active | rotation | compact | clear | subagent_done | reconciled | stopped
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)

    model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Sum of 5m + 1h. Maintained as a denormalized rollup for back-compat
    # with code paths that read this column directly.
    total_cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Anthropic charges 5m and 1h ephemeral cache writes at different rates
    # (1.25× input vs 2× input). JSONL `cache_creation` block exposes the
    # split. Legacy rows have 0 in both columns; cost calc falls back to
    # total_cache_creation_tokens at the 5m rate.
    total_cache_creation_5m_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cache_creation_1h_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
