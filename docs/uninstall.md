# Data locations & uninstall

## Where your data lives

- SQLite DB: `data/orchestrator.db` in the install directory (tasks, projects, agent metadata, configs)
- Agent sessions: `~/.claude/projects/<encoded-path>/*.jsonl` — Claude Code's native files, not duplicated
- Per-project memory: `<project>/PROGRESS.md` inside each project's repo
- Backups: `backups/`; uploads: `~/.xylocopa/uploads/`

A full snapshot is the install directory plus `~/.claude/projects/`.

## Uninstall

```bash
pm2 delete xylocopa-backend xylocopa-frontend && pm2 save
rm -rf ~/xylocopa-main          # or wherever you cloned it
rm -rf ~/.xylocopa              # uploaded files
rm -rf ~/xylocopa-projects      # optional: your project directories
```

Project code, git history, and session JSONL files in `~/.claude/projects/` are untouched. If you want Claude Code's default cleanup window back, remove `cleanupPeriodDays` from `~/.claude/settings.json`.
