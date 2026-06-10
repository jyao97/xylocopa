# Agent-callable MCP tools

> What a xylocopa-managed agent is allowed to do to the orchestrator from
> inside its own session, via the built-in MCP server
> ([`orchestrator/mcp_server.py`](../orchestrator/mcp_server.py)).
>
> The MCP server is auto-registered into every project's `.mcp.json` on
> orchestrator startup and on each agent launch, so any Claude Code agent
> running under xylocopa can call these tools directly.

---

## Safety model — what the agent **cannot** do

This surface is deliberately **non-destructive only**. We use a verb-axis
allow/deny list rather than per-tool gating, so the boundary is auditable
at the name level alone.

| Allowed verbs | Forbidden verbs |
|---|---|
| `list`, `get`, `read`, `tail`, `count`, `health`, `create`, `update`, `dispatch`, `scaffold`, `regenerate` | `delete`, `archive`, `kill`, `force`, `reset`, `drop`, `wipe`, `clean`, `cancel`, `stop`, `purge`, `restore`, `truncate`, `restart` |

A tool whose verb falls in the forbidden column **does not exist** in the
MCP surface — there is no `confirm=True` override. If an agent needs a
destructive action (delete a project, kill another agent, restart the
server, purge backups), it must surface that intent to the human user via
the chat, who performs it through the web UI.

Other guarantees:

- **Idempotent writes.** Calling `project_create` twice with the same name
  is a no-op (or re-activates an archived project). `project_scaffold` and
  `project_regenerate_claude_md` are safe to repeat. `task_dispatch` on an
  already-PENDING task is a no-op.
- **Rollback on partial failure.** `project_create` rolls back the DB row
  if the registry write fails.
- **Read-only queries use a read-only sqlite connection** (`mode=ro`); they
  cannot mutate anything even by accident.
- **Write tools use a separate lazy-loaded SQLAlchemy session** with the
  same WAL/foreign-key/busy-timeout pragmas as the main app, so MCP-driven
  writes are consistent with web-UI writes.

---

## Tools by domain (23 canonical + 6 back-compat aliases)

### project

| Tool | Verb | Effect |
|---|---|---|
| `project_list(include_archived=False)` | list | List active (or all) projects. |
| `project_get(name)` | get | Full project info: path, git remote, agent/task/session counts. |
| `project_create(name, path="", git_url="", description="")` | create | Register a new project. Idempotent on name; re-activates if archived. Does **not** clone — caller must clone first. Rolls back DB insert if registry write fails. |
| `project_scaffold(name)` | scaffold | Create CLAUDE.md / PROGRESS.md if missing. No-op if already scaffolded. |
| `project_regenerate_claude_md(name)` | regenerate | Force-rewrite CLAUDE.md via the deterministic scaffolder, preserving the project-specific rules block. Synchronous — distinct from the web UI's AI-powered async refresh. |

### task

| Tool | Verb | Effect |
|---|---|---|
| `task_list(project="", status="", limit=30)` | list | List tasks, optionally filtered. Capped at 100. |
| `task_get(task_id)` | get | Full task detail: title, description, status, attempt, timestamps, agent_summary, error_message. |
| `task_counts(project="")` | count | Per-status counts. Cheaper than `task_list` for backlog summaries. |
| `task_create(title, project="", description="", model="", effort="", priority=0)` | create | Drop a task into INBOX. Project inferred from cwd if empty. Defaults inherit from project settings. |
| `task_update(task_id, title="", description="", project="", model="", effort="", priority=None)` | update | Update fields. Status is intentionally **not** mutable here — use `task_dispatch`. |
| `task_dispatch(task_id)` | dispatch | INBOX/FAILED/TIMEOUT → PENDING. Background poller picks up PENDING within seconds. Also serves as "retry" for FAILED/TIMEOUT (state machine permits the transition). |

### session

| Tool | Verb | Effect |
|---|---|---|
| `session_list(project="")` | list | Recent sessions across or within a project. |
| `session_read(session_id, max_turns=50)` | read | Full conversation transcript by session ID, agent ID, or prefix. Reads the curated display file when present (~54× smaller than raw JSONL); falls back to raw JSONL otherwise. |
| `session_tail(session_id, max_turns=10)` | tail | Same backend as `session_read`, optimized for "what just happened." Smaller default cap. |

### agent

| Tool | Verb | Effect |
|---|---|---|
| `agent_list(project="", status="", limit=30)` | list | List agents, ordered by last activity. Filter by project + status. |
| `agent_get(agent_id)` | get | Full agent record: status, mode, session_id, model/effort, branch/worktree, tmux pane, task linkage, parent/subagent, unread, last-message preview. |

### probe

Event-driven chat wake-up: an agent registers a one-shot webhook and hands
the URL to whatever monitors an external condition (CI, cron, a long build).
POSTing the URL injects an envelope-wrapped message into the target chat
and burns the token.

| Tool | Verb | Effect |
|---|---|---|
| `probe_create(agent_id, message, expires_in_hours=24)` | create | Register a one-shot webhook; returns the trigger URL. Max 10 active per agent; max expiry 168 h. |
| `probe_list(agent_id="", include_fired=False)` | list | Active probes, optionally including fired ones. |
| `probe_get(probe_id)` | get | Full probe record. |
| `probe_update(probe_id, expires_at="")` | update | Extend or shrink expiry. Setting a past time is the supported cancel path (`delete` is forbidden by the verb whitelist). |

### webapp

Interactive deliverables presented as tappable cards in chat. Three target
kinds, auto-detected: `static` (project-relative .html, served in a
credential-less sandboxed iframe), `port` (localhost service reverse-proxied,
e.g. TensorBoard — cookie-session apps must use `url` instead), `url`
(external dashboard, opens in a new tab).

| Tool | Verb | Effect |
|---|---|---|
| `webapp_present(target, title="", description="", kind="", agent_id="")` | create | Post a web-app card to the calling agent's chat and upsert the project registry. For `port`, returns the stable proxy prefix. |
| `webapp_list(project="")` | list | Registered web apps for a project (inferred from the calling agent if empty). Check before building a new viewer. |

### system

| Tool | Verb | Effect |
|---|---|---|
| `system_health()` | health | DB liveness + registry parseability + project/task/agent counts. Use before queuing work. |

### Back-compat aliases (kept; prefer the domain-prefixed names)

`list_sessions`, `read_session`, `create_task`, `update_task`,
`dispatch_task`, `list_tasks` — thin wrappers, byte-identical output to
their domain-prefixed counterparts.

---

## What is intentionally **not** exposed

The web UI exposes these. The MCP surface does not — by design.

- **Project lifecycle**: archive/delete/permanent-delete, restore from trash.
- **Task lifecycle**: cancel, mark-complete, regenerate-summary,
  batch-process, reorder.
- **Agent lifecycle**: stop, kill, permanent-delete, send-message,
  schedule-message, edit-message, cancel-message, delete-message,
  apply/discard suggestions.
- **System ops**: restart, orphan clean, log truncate, backup
  trigger/import/restore/purge/delete, telemetry toggle.
- **Git mutations**: push, merge, checkout (the agent has shell access; use
  `git` CLI directly).
- **Hooks, push subscriptions, file uploads, auth.**
- **Async AI workflows** that produce review-pending changes
  (refresh-claudemd, summarize-progress, rebuild-insights, apply-progress,
  apply-claudemd) — agent should use the synchronous deterministic
  scaffolder via `project_regenerate_claude_md` instead.

If a use case really needs one of these from inside an agent, it's a
deliberate design conversation, not a tool addition.

---

## Operational notes

- **The agent calls these tools by name** (e.g. `task_create`,
  `session_read`) — there is no "MCP CLI" wrapper.
- **Already-running agents do not pick up newly-added tools.** MCP servers
  are loaded at session start. Restart the agent (or wait for the next
  session) to see schema changes.
- **The tool's working directory is the agent's cwd**, which `task_create`
  uses to infer the project when `project=""`. Worktree subdirectories are
  matched via `path.startswith(project_path + "/")`.
- **Media files**: when generating images/videos/plots, save them inside
  the project directory so the web UI can preview them. Files in `/tmp/`
  cannot be displayed.

---

## Tests

Standalone test script at
[`orchestrator/test_mcp_tools.py`](../orchestrator/test_mcp_tools.py)
sets up a temp `XYLOCOPA_ROOT`, exercises the project/task/session/agent/system
tools' happy + error paths, verifies alias byte-equality, and tears down. Run:

```bash
cd orchestrator && ../.venv/bin/python test_mcp_tools.py
```
