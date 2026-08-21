# Troubleshooting

- **Conversation not updating** — use the refresh button at the top of the chat to re-sync from the CLI.
- **Agent shows IDLE after a server restart but is still working** — expected; status returns to EXECUTING on its next tool call.
- **tmux naming** — don't name your own sessions with the `xy-` or `ah-` prefix; those are managed by Xylocopa.
- **PWA stuck on the loading screen** — stale Service Worker cache. Run `.venv/bin/python tools/push_reset.py` on the host, then fully close and reopen the PWA.
- **Browser certificate warnings on other devices** — expected with the self-signed certificate until you install the CA cert; per-platform steps in [install-cert.md](install-cert.md).

Server logs live in `logs/server.log` and `logs/orchestrator.log` in the install directory.
