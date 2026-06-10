# Getting Started with Xylocopa

> A beginner's walkthrough: capture a task, organize it into a project, and watch an agent execute it.
>
> 中文版：[getting-started-zh.md](getting-started-zh.md)

This guide is for people who just installed Xylocopa ([host setup](../README.md#getting-started)) and want to know what to do next. It doesn't repeat the README's feature list; it answers the three questions new users actually hit:

1. What are the buttons in the task-input panel?
2. What do Inbox, Project, Task, Agent, and Session mean, and how do they fit together?
3. What's the minimum workflow to be productive?

---

## The loop at a glance

Xylocopa follows [GTD](https://gettingthingsdone.com/what-is-gtd/): get ideas out of your head, decide what to do with them later, act when the time is right. The difference is that the acting is done by an agent.

```
         ┌─────────────────────────────────────────────────────────┐
         │                                                         │
   idea  │    Inbox  ──▶  Project  ──▶  Task  ──▶  Agent  ──▶  Session
  ──────▶│    (capture)   (bucket)      (plan)     (execute)   (review & remember)
         │                                                         │
         └─────────────────────────────────────────────────────────┘
                                                                   │
                                                         lessons ──┘
                                                         roll back into PROGRESS.md
```

You capture an idea into the inbox. Later (or immediately) you assign it to a project and dispatch it, which spawns an agent — a Claude Code session — to work on it. When the agent finishes or gets stuck, you review the session and either close the task or iterate. Lessons worth keeping go into the project's `PROGRESS.md`.

---

## Core concepts

### Task
A single unit of work: "add a contact form", "fix the mobile footer", "pay the electricity bill". A task has a title, an optional description, optionally a project, and some knobs (model, thinking effort, worktree, Auto mode). Tasks start in the inbox and leave it when dispatched.

### Inbox
One shared queue across every project: capture into it, process from it.

<p align="center"><img src="getting-started/02-inbox.png" alt="Inbox with several queued tasks" width="320"></p>

The inbox is shared so that capturing never requires deciding which project an idea belongs to. Sorting comes later.

### Project
A bucket for related work, usually backed by a git repo — one you already have, or one Xylocopa clones from a GitHub URL. Agents dispatched under a project run inside that project's working tree.

<p align="center"><img src="getting-started/03-projects.png" alt="Projects list" width="360"></p>

One catch-all project is fine. The author keeps a `random-things` project for everything that doesn't deserve its own repo — bills, shopping research, one-off scripts. You can split things out later.

<p align="center"><img src="getting-started/04-new-project.png" alt="New project form" width="320"></p>

To create one: long-press the `+` button in the bottom nav → **New Project**. Only the name is required. With a Git URL, Xylocopa clones it; without one, you get an empty folder under `~/xylocopa-projects/<name>/`.

### Agent
A running Claude Code session managed by Xylocopa. Dispatching a task spawns an agent inside the project's directory (or an isolated [git worktree](https://git-scm.com/docs/git-worktree) if the Worktree toggle is on), which runs the task and waits for review.

Each agent lives in a tmux session named `xy-<short-id>`; you can attach from any terminal and keep working there — sync with the web app is two-way.

### Session
Every conversation is persisted as a session: the JSONL Claude writes on disk plus Xylocopa's per-message cache. Sessions don't expire unless you delete them, and any session can be resumed later with its full context.

---

## Your first five minutes

### 1. Capture a task

Tap the `+` button in the bottom nav to open the **New Task** sheet.

<p align="center"><img src="getting-started/06-new-task-dispatch-ready.png" alt="New Task sheet" width="320"></p>

- **Title** — optional; derived from the description if blank.
- **Project** — pick one, or leave blank to triage later. On a fresh install, leave it blank.
- **Describe what needs to be done** — the prompt the agent sees.
- **Model** — Opus / Sonnet / Haiku. Opus is the default; pick cheaper for simple tasks.
- **Effort** — L / M / xH / Max. Higher means more thinking, slower, more expensive.
- **Worktree** — run the agent in an isolated git worktree so it won't collide with anything else you have open.
- **Auto** — see [Auto mode and safety](#auto-mode-and-safety).

The six buttons on the input bar, left to right:

<p align="center"><img src="getting-started/01-input-bar-annotated-en.png" alt="Input-bar buttons, annotated" width="520"></p>

| Icon | Name | What it does |
|---|---|---|
| `+` | Attach files | Images, PDFs, text files — passed to the agent as context. |
| 🎙️ | Voice input | Dictation via OpenAI Whisper (needs `OPENAI_API_KEY`). |
| 📅 | Set reminder | Schedule a push notification for this task; it stays in the inbox. |
| ✈️ | Launch agent | Create the task and dispatch it immediately. Only shown when a project is selected. |
| 📥 | Save to inbox | Park it and close the sheet. |
| ⚡ | Quick save | Park it and keep the sheet open for the next idea. |

The three colored buttons are the three ways to leave the sheet: ⚡ or 📥 when you're capturing, ✈️ when you want the agent to start now.

### 2. Create a project

To dispatch, a task needs a project — the agent works inside that project's directory.

Long-press the `+` button; the **Create** menu has three options:

<p align="center"><img src="getting-started/08-create-menu.png" alt="Create menu, New Agent / New Project / New Task" width="320"></p>

Pick **New Project**, name it (lowercase letters, numbers, hyphens, underscores, dots), optionally paste a Git URL, and hit **Create Project**. A catch-all `random-things` or `misc` is a fine first project.

Once a project exists, open any inbox task, assign the project, and hit **Dispatch**.

### 3. Watch it run

After dispatch you land in the agent's chat.

<p align="center"><img src="getting-started/12-chat-header-annotated-en.png" alt="Chat header — id pill, worktree pill, Task chip, branch, Stop button" width="640"></p>

The header has a status dot and action pills (Stop / Resume / OK) on the first row, and a tag strip on the second: project, worktree pill, Auto chip, task chip, and a 4-character id pill (long-press to copy).

From here you can read the agent's output as it streams, approve or deny tool calls (when Auto is off), send follow-ups, or stop the agent. On desktop, the split-screen button (bottom-right) shows 2–4 agents side by side:

<p align="center"><img src="getting-started/09-desktop-inbox.png" alt="Desktop inbox view" width="640"></p>

### 4. Bookmark the turns worth keeping

A long run can be hundreds of turns; the one you'll want next month — a file path, a decision, a working command — is buried in the middle. Double-tap any chat bubble → **Bookmark**. Type a one-line note, or skip it and a generated title + emoji is used instead.

Two granularities: ⭐ **Starred** (chat header) pins the whole session to the top of the project; 📑 **Bookmarks** pins one message.

Each project has a **Bookmarks** tab next to **Starred**. Rows show the title, a one-line preview, age, and a pencil to edit the note; tapping a row jumps back to the original message with a brief highlight.

<p align="center"><img src="getting-started/13-bookmarks-list.png" alt="Project Bookmarks list" width="360"></p>

---

## Processing the inbox

Three ways to drain a backlog:

1. Tap a task → edit, pick a project, **Dispatch**.
2. Drag the `≡` handle to reorder; the top of the list is "do first".
3. **AI batch process** (the `AI` button, top-right): a triage agent reads every task, refines the prompt, and assigns a project. You review before anything dispatches.

To hide a task until later, expand its card and use the 🌙 **defer** button: pick a date and it moves into a collapsed **Deferred** section until then.

<p align="center"><img src="getting-started/10-inbox-defer-annotated-en.png" alt="Defer button and Deferred section" width="360"></p>

See the [project detail view](getting-started/07-project-detail.png) for per-project task lists and stats.

---

## Launching an agent from inside a project

Every project detail page has a **New Agent** card at the top — the fastest path when you already know where the work belongs, skipping the inbox.

<p align="center"><img src="getting-started/11-new-agent-annotated-en.png" alt="New Agent card inside a project" width="520"></p>

Two differences from the New Task sheet:

- **Schedule** (🕐) — launch at a future time instead of immediately, e.g. a refactor at 2am.
- **Task toggle** — whether this run is tracked as a Task record. Off: an ephemeral session, good for quick questions. On: a linked Task that shows up in task lists with a summary afterwards, good for anything you might retry or revisit.

Model, Effort, Worktree, and Auto work as in [the New Task sheet](#1-capture-a-task).

---

## Auto mode and safety

The **Auto** toggle launches the agent with `claude --dangerously-skip-permissions`: it doesn't pause to ask before each tool call. A deterministic [safety hook](../README.md#safety-guardrails) still blocks destructive commands regardless of Auto mode:

- `rm -rf`
- `git push --force`, `git reset --hard` outside worktrees
- `git clean -f`, `git checkout -- .`, `git restore .`
- `DROP TABLE`, `TRUNCATE`
- `Write` / `Edit` to paths outside the project directory

Use Auto for low-risk tasks (documentation, UI tweaks, isolated refactors in a worktree); leave it off when you want to approve every tool call.

---

## When the agent misses

The recovery path is Try → Summarize → Retry:

1. Stop the agent.
2. In the task detail, tap **Summarize** — Xylocopa reads the session and writes up what was tried, what didn't work, and what to try next.
3. Edit the summary, add your feedback, tap **Redo**. A fresh agent starts with that summary in context.

Lessons that aren't session-specific go to the project's `PROGRESS.md`, which is retrieved for future agents automatically.

---

## Common scenarios

**An idea on the subway** — open the PWA, tap `+`, dictate it, hit ⚡. Triage later.

**Ten tasks stacked up** — open the inbox, tap **AI**. Review the triage result, then bulk dispatch.

**No interest in organizing** — one `random-things` project for everything. You lose per-project lesson retrieval, everything else works.

**Agent going in circles** — stop it, Summarize, add a one-line correction, Redo. Better than letting a bad trajectory run to the token limit.

**Watching several agents** — desktop: split-screen button, 2–4 panes. Mobile: the Attention button turns cyan when any agent has unread messages; tap to jump to the oldest.

---

## Where to go next

- [README](../README.md): feature list and install
- [Workflow](workflow.md): a worked example of one day's tasks, capture through retry
- [ARCHITECTURE.md](ARCHITECTURE.md): system design
- [install-cert.md](install-cert.md): trust the self-signed HTTPS cert on client devices
