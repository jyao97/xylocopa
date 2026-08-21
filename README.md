# Xylocopa

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)

> [**The Loop**](#the-loop) · [**Getting Started**](#getting-started) · [**Features**](#features) · [**What's New**](CHANGELOG.md) · [**Architecture**](docs/ARCHITECTURE.md) · [**Contributing**](CONTRIBUTING.md) · [**新手入门（中文）**](docs/getting-started-zh.md)

<p align="center"><img src="docs/hero.png" alt="Xylocopa — Many projects. One attention." width="640"></p>

**Xylocopa is a visual orchestrator for your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents — it keeps you focused while they do the work.**

Capture a task from anywhere — your phone included — and hand it to an agent. Agents run in parallel across your projects, each in its own git worktree; one attention button always takes you to the agent that needs you; what's learned lands in per-project memory instead of dying with the session. The workflow follows [GTD](https://gettingthingsdone.com/what-is-gtd/), with agents as the executor.

*Named after [Xylocopa caerulea](https://en.wikipedia.org/wiki/Xylocopa_caerulea), the blue carpenter bee — xylocopa (zy-LOCK-uh-puh) is Greek for "wood-cutter". If you find it useful, a star helps others find it.*

| <img src="docs/pwa/inbox.png" alt="Inbox" width="220"> | <img src="docs/pwa/projects.png" alt="Projects" width="220"> | <img src="docs/pwa/agents.png" alt="Agents" width="220"> |
|:---:|:---:|:---:|
| Inbox | Projects | Agents |
| <img src="docs/pwa/chat-insights.png" alt="Chat with Progress Insights" width="220"> | <img src="docs/webapp-preview.png" alt="Interactive artifact — agent-built 3D exoplanet explorer running sandboxed in chat" width="220"> | <img src="docs/pwa/monitor.png" alt="Monitor" width="220"> |
| Chat | Interactive artifact | Monitor |

## The Loop

Five steps, one loop — this is the whole product.

### 1. Capture

An inbox that works from anywhere: type it, speak it (Whisper voice input), or use the quick-entry form. Drafts survive closing the app.

### 2. Dispatch

One click turns a task into an agent: pick a model, optionally enable Auto mode (`--dangerously-skip-permissions`; destructive commands are still blocked by the [safety hook](#safety-guardrails)). Agents run in parallel, each in its own git worktree, seeded at dispatch with lessons from past sessions in the same project. Batch dispatch triages a pile of inbox tasks in one step.

### 3. Monitor

A mobile-first PWA with split screen and an attention button that always takes you to the oldest unread agent. The chat renders markdown, inline media, LaTeX, and interactive cards for tool approvals and plan review.

A deliverable doesn't have to be a diff. Agents present runnable work — a web app they built, or a proxied localhost service like TensorBoard — as tappable cards that open fullscreen in a sandboxed iframe. The interactive-artifact screenshot up top is an agent-built 3D explorer of 6,271 planets from the NASA Exoplanet Archive, running in chat on an iPhone.

Every agent runs in a tmux session you can attach to from a terminal, and CLI sessions show up in the web app — sync is two-way.

![CLI sync demo](docs/cli-sync.gif)

Push notifications fire when you're away and stay quiet while you're watching. For external conditions, an agent can register a one-shot webhook ([probe](#agent-control-plane)) and be woken when CI finishes, a GPU frees up, or a build completes.

<img src="docs/probe-fired.png" alt="Probe fired" width="640">

### 4. Review

Approve and close, or retry: stopping a missed attempt auto-summarizes what was tried, and the next agent starts from that summary instead of from zero. Diffs, commit history, and branch status are available per project, with one-click cleanup and push.

### 5. Remember

Lessons accumulate in a per-project `PROGRESS.md` that you can edit in the UI; relevant entries are retrieved for future agents. Every conversation is persisted, searchable, and resumable.

## Why Xylocopa?

Plain `claude` works for one-off sessions. It frays once several run in parallel, across projects, over days: you lose track of which agent needs input, old sessions are hard to find, and every retry starts from scratch.

Xylocopa is the task, attention, and memory layer around the same CLI. It launches the `claude` you already use inside tmux on your machine, so your CLAUDE.md files, project setup, and credentials carry over. The only new dependencies are tmux and, for remote access, a VPN such as Tailscale. And it's a server plus PWA rather than a desktop app: install it once on the machine where your code lives, then drive it from any browser — including the one in your pocket.

The design assumes agents miss. Stopping a bad attempt produces a summary; the next attempt picks it up; durable lessons land in per-project memory rather than dying with the session.

## Features

| Category | What you get |
|---|---|
| **Task management** | Inbox with voice input, quick capture, drag-to-reorder, draft persistence; per-project organization; retry with auto-summarization. |
| **Agent control** | Start/stop/resume; per-agent model, timeout, and permission mode; batch dispatch; lesson retrieval at dispatch. Agents read each other's sessions over MCP at ~54× fewer tokens than raw JSONL. |
| **Chat** | Markdown, inline media, LaTeX; plan-review and tool-approval cards; per-agent context-usage and lifetime-cost breakdowns. Double-tap any bubble to bookmark, modify, or fork the conversation from that point ([gestures](docs/gestures.md)). |
| **Interactive artifacts** | Agents present runnable deliverables as cards (`webapp_present`): static apps served sandboxed, localhost services reverse-proxied (HTTP + WebSocket), with an injected console feeding a debug drawer. |
| **Monitoring** | Split screen (up to 4 panes), real-time WebSocket updates, system monitor (disk, memory, GPU, tokens), weekly progress stats. |
| **Notifications** | Hook-based Web Push: fires when you're away, quiet while you're watching; permission requests always cut through. |
| **Mobile PWA** | Home Screen install on iOS/Android with push and voice; e-ink display mode for e-paper readers. |
| **CLI sync** | Two-way: CLI sessions appear in the web app; web sessions resume from a terminal via `tmux attach -t xy-<id>`. |
| **Git** | Per-project diffs, commit history, branch status; agents work in isolated worktrees; one-click cleanup and push. |
| **History** | Every conversation persisted and full-text searchable; star sessions, bookmark messages, resume any agent. |
| **Security** | Password auth with rate limiting, inactivity lock, HTTPS. |
| <a id="safety-guardrails"></a>**Safety guardrails** | A deterministic `PreToolUse` hook hard-blocks destructive operations — `rm -rf`, force-pushes, `git reset --hard` outside worktrees, `DROP TABLE`, writes outside the project directory — even under `--dangerously-skip-permissions`. |
| **Reliability** | Built to survive restarts and kills: incremental [session cache](orchestrator/session_cache.py), tmux-anchored agent recovery, scheduled [backups](orchestrator/backup.py), [orphan cleanup](orchestrator/orphan_cleanup.py), unlimited session retention. |

## Getting Started

### Prerequisites

- Linux or macOS host (Ubuntu 22.04+ / macOS 13+ recommended)
- Node.js 18+, Python 3.11+, tmux
- Claude Code CLI: `npm install -g @anthropic-ai/claude-code`, then run `claude` once to log in (on a headless server, `claude setup-token`). Xylocopa reuses the credentials in `~/.claude/` — a Claude Pro/Max subscription is enough, no separate API billing.
- OpenAI API key (optional, for voice input)

Claude Code's own support for Amazon Bedrock, Google Vertex AI, and gateways like LiteLLM carries over: set the usual environment variables and Xylocopa's `claude` subprocesses inherit them (see [unsloth.ai/docs/basics/claude-code](https://unsloth.ai/docs/basics/claude-code) for a LiteLLM walkthrough).

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/jyao97/xylocopa/master/setup.sh | bash
```

The installer clones into `~/xylocopa-main`, prompts for your projects directory, default model, optional OpenAI key, and ports, then writes `.env`, generates SSL certs, installs dependencies, and starts the services. Every setting lives in `.env`; [`.env.example`](.env.example) is the annotated reference. To clone manually instead:

```bash
git clone https://github.com/jyao97/xylocopa.git ~/xylocopa-main
cd ~/xylocopa-main
./setup.sh
./run.sh start
```

Open `https://<machine-ip>:3000` and set a password on first visit (`hostname -I` on Linux or `ipconfig getifaddr en0` on macOS gives the LAN IP).

A tip: symlink the Xylocopa repo itself into `~/xylocopa-projects/` and agents can improve the tool while you use it.

### Auto-start on reboot

`./run.sh startup` runs `pm2 save` + `pm2 startup` (systemd on Linux, launchd on macOS; on Linux, copy and run the printed `sudo` line). This also moves pm2 out of your terminal's cgroup, so closing the terminal doesn't take the services down. Disable later with `pm2 unstartup`.

### Add projects

Long-press the **+** button → **New Project**: paste a GitHub URL or point at a folder. Folders in the projects directory (`~/xylocopa-projects/` by default, `HOST_PROJECTS_DIR` in `.env`) are also picked up.

### Remote access

Any VPN or tunnel works — Tailscale, ZeroTier, WireGuard, frp, Cloudflare Tunnel. With Tailscale: install it on the server and your phone, `tailscale up` on both, then open `https://<tailscale-ip>:3000`. No ports exposed to the internet.

### iPhone PWA

Open `https://<machine-ip>:3000` in Safari (Advanced → Visit Website past the certificate warning, then refresh) and follow the on-screen guide to install the CA certificate and add the app to the Home Screen. Xylocopa uses a self-signed certificate, so other devices warn until the cert is installed; for Android, macOS, Windows, and Linux see [docs/install-cert.md](docs/install-cert.md).

### Your data, and leaving

Tasks and metadata live in `data/orchestrator.db` in the install directory; agent sessions are Claude Code's native JSONL under `~/.claude/projects/`, not duplicated; per-project lessons live in each repo's `PROGRESS.md`. A full snapshot is the install directory plus `~/.claude/projects/`. Uninstalling is one `pm2 delete` and a few `rm -rf`s, and touches none of your project code — exact steps in [docs/uninstall.md](docs/uninstall.md).

## Agent control plane

Agents can call back into the orchestrator through a built-in MCP server: list and create tasks, dispatch work, read other agents' sessions, scaffold projects, present web apps, check health. The surface is restricted to non-destructive verbs.

Probes are the event-driven part: an agent registers a one-shot webhook via `probe_create` ("wake me when CI on PR #42 goes green") and hands the URL to whatever monitors the condition. POSTing it injects a message into the chat and burns the token.

The full tool list and safety model are in [docs/agent-mcp-tools.md](docs/agent-mcp-tools.md).

## Telemetry

One anonymous event per day: random install id, version, platform, timestamp — nothing user-generated, no IPs, no prompts, no paths. Sent by [`telemetry.py`](orchestrator/telemetry.py) to a [Cloudflare Worker](https://github.com/jyao97/xylocopa-telemetry) owned by the author; no third-party analytics. Disable with the toggle in **Monitor → Help improve Xylocopa**, `XYLOCOPA_TELEMETRY=0`, or `telemetry: false` in `~/.xylocopa/config.yaml`.

## More

- [Getting started guide](docs/getting-started.md) — first project, first dispatch, first review ([中文](docs/getting-started-zh.md))
- [Gestures](docs/gestures.md) — the touch shortcuts behind the UI
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Agent MCP tools](docs/agent-mcp-tools.md)
- [Data locations & uninstall](docs/uninstall.md)
- [Migrating from AgentHive](docs/migrating-from-agenthive.md) — Xylocopa's former name

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports, development setup, and pull requests.

## License

Apache 2.0 — see [LICENSE](LICENSE).
