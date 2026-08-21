# Migrating from AgentHive

Xylocopa was previously named AgentHive. The upgrade is backward compatible:

- `agenthive` CLI remains as a symlink; `AGENTHIVE_*` env vars are still honored.
- pm2 processes are renamed to `xylocopa-*`; the upgrade script removes the old entries.
- `.mcp.json` entries rename on first agent start; legacy `ah-<id>` tmux sessions are still recognized.
- `~/.agenthive/uploads` auto-renames to `~/.xylocopa/uploads`; `localStorage` keys migrate on first page load.
- Managed repos whose local git identity was set to `AgentHive <agenthive@localhost>` by an older orchestrator are rewritten to `Xylocopa` on startup, so future agent commits are authored as Xylocopa. Only that exact legacy identity is touched; a user-set `user.name`/`user.email` is left alone. Existing commit history is not rewritten.
- Re-add the Home Screen icon if you want the new name (Safari Share → Add to Home Screen / Chrome → Install app).

To rename an old install dir: `mv ~/agenthive-main ~/xylocopa-main && cd ~/xylocopa-main && ./run.sh restart`.
