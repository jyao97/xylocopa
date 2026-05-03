# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Each entry mirrors the corresponding [GitHub release](https://github.com/jyao97/xylocopa/releases) — see those pages for the full prose write-up. This file keeps the same content in Keep-a-Changelog form so it's grep-able from a clone.

## [Unreleased]

## [0.10.2] - 2026-05-03

### Fixed

- **Android PWA app icon now displays correctly.** Manifest icons previously pointed to local URLs (`/icon-192.png`), which Google's WebAPK minting server can't reach when the host is a private Tailscale or LAN IP — the launcher then fell back to "first character of host" (e.g. "1" for `100.x.x.x`). Manifest icons now point to a public jsDelivr CDN mirror of the GitHub release tag, which the minting server can fetch over the public internet. The version is read from `frontend/package.json` at build time so each release pins to its own immutable icon set.
- **iOS Safari "Add to Home Screen" without mobileconfig** also affected by the same private-cert fetch issue. `apple-touch-icon` link in `index.html` now uses the same jsDelivr CDN URL (with `__APP_VERSION__` injected at build time via a small `transformIndexHtml` plugin), so iOS users who skip the Web Clip flow get the bee icon instead of a generic placeholder.

### Added

- **Android-aware cert-guide page.** `/cert-guide` detects the platform from UA and shows the appropriate install steps. Android Step 1 covers the actual Android cert-install path (download `.crt`, set screen lock first, Settings → Security → Encryption & credentials → Install a certificate, with a note that path varies by ROM and Settings search works). Android Step 2 covers PWA install via Chrome menu, and uses `beforeinstallprompt` to surface a one-tap install button when Chrome fires it. A manual iOS/Android toggle at the top of the page lets users override detection.
- **`tools/bump-version.sh`** — bumps version across both `package.json` files, inserts a CHANGELOG stub, commits, and creates an annotated tag. Prints next-step reminders, including that the tag must be pushed before the build is shipped (jsDelivr won't serve icons from an unpushed tag).

## [0.10.1] - 2026-05-03

### Added

- **E-ink mode: two-finger horizontal swipe to page-scroll the chat.** Left swipe = page down, right swipe = page up. Saves the user from dragging the scrollbar on slow-refresh e-paper panels.

### Fixed

- **E-ink swipe gesture not firing.** `touchend` fires once per finger lift, and the handler was nulling `touchState` on every intermediate fire — so a 2-finger swipe only worked if both fingers happened to lift in the same frame. Now waits for `e.touches.length === 0` before processing, and preserves the original start point if a second finger lands mid-gesture.

### Changed

- **Skeleton cleanup, round 2.** `GitPage` and `ProjectDetailPage` were still flashing ghost shapes in the body during data loads (commit-row placeholders, branch pills, agent-row cards). Body region now stays empty during load, matching the prior simplification of `ChatSkeleton` / `ProjectDetailSkeleton` / `TaskDetailSkeleton` / `RouteFallback`. Header chrome (Git tab pills) preserved.

## [0.10.0] - 2026-05-02

### Added

- **Context usage pill** on the chat header. Live per-agent context-window meter with a tap-to-expand breakdown (system / tools / MCP / messages / cache split, free vs. used). Counts come straight from the Claude Code session JSONL, not estimated. Inline suggestions appear when usage gets high. Pushed over WebSocket and persisted on the agent row so the value paints immediately on chat open. Resolves [#3](https://github.com/jyao97/xylocopa/issues/3) point 1.
- **System / meta-agents on `.xylo-internal`.** System-level sessions (Task-AI, merge agents, insights generation, etc.) are now hosted on a synthetic `.xylo-internal` project placeholder — they no longer need to be bound to a real project to run. Templates (`CLAUDE.md`, `PROGRESS.md`, agent-hooks JSON) moved to `.xylo-internal/templates/` and loaders fail-fast if `PROJECTS_DIR` is unset. Resolves [#3](https://github.com/jyao97/xylocopa/issues/3) point 4.
- **E-ink display mode.** Manual toggle in **Settings → Display**, plus user-agent auto-detect for BOOX / Onyx / Kindle / Bigme / Hisense / Meebook / iReader. Flattens glass surfaces to a single page color, collapses colored badges/tags to grayscale, switches saturated bubbles to outlined style, drops gradients, and bumps secondary-text weight for readable contrast on e-paper. Includes an `?eink-diag=1` overlay for on-device detection debugging and an auto-fullscreen step on first toggle.
- **Subagent visibility.** Sub-sessions spawned via Claude Code's `Agent` tool are now discovered under `<session>/subagents/` and surfaced as a Task → Xylo session → CC session → Sub-session hierarchy in the UI; receiver-side filter on the Agents page hides synthetic subagent rows from the main list while preserving the parent linkage in chat.
- **Lifetime cost tracking** for xylo-agents. Per-agent cumulative spend, deduped by message id (so resumes don't double-count), with corrected Opus prices and 5m / 1h cache-read split.
- **Task graph visualization** on the project dashboard, with task chips on the graph tab.
- **Bookmarks (continued from 0.9.x).** Project rename data-migration chain now includes `bookmarked_messages` (FK deferred for safe rename).
- **Frame-by-frame DOM mutation logger** (`frameLogger.js`) for diagnosing UI flicker.

### Performance

- **Chat-page open is materially faster.** Parallel fetches in place of serial waterfalls; hover prefetch on agent rows so the next chat is warm by the time you tap; idle-prefetch of heavy lazy chunks (chat / project / task / new-task) so route transitions don't pay the chunk-download cost; `briefCache` so detail pages paint the real header from cached project/task data instead of showing a centered spinner.
- **Route-aware skeletons** replace top-level "Loading…" spinners on main tabs. Skeletons leave the middle blank while keeping header + composer chrome, so the chat shell paints instantly.
- **Keep-mounted main tabs.** Inbox / Projects / Agents / Git / Monitor stay mounted across navigation (visibility toggle instead of `display:none` / unmount), eliminating the loading-flash on tab switches and re-fetch-on-return.
- **ESC endpoint.** Latency cut from ~1.4s to ~570ms; double-tap Esc + safety Esc replaces `C-l`; 100ms buffer before status flips to IDLE and dispatch fires.
- **Context-usage value paints immediately on chat open** — persisted on the agent row + early `setLoading(false)` so the pill doesn't hold up the rest of the header.
- **Faster agent launch + thread-safety hardening** (carried in from 0.9.6 / 0.9.7 work and stabilized this cycle).

### Changed

- **AgentsContext / context-provider refactor.** Folders, inbox-tasks, agents, and health-status polling lifted into shared providers; AgentsPage / ProjectDetailPage / ProjectsPage read from context instead of duplicating fetches. Agents-page tasks fetch limit raised from 100 to the backend max (1000).
- **Inbox card UX overhaul.** Title becomes inline-block `contentEditable` for native cursor placement; click on empty title-row / timestamp / gap / any empty area collapses the expanded card; iOS word-snap caret placement overridden; transitions tightened to avoid jitter.
- **MCP enforcement.** `task_create` / `task_update` now require `model` and `effort` tags.
- **WebSocket realtime.** `emit_agent_update` short-circuits for synthetic subagents; `agent_update` re-emitted on insights apply / discard / generate-done so other clients flip the pill without a refetch; `new_message` emit centralized in `flush_agent`.
- **Token-counts popover** notes that values come directly from the CC session JSONL, and labels Xylo vs. CC sessions explicitly.

### Fixed

- `stop_agent` `NameError` on the retry path.
- Project rename: `bookmarked_messages` rows were not being migrated.
- Lifetime cost double-counting on resumed agents (now deduped by message id).
- Various e-ink-mode contrast and badge-rendering edge cases (saturated bg → light text/SVG, status dots inside saturated pills, popover/toast borders, inline code styling).
- `frameLogger.js`: bumped class-attr truncation 60 → 200 chars so longer Tailwind utility chains aren't cut off in mutation logs.

## [0.9.3] - 2026-04-29

### Changed

- BookmarksSection rows: title editing now via an explicit pencil icon (replaces the long-press gesture); pencil and time both pinned to the top-right of the row.
- Title clamps to a single line (truncate); the original-message line below also clamps to a single line so rows stay uniform height.
- 4o-mini bookmark-title summarizer prompt tightened to 3–6 words, ≤40 chars for cleaner row display.
- Split-screen Projects tab: pane restores the last-opened project on re-entry instead of resetting to the project list.

## [0.9.2] - 2026-04-28

### Changed

- Split-screen pane chat header: replace the X (close) icon with the chevron-left back arrow and switch to `resolveBack()` logic so the pane respects the navigation state chain (e.g. A→B→back goes to A) instead of always returning to `/agents`.

## [0.9.1] - 2026-04-28

### Changed

- BookmarksSection rows: shrink primary line to 13px; swap title/description in row layout; drop the "note" pill.
- Right-side bookmark icon on a row toggles in both directions; second click on the filled icon removes the bookmark.
- Empty-state hint reworded long-press → double-tap to match the new gesture.

### Fixed

- Keep de-bookmarked rows visible until the next page mount so the row doesn't vanish mid-toggle.

## [0.9.0] - 2026-04-28

### Added

- **Bookmarked messages.** Double-tap a chat bubble → **Bookmark** to save standout turns. New `bookmarked_messages` table + `routers/bookmarks.py` CRUD endpoints. Each bookmark stores the message id, an optional user note, and a `gpt-4o-mini`-generated summary + emoji. Media references (image/file paths) are extracted from the bookmarked message and ±2 neighbors and cached. Per-project **Bookmarks** section; tapping a row scrolls to the original turn with a yellow focus-flash (2 cycles, tinted by the bubble's own color).
- Post-bookmark note prompt — compact amber pill that expands into a textarea inline; the AI summary serves as the title fallback if you skip.
- Bookmark icon on attachment action buttons (the chat-bubble menu remains the canonical entry for text bookmarks).
- Service Worker now caches Fluent UI emoji SVGs `CacheFirst`.

### Changed

- Chat-bubble interaction: **double-tap** opens the per-message action menu (Copy / Modify / Delete / Bookmark). Long-press menu trigger removed; only one menu is open at a time; outside-pointerdown auto-closes.
- MonitorContext warms its cache 2s after app mount, then background-polls every 60s while inactive.

## [0.8.9] - 2026-04-28

### Fixed

- ESC-then-queued-dispatch race that put the just-sent user bubble *above* the assistant turns and the "Request interrupted by user" bubble in chat. `dispatch_pending_message` now drains pending JSONL turns synchronously before allocating `display_seq`, so the interrupt and any in-flight agent activity get their seq first and the promoted user message lands after them in chronological order.

## [0.8.8] - 2026-04-28

### Added

- Offline-resilient adoption path for unmanaged CLI sessions: `session-start.sh` writes `/tmp/xy-pending-unlinked/pane-{key}.json` when its HTTP call fails, mirroring the existing managed-agent `xy-{id}.newsession` fallback.
- `POST /api/unlinked-sessions/replay` with a liveness check (live `claude` pane whose currently-open JSONL matches the stashed `session_id`) plus the same 4-layer guards as the live hook path. Lifespan auto-replays stashes on backend startup; the Agents page refresh button also triggers replay.

### Changed

- Removed `redispatch_stuck_queued`; folded its logic into `dispatch_pending_message`. The previous EXECUTING guard checked `agent.generating_msg_id`, which has been dead-set to `None` since the 4/27 sync_engine refactor — meaning queued messages could promote pre-sent → sent while Claude was mid-turn. The 10s age cutoff is also removed under the pre_sent architecture.

### Fixed

- `sync_full_scan` content_mismatch audit now skips USER turns; a `stop_hook_summary` landing on a USER turn previously triggered spurious drift logs.

### Removed

- "CLI sessions started while backend is offline" entry from README's Known Issues and TODO.md (now handled by the stash + replay path above).

## [0.8.7] - 2026-04-28

### Fixed

- `sync_full_scan` benign-drift branch advancing pointer past missing UUIDs. When `earliest_missing_idx >= ctx.last_turn_count`, the previous code unconditionally set `ctx.last_turn_count = len(turns)`, jumping the pointer over the missing UUID and making the next `sync_import_new_turns` slice miss it forever. Result: `tool_use` turns silently absent from DB until a later `real-drift` rewind happened to catch them. Fix: leave the pointer at `_earliest_missing_idx`.

### Added

- `DRIFT_INSTRUMENT` logging (`sync_start`, `sync_done`, `drift_detected`, `count_mismatch`, `savepoint_integrity_error`, `compact_purge`). Log-only, no behavior change.

## [0.8.6] - 2026-04-28

### Added

- Split `/display` into `/display/sent` and `/display/pre-sent` endpoints; chat state split into `sentMessages` + `preSentMessages` so delivered and queued bubbles render independently.
- `display_writer` writes `seq=0` retry marker on agent create; `dispatcher` emits `pre_sent_tombstoned` event on promote so the frontend can drop the queued bubble.
- Persist deferred-section expanded state across reloads (inbox & agents).
- Base screenshots for input bar, inbox defer, and new agent for the getting-started walkthrough.

### Changed

- Sync state machine: accumulate `_saw_*` signals across `new_turns` instead of reading only the last turn — out-of-order JSONL writes (trailing assistant after `stop_hook_summary`) no longer mask the stop signal.
- New rule: `saw_assistant_turn → EXECUTING`. Sync now flips `IDLE → EXECUTING` from JSONL truth alone, without depending on the `user_prompt` hook chain.
- ESC / interrupt: write `IDLE` directly when interrupting `EXECUTING`; send `C-l` instead of `End + C-u` for reliable input clear in the tmux pane.
- `/display/sent`: sort messages by `seq` for deterministic ordering.
- AgentChatPage compact header drops the redundant status dot, softens Stop/Resume to ghost pills, drops branch text from row 2 (icon-only worktree pill is enough).
- README notes third-party / local model support and the UI scope caveat; drops the iPad / mobile-browser known issue.

### Fixed

- Stale localStorage when an expanded inbox card is collapsed via outside-click.
- Tap-to-edit title/description on expanded inbox cards; place caret at click point on first edit tap.
- NewTaskPage: align worktree input row to the Effort selector's right edge.

## [0.8.5] - 2026-04-27

### Changed

- Persist JSONL sync pointer to DB to eliminate full history replay on restart.
- Skip stop-hook / interrupt / rate-limit side effects on initial scan.
- Skip status inference on initial / pointer-reset scan.
- Distinguish real drift from benign timing gaps in `sync_full_scan`; add EXECUTING inference + plumb compact trigger; emit missing /compact completion signals; derive last-turn signal correctly + wake_sync on tool_activity.
- Drain old JSONL + reset sync pointer on session rotation; trust DB on `_recover_agents` + stale fallback; slim `_start/_stop_generating` to in-memory only; emit `agent_update` after `rebuild_agent` on session rotation.
- Hooks only `wake_sync`, never write status directly. Router drops launch-task IDLE write + resume fallback.
- Rename `MessageStatus.QUEUED` → `SENT`; restore inbox-card tag click → popover.

### Fixed

- `UnboundLocalError` on `_time` in sync loop.

### Removed

- Dead `MessageStatus.PENDING` and `TIMEOUT` enum values.
- Client-side telemetry 20h gate; rely on Worker per-day dedup.

## [0.8.4] - 2026-04-27

### Changed

- Telemetry: drop the client-side 20h gate, `last_heartbeat` file, and `force` parameter. `record_heartbeat()` now resolves `install_id` and POSTs unconditionally; the Worker handles per-day dedup for Discord while D1 keeps the full event stream. Schedule a 24h heartbeat loop in the FastAPI lifespan so long-running orchestrators that never restart still ping daily.
- Chat-message WS events are now signal-only (no payload).
- Renamed predelivery → pre_sent across the codebase + idempotent migration for legacy rows.
- Dispatcher emits `agent_update` after `rebuild_agent` on session rotation; emits `predelivery_tombstoned` on bulk-fail.
- `sync_engine` emits the missing `/compact` completion signal and emits `new_message` for any insert (covers post-compact and CLI-typed user messages).
- Subagents are filtered from the project agent list, with a legacy backfill.
- Restored tag-click → popover on inbox cards; deferred-section header layout uses a 3-col grid.

### Fixed

- Telemetry restart-within-20h-of-previous-send no longer silently swallows the heartbeat.

### Removed

- Dead `agent_stream` token-streaming code path.
- Eight earlier `agent_update` / `task_update` emit additions (agents.stop, regenerate-insights, insights success, mark_read, tasks.dispatch, dispatcher notify_at, retry-summary, suggestions) that caused redundant/incorrect updates.

## [0.8.3] - 2026-04-26

### Added

- **Long-press multi-select unified** across every list surface — Inbox tasks, Agent rows, Project tiles, agent rows inside a project's detail page, Trash rows. Long-press pre-selects the pressed item.
- Bulk action bars per surface: Inbox (AI batch-process / Start / Delete); Agents and ProjectDetailPage agent list (mark Read / Stop / Delete); Projects (Activate / Archive / Delete with uniform-state enabledness); Trash (Restore / permanently Delete).
- One-shot startup backfill in `database.py` promoting `parent_id`-set rows with `is_subagent=0` to `1`, protecting against future drift.

### Changed

- Selected card state unified across surfaces: `ring-2 ring-cyan-500/50 brightness-[0.88]` with a 400ms `cubic-bezier(0.22,1.15,0.36,1)` transition that includes the `filter` property.
- Bulk-bar buttons now share size, layout, and disabled treatment (`flex-1 min-h-[40px]` icon-buttons, `disabled:opacity-50 disabled:cursor-not-allowed`); Agents' Delete switched from `bg-red-900` to the standard `bg-red-600`.
- Select-mode header bars use a 3-col grid (Select All / N selected / Done) for a centered middle label.
- README's Gestures & Shortcuts section documents long-press → multi-select and the per-surface bulk actions.

### Fixed

- `GET /api/projects/{name}/agents` was missing the `Agent.is_subagent == False` filter — subagents could appear in a project's agent list until they hit the SubagentStop hook.

### Removed

- Per-card check-circle indicator on Agents and Projects (the ring + darkening is the single visual language).
- Clipboard-icon toggle button from the Inbox and Agents top bars (long-press is the sole entry point).

## [0.8.2] - 2026-04-26

### Changed

- `WorktreePill` interaction model now matches the `xylo id` pill: hover (mouse) opens the popover, long-press (touch) opens it, double-click copies. Outside `pointerdown` closes; popover hover cancels the close timer.
- Both the `xylo id` and `worktree` popovers gain an upward arrow rendered after content so it paints over the seam to form a continuous shape.
- Replaced popover drop-shadow with a reusable `.shadow-popover` utility (`0 4px 20px @ 14%` below + `0 -1px 6px @ 6%` above) — softer macOS-style halo matching the existing `.glass-bar` aesthetic.

### Removed

- Insights-status tags (`failed` / `generating` / `insights`) from the chat-page top bar; the dedicated insights surface remains the source of truth.

## [0.8.1] - 2026-04-26

### Added

- New shared `WorktreePill` component: icon-only purple chip; single-click expands a centered popover showing `worktree: <name>` plus a Copy button. Used in `AgentRow` (Agents page + Project detail), `InboxCard`, and the `AgentChatPage` header — chat list / project page / agents list / inbox / chat header now share the same compact tag.

### Changed

- Trigger uses `span` + `role="button"` so it nests inside the AgentRow card button without invalid HTML.

### Removed

- Dark/light theme toggle from the chat-page icon toolbar; theme toggle remains available on Inbox, Agents, Projects, Monitor, Tasks, Git, Trash, Split, and New pages. The chat-page toolbar is now scoped to agent-specific actions (refresh / browse / mute / defer).

## [0.8.0] - 2026-04-26

### Added

- New row 2 in the chat header hosts task and agent-id as interactive pills. ID pill is labelled `id` (4 chars, sized for tap targets); hover or long-press shows a portal-rendered popover (escapes `overflow-x` clipping, centered under the pill, prefixed `xylo id:`); double-click copies.

### Changed

- Tags collapsed onto a single line; action buttons (Stop/Resume/OK) replaced with an icon toolbar in tinted style.
- Status dot moved next to the title, matching `AgentRow` card spacing and the running-pulse on `ProjectsPage`.
- Tag row aligned with the card-list visual style.

### Removed

- Noisy chips: `model`, `effort`, `tmux`, and the `deferred` chip (which now also auto-hides once the defer time has passed).
- Native `title` tooltip on the ID pill (avoid duplicate-popover flicker); the overlay that used to intercept hover/dblclick was removed.

### Fixed

- Monitor health chip restored after an earlier drop.

## [0.7.1] - 2026-04-25

### Fixed

- Telemetry `daily_heartbeat` was reporting `v0.6.1` after the v0.7.0 release because `_load_version()` read the root `package.json` (the `create-xylocopa` npm installer's own version), which the v0.7.0 release commit didn't bump. Version source switched to `frontend/package.json` — release flow already bumps that on every tag, so heartbeat version stays in sync automatically going forward.

### Changed

- Sent-bubble check icon brightened from `gray-400/50` to `gray-100/80`; sent bubble matches delivered colour while the grey check distinguishes the two states.

### Removed

- Stale refactor plan docs and references to them in source comments.

## [0.7.0] - 2026-04-24

### Added

- Anonymous `daily_heartbeat` telemetry (one event/day, gated to >20h interval) sent to a Cloudflare Worker that writes to a private D1 database. No IPs, no user content. Opt-out via the Monitor page toggle, `XYLOCOPA_TELEMETRY=0`, or `telemetry: false` in `~/.xylocopa/config.yaml`. See `## Telemetry` in README and [`orchestrator/telemetry.py`](orchestrator/telemetry.py).
- Defer agent: hide an agent from the main list and mute its notifications until a chosen time. Collapsible "Deferred" section on the Agents page. Defer chip uses an hourglass icon in both the inbox card and chat header.
- Per-message bubble menu in chat with Copy / Modify / Delete actions.

### Changed

- **Pre-delivery refactor.** Messages now flow PENDING → pre-delivered → sent → delivered. Dispatcher promotes pre-delivery → sent atomically on tmux send; display file uses read-before-truncate rebuild; sync content-matching is restricted to sent-state DB rows. WebSocket emits `predelivery_*` and `message_sent` events; frontend chat bubbles render the new state machine.
- DELETE is now single-step (tombstones queued/scheduled rows immediately) and ESC does soft-cancel through a separate endpoint — they no longer share behavior.
- SessionStart hook is the canonical launch-wake signal. JSONL polling fallback removed from the launch path; orchestrator waits on the hook.
- `/compact` and `/clear` slash-commands emit `message_executed` on completion; web slash-command rows are matched against their `<command-message>` JSONL wrapper instead of creating duplicate CLI rows.
- `datetime-local` inputs in the frontend prefill in the local timezone rather than UTC.
- README hero rewritten ("Many projects. One attention."), install simplified to a curl one-liner, GTD framing added.
- Tmux launch poll tightened to 200 ms; `TUI_SETTLE_DELAY` reduced from 3 s to 0.5 s.

### Fixed

- `cancel_message` is idempotent when the row is already `CANCELLED`.
- Tmux send no longer breaks on messages that begin with `-`.
- DB fallback no longer leaks `CANCELLED` messages back into display output.
- Duplicate pre-delivery bubble and out-of-order Attempts panel in chat.
- First message after orchestrator startup is synced immediately rather than waiting for the next tick.
- Sync engine restores task-launched rows to the match-candidate pool so they don't get orphaned.

### Removed

- Dead `/messages` endpoint and stale `emit_message_update(CANCELLED)` WS event.
- `XY_QUEUED_FALLBACK` scaffolding (Phase 3 cleanup of the predelivery migration).
- Unreachable partial-output salvage path in the dispatcher and the corresponding README claim.
- Telegram notification claims from docs (the integration was never implemented).

## [0.6.1] - 2026-04-23

### Changed

- Agent chat back button returns to the originating page (project detail or Agents list) instead of always going to a default.
- Inlined the back chevron into the project header so the title and nav share a single row.
- Pinned the project detail `FilterTabs` to the header, matching the sticky behavior of `AgentsPage`.
- Agent → orchestrator callbacks switched from the HTTP token channel to MCP tools; legacy callback token path removed.
- `session_source_dir()` now resolves `project_path` via `realpath`, fixing symlinked-project edge cases (follow-up to the `ef07c26` /clear rotation fix).
- Bumped `package.json` version to `0.6.1` (was stuck at `0.4.1` across v0.5.0 and v0.6.0).

### Removed

- In-project "New Agent" card — the New Task sheet is now the sole entry point for creating agents inside a project.
- Reverted the folders-endpoint union change while keeping `reconcile.py` (the union broke a downstream consumer; reconciliation logic is preserved).

## [0.6.0] - 2026-04-23

### Added

- **Resume hint** — per-project LLM-generated mood + recap card on each project tile. Anchored to three signals (task name, latest user intent, last 8 turns) so the recap doesn't drift into whatever tangent happened most recently. Regenerated on the stop hook.
- New Fluent UI Emoji set for project cards (`ProjectRing` + `FluentEmoji` components, MIT-licensed, jsdelivr CDN with system-emoji fallback). Inline emoji editor wired into both `ProjectsPage` and `ProjectDetailPage`.
- Day/week toggle on the time-badge popover.
- New delete action on cancelled message bubbles.
- Beginner getting-started guide (en + zh).

### Changed

- ~54× reduction in token consumption for MCP cross-session references.
- Pipeline-order status bar replaces the stat text strip — color-coded by task status.
- Compact single-row project strip; dropped the progress ring around the icon.
- "Task" toggle defaults to ON when creating new agents.
- Persist the voice transcription/refine pipeline in IndexedDB so it survives page reloads. `keepalive: true` on transcribe/refine fetches; `MediaRecorder.start(1000)` timeslice for long recordings.
- Inject a short-TTL `XYLOCOPA_AGENT_TOKEN` into spawned tmux agents; harden tmux create + use the worktree cwd on agent resume.
- `realpath` worktree paths in session JSONL resolution; triage meta-agent sessions attributed to the self-host project.
- `archive` blocked while sessions are active; tasks are unassigned rather than cancelled.

### Fixed

- Race where unmounting the recorder deleted its IndexedDB entry mid-flush.
- Pending interactive cards now dismiss on ESC / interrupt.

### Removed

- Git-remote chip from project cards and agent/task counts from the project detail header.
- Active Agents section from the Monitor page.
- Frustrated-face emojis from the resume-hint mood palette; re-added 🤯 as "mind-blown".

## [0.5.0] - 2026-04-21

### Added

- New `UnreadProvider` centralizing per-agent unread counts on the WebSocket event stream (`agent_update`), with HTTP resync on WS (re)connect. BottomNav Agents badge, FAB, and PWA app badge render from one source — no more 5s poll divergence.
- `AttentionButton` (renamed from the split-screen FAB): draggable, turns into a cyan unread total when any agent has new messages, tap jumps to oldest-unread (FIFO), long-press opens split screen.
- Viewing-time stats popover on the Projects header, aligned with the Weekly Success Rate layout. Press-and-hold a daily bar reveals its duration; a dim duration label sits above every bar so magnitudes are readable without interaction.
- Per-project session viewing time tracking (powers the new popover).

### Changed

- `/compact` handling: drain pending JSONL turns in `PreCompact` before pausing sync, and defer the single-check until after the drain completes.
- `mark_delivered` on slash-command delivery transitions `QUEUED → EXECUTING` and emits a `message_update` event so the UI can drop muted "pending" styling.
- `agent_update` WS payload now carries `unread_count` and message preview.
- Push fanout is fire-and-forget so it doesn't block the event loop.
- Restart-button reload wipes the service worker and caches before reloading.
- Installer prints a system-package-manager install hint instead of auto-invoking `sudo`.
- Monochrome cyan palette + roomier tooltip on the viewing-time popover; viewing-time ring replaced by a duration pill.

### Removed

- "All Tasks" section from the Weekly SR popover and the project-detail SR popover; percentage text stripped from popover numbers (first-attempt + daily sparkline labels keep them).

## [0.4.1] - 2026-04-20

### Added

- Orchestrator startup `WARNING` if any `frontend/src` file is newer than `frontend/dist/index.html`, making stale-build deploys visible immediately.
- Reload-storm detector that piggybacks on the reload-trace beacon and logs `ERROR` when a single client IP triggers ≥5 patch-failed reloads in a 60s window.
- New git `post-commit` hook auto-rebuilds `frontend/dist` when a commit touches `frontend/*` (excluding `dist/`, `node_modules/`, `dev-dist/`); serialized via `flock`, installed through `tools/install-git-hooks.sh`.
- Navigation and API timing console logs to aid client-side perf debugging.

### Changed

- `install.js` runs the step-1 dependency check before `git clone`, so users without `git` get the auto-installer guidance instead of a raw clone failure.
- pm2 → systemd migration: `install.js` prompts for auto-start independently of "start now"; `run.sh restart` refreshes `dump.pm2` so boot-time resurrect matches current config.
- npm package version aligned with the git tag (was stuck at 1.0.0).

### Fixed

- New-agent card overflow on the project detail page — Model/Effort pills now wrap inside the card on narrow viewports instead of pushing the right-side toggles off-screen.

## [0.4.0] - 2026-04-19

### Added

- **Jump-to-Unread FAB.** The split-screen FAB morphs into a cyan unread-count badge when any agent has new messages; tap jumps to the oldest unread (FIFO), long-press always opens split screen.
- New `GET /api/agents/unread-list` endpoint returns unread agents sorted oldest-first.
- New `tools/push_reset.py` — interactive picker that sends a remote SW-reset push to a specific device, unblocking PWAs wedged on a stale bundle.

### Changed

- Badge updates are event-driven over WebSocket (`new_message` / `agent_update`) with 150ms debounce; 5s poll kept as a reconnect safety net.
- `_send_webpush` fans subscribers out through a 16-thread pool instead of looping serially; total time `sum(rtt)` (~2.5s across 11 subscribers) → `max(rtt)` (~250ms).
- ESC button now triggers `wake_sync` so the cancelled-bubble state lands in the UI without waiting for the next poll tick. `loadData` defers 150ms after ESC wake-sync so the refresh lands after the sync pass.
- Cancelled message bubbles render with a gray background.
- Service-worker auto-reload disabled to break an iOS PWA reload loop. Cache-buster `CV` bumped v2 → v3 to force stuck PWAs to unregister the old SW.
- `./run.sh restart` and `/api/system/restart` auto-rebuild stale `frontend/dist/` before restarting. Vite dev → `vite preview` in production to avoid HMR-reconnect white screens.
- README hero slimmed to a single bold tagline; jump-to-unread FAB added to the Monitor feature list.

### Fixed

- `sync_engine` stop-hook branch emits `agent_update` immediately after the unread bump instead of waiting for the full turn import + push fanout.

## [0.3.2] - 2026-04-19

### Added

- New skill picker panel in the chat input — frequency-sorted, slash-triggered, scans both `~/.claude/skills/` and per-project `.claude/commands/` markdown.
- Decoupled skill enumeration into its own module (`skills.py`) with per-project cache and parser folding.
- Hybrid allowlist + `KNOWN_PROBLEMATIC` blocklist for slash-command gating.
- Per-agent tab title and favicon (hue derived from agent ID, skipping the blue band).
- Permission-mode segmented three-way switch (Normal / Auto / Plan), synced to `agent.skip_permissions`.
- New `xhigh` effort level for Opus 4.7 CLI (between `high` and `max`).

### Changed

- Queued messages cancelled by the user are soft-cancelled — the row stays in the DB with `CANCELLED` status, the bubble greys in place rather than disappearing.
- `Esc` bulk-cancels all active queued messages.
- `dispatch_pending_message` gained a busy guard — refresh / wake-sync no longer send-keys into an `EXECUTING` pane.
- `_stop_generating` auto-dispatches the next `PENDING` message on every `EXECUTING → IDLE` transition.
- Streaming poll tightened to 100ms; permission re-check stays at ~30s.
- `.mcp.json` uses an absolute venv python path so MCP works regardless of CWD.
- Open agent chat in a new tab from list views.
- README repositioned around AI-native GTD; surfaces crash-recovery and durability properties.

### Fixed

- `/plugin` blocked because the TUI marketplace UI never fires `UserPromptSubmit`, leaving messages wedged.
- `<command-message>` wrapper filtered from chat history (universal slash-command JSONL injection that lacks `isMeta`).
- `AuthGuard` recovers softly when the backend reconnects instead of forcing a full reload.

## [0.3.1] - 2026-04-18

### Added

- `create_task` MCP tool — any Claude Code session can drop a task into the inbox without DB scripts or auth navigation. Reuses the `TaskCreate` Pydantic schema; `project` is optional and falls back to longest-prefix `cwd` match against `projects.path`.
- README "Durable by Default" section with source-linked bullets covering session cache, unlimited retention, partial-output salvage, tmux-anchored recovery, resume, backups, draft persistence, session-dir migration, orphan cleanup. Features table renamed "Backups" → "Reliability & Recovery".
- Reload-trace probe: every reload trigger (SW `controllerchange`, explicit `location.reload()`, vite HMR full-reload, iOS background kill) is logged via `sendBeacon` to `/api/debug/auth-diag`.

### Changed

- `AuthGuard` performs soft recovery on server reconnect instead of full page reload.
- `location.reload` override installed in `<head>` (before any ES module) detects vite-originated reloads via stack trace and suppresses them — works around vite HMR client reloading on every WS reconnect.
- `/api/debug/frontend-state` gated behind a localStorage flag.

## [0.3.0] - 2026-04-18

### Changed

- **Rebrand AgentHive → Xylocopa.** Backend renamed with `agenthive` compat shims; frontend rebrand with localStorage migration (`agenthive_*` → `xylo_*`); CLI / installer / scripts renamed (`ah` → `xy`). Tmux pane prefix switched to `xy-{agent_id[:8]}`; legacy `ah-` sessions still recognized. GitHub repo moved to `jyao97/xylocopa`. Bee mascot (carpenter bee, *Xylocopa*) replaces robot icon; PWA icons regenerated.
- LaTeX math rendering via KaTeX in the chat view.
- Media file extraction from tool-usage entries (absolute paths supported) for inline preview.
- All images render via `FileAttachments` thumbnails; inline duplicates suppressed.
- Agent cards redesigned to match Inbox style — tag pills, single-line preview truncation. Hollow status ring with radiating glow for executing; cyan-family palette for idle/stopped. Drag-and-drop reordering with `sort_order` column migration.
- FloatingTaskCard redesigned with structured metadata layout and InboxCard-style tag pills. Note module added (markdown rendering, no border). **Quick Note** renamed from Notes — personal memo, not model input.
- Unlock screen redesigned as horizontal liquid-glass card.

### Fixed

- Stuck `pendingSendRef` causing auto-send after failed upload.
- Deferred-send decoupled from text changes via refs.
- File browser localStorage scoped per-agent-session; stale project-scoped keys cleared.

## [0.2.1] - 2026-04-14

### Added

- LaTeX math rendering — chat messages now render LaTeX formulas via KaTeX. Block math (`$$...$$`) displays as centered equations; inline math (`$...$`) renders within text. Requires at least one LaTeX marker (`\`, `{`, `^`, `_`) to avoid false positives with currency symbols.
- Tool-usage media extraction — file paths mentioned in tool calls (e.g. `Read /path/to/image.png`) are detected and displayed as media previews in the following text bubble.
- Known Issues section in the README documenting iPad and mobile-browser layout caveats.

## [0.2.0] - 2026-04-13

### Added

- macOS support: process detection (`pgrep`/`ps`) moved into a platform abstraction layer; macOS Keychain support for Claude credential reading; NFC Unicode normalization and CWD fallback for JSONL path resolution on APFS; Bash 3.2 quoting fix in `session-start.sh`. iOS CA certificate install guide, Web Clip profile generation, mkcert CA root support.
- Cross-session MCP — agents read other agents' conversation history via `"check ah session <session_id>"`.
- Retry-adjusted stats: progress metrics account for retried tasks; unified formula across TaskRing, weekly rate, and daily sparkline.
- Timezone support for daily/weekly stats.
- Task toggle on agent creation forms — optionally create a tracked task when launching an agent.
- Gestures documented: double-tap to copy session IDs and messages.

### Changed

- Viewport: `h-dvh` → `fixed inset-0` migration for iOS Safari bottom gap; `overscroll-behavior` to prevent rubber-band scroll breaking layout.
- Import retry with loading fallback for iOS PWA background resume; split-screen `h-dvh`/`h-screen` inconsistency fix.
- Unified split-screen and single-screen navigation bar layout.
- File browser converted from full-screen modal to bottom sheet with cached state.
- Code copy button always visible on mobile, hover on desktop. Scrollable code blocks and tables inside chat bubbles.
- Chat bubble width tuned to `min(85%, 30rem)`.
- Push notification taps preserve split-screen mode.
- `skipWaiting` + `clientsClaim` for instant Service Worker updates.

### Fixed

- Orphaned tmux sessions properly killed on agent stop.
- Page navigation lag (polls no longer restart on every route change).
- Rate-limit-options menu auto-dismiss after rate limit clears.

### Removed

- Broken Agents-by-Status card and redundant Claude Processes card from Monitor.

## [0.1.1] - 2026-04-13

### Changed

- **Session detection simplification.** Replaced 4-strategy `_discover_session_id_from_pane()` fallback chain with a single `SessionStart` hook path. The hook now writes `session_id` at entry creation time; the adopt endpoint reads it directly without discovery.

### Removed

- Multi-strategy discovery (file descriptor scanning, JSONL freshness-window matching, `/tmp/ahive-pending-sessions/` signal mechanism, shell script offline fallback).

### Added

- Commit-safety rules added to `CLAUDE.md` (no secrets, no certs, no personal paths, no database files).

## [0.1.0] - 2026-04-13

### Added

- Multi-agent orchestration with tmux-based sessions
- Real-time WebSocket communication for live agent output streaming
- Project management with git integration and isolated worktrees per agent
- Voice input support via OpenAI Whisper for hands-free task creation
- Mobile-responsive PWA interface with Add to Home Screen support
- Task management inbox with drag-to-reorder priorities
- Agent coordination with configurable concurrency limits and timeouts
- Session persistence and JSONL-based history with crash recovery
- Push notifications for agent status changes (finish, error, needs input)
- Password authentication with rate limiting and inactivity-based session lock
- HTTPS with self-signed certificate generation for LAN encryption
- System monitor for disk, memory, and GPU usage
- CLI session sync (read-only import and live-tail of terminal sessions)
- Dark/light theme with system-aware toggle
- Automatic hourly database backups
