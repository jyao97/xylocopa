# CLAUDE.md
> Read this file at the start of every task. Rarely modified — only update when project structure or conventions change.

## Universal Rules
- Think from first principles. Don't assume the user knows exactly what they want or the best way to get it. Start from the original requirement, question the approach, and suggest a better path if one exists
- Think step by step. Investigate before coding — read relevant code, trace the full flow, print findings before proposing a fix
- When a task is complex, break it into sub-tasks and spawn sub-agents to work in parallel
- Never guess. If unsure, read the code, check logs, or run a test first
- Every task must produce a visual verification artifact (screenshot, plot, diff, rendered output)
- If the goal or motivation is unclear, stop and discuss before writing code. If the goal is clear but the path isn't optimal, say so and suggest the better approach
- For "does X cause Y in the UI" questions, trace the full chain: backend write → event emit → API caller → component state → render condition. Grepping the backend alone is insufficient — feedback often comes from local setState or per-entity refetch, not WS
- Match assertion strength to evidence. If you only verified part of a claim, say so explicitly — don't write "❌ doesn't trigger" / "0–5s delay" when you only checked one of multiple paths. Downgrade language ("backend doesn't, frontend not checked") instead of presenting a partial check as complete

## Do NOT
- Do not refactor or rename files unless the task explicitly requires it
- Do not delete or modify tests unless asked
- Do not change dependencies/package versions without explicit approval
- Do not modify CLAUDE.md
- Do not write to memory files (.claude/memory/, MEMORY.md) — only the orchestrator manages persistent memory

## Output Rules
- Keep responses concise — no long explanations unless asked
- For large outputs (logs, data), write to a file instead of printing to stdout
- Truncate error logs to the relevant section, don't paste entire stack traces

## Git Conventions
- Commit message format: `[scope] brief description` (e.g. `[frontend] fix image zoom gesture`)
- Commit frequently — small atomic commits, not one giant commit at the end
- Commit to master directly when appropriate

## Concurrency Rules
- Check which files other agents are currently modifying before editing shared files
- Prefer creating new files over modifying existing shared ones when possible

## Working Log (context recovery)
- Each agent keeps a log at `working_logs/working_<agent-id>.log` in the MAIN project root — never inside a worktree (resolve the main checkout path first); `<agent-id>` = first 8 chars of `$XY_AGENT_ID`
- At task start: read your existing log if present, then append `[YYYY-MM-DD HH:MM] START <one-line goal>`
- Append one line per milestone only — key decisions, completed steps, blockers — not every action
- Append-only, never rewrite or delete entries; `working_logs/` is gitignored — never commit it

## Code Style
- Follow existing patterns in the codebase — don't introduce new conventions
- Match the indentation, naming, and structure of surrounding code

## Project: cc-orchestrator (Xylocopa, formerly AgentHive)
- Tech Stack: Python 3.11+ (FastAPI), React (Vite), SQLite
- Top Dirs: certs/, frontend/, orchestrator/, project-configs/, projects/
- Config: .env
- Entry: orchestrator/main.py
- Tests: test_multi_question.py
- Build (frontend): `cd frontend && npx vite build && pm2 reload xylocopa-frontend` — required after a frontend-only edit (prod-build mode, no HMR). Skip the manual build if you're about to restart anyway: both `./run.sh restart` and `/api/system/restart` auto-detect stale dist/ and rebuild first
- Test (frontend): `cd frontend && npx vitest run`
- Verify backend: `cd orchestrator && ../.venv/bin/python -c "from models import *; print('OK')"` (use the project venv — system python lacks the deps)
- Restart: `./run.sh` or POST `/api/system/restart` (both auto-rebuild stale frontend before restart)
- Logs: `logs/server.log`, `logs/orchestrator.log`

## Xylocopa context
- This project is managed by xylocopa. The orchestrator MCP server is auto-registered via `.mcp.json`.
- Available tools: `project_*`, `task_*`, `session_*`, `agent_*`, `system_health` — list/get/read/create/update/dispatch/scaffold only, no destructive verbs.
- Full reference: xylocopa repo `docs/agent-mcp-tools.md`.

## Project-Specific Rules
See README.md for detailed project documentation.
- Worktree sessions: always use `_resolve_session_jsonl()`, never bare `session_source_dir()`
- tmux pane matching: `xy-{agent_id[:8]}` (legacy `ah-{agent_id[:8]}` still recognized) session name is authoritative
- CWD matching: use `startswith(proj + "/")` not `==` (worktree subdirs)
- SQLAlchemy: `metadata` is reserved — use alt attr name with explicit column
- When fixing a helper, grep ALL call sites — don't assume you found them all
- Queued messages use stop-hook dispatch: PENDING in DB → stop hook fires → send via tmux → UserPromptSubmit confirms delivery
- Realtime UI follows three independent paths, all of which must be considered when diagnosing perceived latency: (1) WebSocket push (`emit_agent_update` carries status/unread_count/last_message_preview/last_message_at/has_pending_suggestions/insight_status; `emit_agent_created` only from `tasks.py:169`, NOT from `agents.py:518` create_agent or `:712` launch-tmux), (2) 5s list poll (`AgentsPage.fetchAgents`, `TasksPage.fetchTasksV2`, etc — wholesale replace), (3) caller-side feedback after a mutation (local `setState` + per-entity refetch via `loadData`/`fetchAgent(id)`, e.g. apply/discard insights uses this, NOT WS). When a user asks "why is X laggy" or "why is Y instant", check all three.

### Release conventions
- Tag format: `v<major>.<minor>.<patch>`
- Release notes: overview paragraph + categorized changelists with context
- Tone: factual changelog style — describe what changed, no adjectives or selling language
- Use `gh release create` with `--notes` — include `Full Changelog` compare link at the bottom
- Only create releases when explicitly asked by the user

### Commit safety (public repo)
- **No secrets**: never commit API keys, tokens, passwords, or private keys — even as "examples". Use empty values in `.env.example` (e.g. `OPENAI_API_KEY=`), not fake-looking placeholder strings
- **No certificates**: `.pem`, `.crt`, `.key`, `.p12` etc. are gitignored — never force-add them
- **No personal paths**: use `~/`, `/home/YOUR_USERNAME/`, or relative paths in committed files — never hardcode real user home paths
- **No internal docs**: don't commit audit reports, release strategy, or internal planning docs — they expose security details and roadmap
- **No database files**: `.db`, `.sqlite3`, `:memory:` stubs are gitignored — never force-add them
- **Before committing new config/example files**: verify they contain only placeholders or empty values
