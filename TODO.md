# TODO

Priority-ordered backlog. Move items between sections as priorities shift.
PROGRESS.md tracks completed work; this file tracks what's next.

## High

_(empty)_

## Medium

### Detect CC TUI modal dialogs before dispatching tmux messages
`dispatch_pending_message` (`agent_dispatcher.py:2647`) only checks
`agent.status == EXECUTING` before pasting + sending Enter. If Claude Code
is showing a modal dialog (Resume from summary, permission prompt,
ExitPlanMode, AskUserQuestion) the agent's xylo-side status is still IDLE
and our Enter accepts the menu's default option instead of submitting the
queued message — which is silently lost. Concrete prior incident:
2026-05-03 22:44 agent `d6129b61`, msg `03609fef` ("又发生了" + image)
was eaten by the Resume-from-summary menu, default "1. Resume from summary"
fired /compact, JSONL got rewritten, message vanished.

**Why it can't be detected via files / hooks.** TUI dialog state is pure
React/Ink render state — never written to JSONL, no hook fires on
"dialog opened/closed". Tmux's own pane vars are useless too:
`alternate_on=0` (Ink renders inline), `cursor_x/y` lands on the input
box even with a menu floating above, `pane_in_mode` is tmux's own
copy-mode flag. **Only signal available is `tmux capture-pane` text
matching.**

**Proposed design.**
- Add `pane_is_in_modal(pane_id) -> bool` helper that captures the last
  ~10 lines and matches known modal signatures:
  - Generic: `"Enter to confirm · Esc to cancel"` (all select-menus)
  - Numbered options: regex `❯ \d+\. ` + at least one more numbered line
  - Permission dialog: regex `^\s*\d+\.\s+(Yes|No|Allow|Deny)` multiline
  - Cost-threshold dialog, ExitPlanMode, AskUserQuestion as we discover
    their signatures
- Call it in `dispatch_pending_message` and `_dispatch_pending_messages`
  (scheduled path) right before `send_tmux_message`. On hit: leave message
  PENDING, log a structured warning, let the next stop-hook cycle retry.
- Add small unit-test fixture set with known pane_tail strings from
  GHOST_PROBE logs (we already have one for Resume-from-summary).

**Open questions.**
- Retry cadence: stop hook fires when CC actually idles after the user
  closes the dialog manually. But if dialog stays up indefinitely (user
  walked away), how does the queued message ever get sent? Possibly
  surface a UI affordance: "agent waiting on dialog — view pane / send
  Esc to dismiss".
- ANSI handling: capture-pane returns rendered text but Ink can use
  invisible chars / cursor positioning. Need to test signature stability
  with `-e` (escape sequences) vs default.

**Already done (2026-05-04, related):** suppressed the most common
trigger by setting `CLAUDE_CODE_RESUME_TOKEN_THRESHOLD=999999999` /
`CLAUDE_CODE_RESUME_THRESHOLD_MINUTES=999999` on every CC launch
(`route_helpers.py` `create_tmux_claude_session`). This kills the
Resume-from-summary menu specifically. Permission / ExitPlanMode /
AskUserQuestion dialogs still leave the same message-loss surface
exposed — which is why this Medium-priority item exists.

## Low

### Project state reconciliation + orphan cleanup
Unify the two divergent project-listing endpoints (`/api/projects` reads DB,
`/api/projects/folders` scans filesystem) into a single reconcile pipeline,
and add a manually-triggered orphan cleanup script.

**Status.** The immediate user-visible symptom, xylocopa repo missing
from the Projects grid, was hand-patched on 2026-04-18 by deleting the
stale `agenthive` DB row and creating
`~/xylocopa-projects/xylocopa → /home/jyao073/xylocopa` symlink. This
TODO is structural prevention, not a pending bug.

**Background.** The grid page (`/api/projects/folders`,
`orchestrator/routers/projects.py:815`) lists filesystem dirs in
`PROJECTS_DIR` joined with DB stats. The picker (`/api/projects`, line 723)
reads DB rows filtered by `archived=False`. Projects whose `Project.path`
falls outside `PROJECTS_DIR` (e.g. xylocopa self-hosting) appear in the
picker but not the grid. Manual fs deletes leave DB orphans; manual fs
adds leave unregistered dirs. `registry.yaml` is a third source of truth
seeded into DB on startup (`main.py:79`), and `_remove_from_registry()`
writes back, so all three must stay in sync.

**Proposed design.**
- Single reconcile pass on app startup + manual refresh button:
  scan `PROJECTS_DIR` and DB together, classify into Active /
  Inactive (archived) / Unregistered (fs-only) / External (DB row, path
  outside PROJECTS_DIR but exists) / Orphan (DB row, path missing).
- One-way update direction: FS → DB → `registry.yaml`.
- Picker shows only Active. Grid shows Active + Inactive (Inactive
  cannot receive new tasks). Orphans live in a separate monitor view
  with cleanup actions.

**Orphan cleanup script (`orchestrator/reconcile.py`, dry-run + apply):**
- Project layer: missing fs path, dead symlinks, unregistered dirs,
  registry.yaml ↔ DB drift.
- FK orphans: `agents.project` / `tasks.project_name` → missing project;
  `messages.agent_id` → missing agent; `agents.task_id` → missing task;
  `starred_sessions.project` → missing.
- Session layer: `agents.session_id` → missing JSONL;
  `~/.claude/projects/<path>/` for deleted projects;
  starred sessions for missing JSONL.
- Residue: stale `xy-*` tmux sessions, `.trash/` entries older than
  N days (report only, no auto-purge), session_cache stale entries.

**Triggers for promoting to Medium/High.**
- A second fs/DB-divergence bug surfaces.
- Project count grows past ~100 (perf concern).
- Self-hosting / external-path projects become a regular pattern.

**Quick wins available without the full refactor.**
- Add union in folders endpoint: include DB rows whose `path` is not in
  PROJECTS_DIR, so xylocopa-style external projects show in the grid
  automatically without requiring a manual symlink.
