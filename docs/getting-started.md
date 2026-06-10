# Getting Started with Xylocopa

> A beginner's walkthrough, by the end, you'll have captured your first task, organized it into a project, and watched an agent execute it.
>
> 中文版：[getting-started-zh.md](getting-started-zh.md)

This guide is for people who just installed Xylocopa (see the [host setup](../README.md#getting-started) in README if you haven't) and want to understand what to actually do with it. It does **not** repeat the feature list, for that, read the README. It answers the three questions every new user hits:

1. **What are all those buttons in the task-input panel?**
2. **What's the difference between Inbox, Project, Task, Agent, and Session, and how do they fit together?**
3. **What's the minimum workflow I need to know to be productive?**

---

## The Loop at a glance

Xylocopa is an AI-native take on [GTD](https://gettingthingsdone.com/what-is-gtd/). The idea of GTD is simple: get ideas out of your head, decide what to do with them later, and act on them when the time is right. Xylocopa keeps that loop, but the "actor" is an AI agent, not you.

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

- You **capture** an idea into the **Inbox**: fast, no friction, no thinking.
- Later (or immediately), you assign the task to a **Project**, then **dispatch** it. That spawns an **Agent** (a Claude Code session) which works on it.
- When the agent's done, or gets stuck, you **review** the **Session**, mark it done, or iterate. Durable lessons get saved into `PROGRESS.md`.

That's the whole loop. The rest of this doc just fills in the details.

---

## Core concepts

### Task
A **task** is a single unit of work you want done, "add a contact form", "fix the mobile footer", "pay the electricity bill". A task has a title, an optional description, optionally a project, and some knobs (model, thinking effort, worktree, Auto mode).

Tasks start their life in the **inbox** and leave it when you dispatch them.

### Inbox
The inbox is the **one shared queue across every project**. You capture into it, and process from it. Keep it short.

<p align="center"><img src="getting-started/02-inbox.png" alt="Inbox with several queued tasks" width="320"></p>

Why a single inbox across all projects? Because most of the time, when an idea hits you, you don't want to stop and ask "which project does this belong to?" You want to dump it and move on. The inbox lets you do that. Sorting comes later.

### Project
A **project** is a bucket for related work. Usually it's backed by a git repo on disk, either a repo you already have, or one Xylocopa clones from a GitHub URL. Agents dispatched under a project run inside that project's working tree.

<p align="center"><img src="getting-started/03-projects.png" alt="Projects list" width="360"></p>

**Don't want to bother with multiple projects?** Totally fine. Create one catch-all project, the author personally uses a `random-things` project for anything that doesn't deserve its own repo (bills, shopping research, reading notes, a one-off script). All tasks live inside it, and you're done. You can always split things out later.

<p align="center"><img src="getting-started/04-new-project.png" alt="New project form" width="320"></p>

To create one, long-press the `+` button in the bottom nav → **New Project**. Only the **Name** is required. If you supply a **Git URL**, Xylocopa clones it; if you leave it blank, you get an empty folder under `~/xylocopa-projects/<name>/`.

### Agent
An **agent** is a running Claude Code session that Xylocopa manages. When you dispatch a task, an agent is spawned inside the project's directory (or an isolated [git worktree](https://git-scm.com/docs/git-worktree): see the Worktree toggle), runs the task, and waits for you to review.

Each agent lives in its own tmux session named `xy-<short-id>`, you can attach from any terminal and keep working. See [Dual-directional CLI sync](../README.md#3-monitor) in the README.

### Session
Every conversation with an agent is persisted as a **session** (both the JSONL Claude writes on disk, and Xylocopa's own per-message cache). Sessions never expire unless you delete them. You can resume any session, yesterday's, last month's, and the agent picks up with its full context.

---

## Your first five minutes

### 1. Capture a task

Tap the `+` button in the bottom nav. The **New Task** sheet slides up.

<p align="center"><img src="getting-started/06-new-task-dispatch-ready.png" alt="New Task sheet" width="320"></p>

Fill in what you want done:

- **Title** is optional, if blank, Xylocopa derives one from your description.
- **Project**: pick one from the dropdown, or leave blank to save into the inbox as "unassigned" (triage later). On a fresh install you have no projects yet, leave it blank for now.
- **Describe what needs to be done**: free-form prompt. This is what the agent sees.
- **Model**: Opus / Sonnet / Haiku. Opus is the default; pick cheaper for simple tasks.
- **Effort**: L / M / xH / Max. Higher = more thinking, slower, more expensive.
- **Worktree** toggle, if on, the agent works in an isolated git worktree so it won't step on anything else you (or other agents) have open.
- **Auto** toggle, see [Auto mode + safety](#auto-mode--safety) below.

Then pick how to leave the sheet. The six buttons on the input bar (left → right):

<p align="center"><img src="getting-started/01-input-bar-annotated-en.png" alt="Input-bar buttons, annotated" width="520"></p>

| Icon | Name | What it does |
|---|---|---|
| `+` | Attach files | Attach images, PDFs, text files, passed to the agent as context. |
| 🎙️ | Voice input | Dictate. Transcribed with OpenAI Whisper (needs `OPENAI_API_KEY`). Great on phone. |
| 📅 | Set reminder | Schedule a push notification for this task, e.g. remind me Friday at 9am. The task stays in the inbox. |
| ✈️ | **Launch agent** | Create the task **and** immediately dispatch it, you land in the agent's chat. _Only appears when a project is selected._ |
| 📥 | **Save to inbox & close** | Park it in the inbox, close the sheet. Default for "I'll deal with this later." |
| ⚡ | **Quick save to inbox** | Park it in the inbox, **keep the sheet open** so you can type the next idea immediately. Use this when you have five thoughts to dump in a row. |

The three **colored** buttons on the right (✈️ Launch, 📥 Save, ⚡ Quick) are the three ways to leave this sheet. Pick by intent:

- You're capturing an idea on the go → **⚡ Quick save** (stay here for the next one) or **📥 Save** (one and done).
- You want the agent to start **right now** → **✈️ Launch agent** (requires a project, see step 2).

### 2. Create a project (when you're ready to dispatch)

You can leave tasks in the inbox as long as you want. But to dispatch a task to an agent, it needs a project to live in, the agent works inside that project's directory.

Tap and hold the `+` button in the bottom nav. The **Create** menu opens with three options:

<p align="center"><img src="getting-started/08-create-menu.png" alt="Create menu, New Agent / New Project / New Task" width="320"></p>

Pick **New Project**, give it a name (lowercase letters, numbers, hyphens, underscores, dots), optionally paste a Git URL, and hit **Create Project**.

> **Shortcut**: if you just want a dumping ground, name it `random-things` or `misc` and be done. You can always add more projects later.

Once a project exists, open any inbox task, assign the project, and hit **Dispatch**.

### 3. Watch it run

If you dispatched, you're now in the agent's chat view.

<p align="center"><img src="getting-started/12-chat-header-annotated-en.png" alt="Chat header — id pill, worktree pill, Task chip, branch, Stop button" width="640"></p>

The header is two rows:

- **Row 1** — status dot next to the title; on the right, tinted action pills (red **Stop** / cyan **Resume** / green **OK**) and an icon toolbar (refresh, files, notifications, schedule, star).
- **Row 2** — a single tag strip: project • **worktree pill** (icon only — hover or long-press for a `worktree: <name>` popover, double-click copies) • **Auto** chip (when Auto mode is on) • **Task chip** (orange, opens the task card) • **ID pill** (4-char monospace — same hover/long-press = popover, double-click = copy).

From here you can:

- Read the agent's thinking and tool calls as they stream in.
- Approve or deny tool calls (when Auto mode is off).
- Send follow-ups, course-correct, or stop the agent.
- **Double-tap a message** to bookmark it (see [next section](#4-save-the-moments-worth-remembering-message-level-bookmarks)).

On desktop, tap the split-screen button (bottom-right corner) to watch 2–4 agents at once:

<p align="center"><img src="getting-started/09-desktop-inbox.png" alt="Desktop inbox view" width="640"></p>

### 4. Save the moments worth remembering (message-level Bookmarks)

A long agent run can be hundreds of turns. The one exchange you'll actually want again next month — the file path, the decision, the working command, the surprising result — is buried somewhere in the middle. **Bookmarks** are how you keep it.

> **Two granularities, two buttons.** ⭐ **Starred** (top-right of the chat header) pins the **whole session** to the top of your project. 📑 **Bookmarks** (this section) pins **one specific message** inside a session. Use Starred for "this session is important", Bookmarks for "this _turn_ is the one I'll come back to."

**Save a turn:** double-tap any chat bubble → tap **Bookmark** in the action menu. An inline note prompt appears — type a one-liner, or skip it and let `gpt-4o-mini` generate a short title + emoji from the message and its ±2 neighboring turns (media references included).

**Find them again:** every project has a 📑 **Bookmarks** tab on its detail page, sitting next to ⭐ **Starred**. Each row shows the AI title, a one-line preview of the original message, the bookmark's age, and a pencil to edit your note. Tap a row and you jump back into the original chat with a yellow **focus-flash** on the bookmarked bubble.

<p align="center"><img src="getting-started/13-bookmarks-list.png" alt="Project Bookmarks list — message-level saves with AI title + emoji" width="360"></p>

When to reach for it:
- The agent just printed the exact command / file path / function signature you'll need later.
- A long debugging conversation produced **one** turn that finally explained the root cause.
- You want a project-level "best-of" list of useful exchanges, not just a flat session archive.

Empty note saves the AI summary as the title; ⌘/Ctrl+Enter or blur saves your edit, Esc cancels, saving an empty string reverts back to the AI summary.

---

## Processing the inbox

When you've captured faster than you've dispatched, the inbox piles up. There are three ways to drain it:

1. **Tap a task** → edit it, pick a project, hit **Dispatch** on the detail page.
2. **Drag the handle** (`≡` on the left of each card) to reorder, the top of the list is "do first".
3. **AI batch process** (the `AI` button in the top-right of the inbox), one tap, and an AI triage agent reads every task, refines the prompt, and assigns a project. You review the result before it actually dispatches. Great for "I captured 15 things this week, now deal with them."

**Defer, hide a task until later.** When you expand a card, the toolbar has a 🌙 **defer** button. Pick a date and the task disappears from the main list, moving into a collapsed **Deferred (N)** section at the bottom. It reappears on the scheduled date. Use this for "I can't act on this yet but I don't want it cluttering the view."

<p align="center"><img src="getting-started/10-inbox-defer-annotated-en.png" alt="Defer button and Deferred section" width="360"></p>

See the [project detail view](getting-started/07-project-detail.png) for per-project task lists and stats.

---

## Launching an agent from inside a project

Every project detail page has a **New Agent** card at the top. It's the fastest way to fire off work when you already know which project it belongs to, you skip the inbox entirely.

<p align="center"><img src="getting-started/11-new-agent-annotated-en.png" alt="New Agent card inside a project" width="520"></p>

It's similar to the New Task sheet, with two differences worth calling out:

- **Schedule** (🕐, amber), fire the agent at a future time instead of immediately. Pick a date/time and the agent will auto-launch when the clock hits it. Useful for "kick off the nightly refactor at 2am when I'm not using the machine."
- **Task toggle** (cyan when on), decides whether this run is tracked as a **Task** record. Off = an ephemeral agent session with no task attached. On = a Task is created and linked, so it shows up in the inbox/task lists with a summary afterwards. Leave it off for quick throwaway questions ("explain this function"); turn it on for anything you might want to come back to or retry.

Model, Effort, Worktree, and Auto work exactly like in the New Task sheet (see [section 1](#1-capture-a-task)).

---

## Auto mode + safety

When you toggle **Auto** (the orange switch), Xylocopa launches the agent with `claude --dangerously-skip-permissions`. That means the agent **won't stop to ask permission** for each tool call, it just runs.

That sounds scary. It isn't, because Xylocopa installs a [deterministic safety hook](../README.md#safety-guardrails) that hard-blocks destructive commands **regardless** of Auto mode:

- `rm -rf` / `rm -rf /`
- `git push --force` / `git reset --hard` outside worktrees
- `git clean -f`, `git checkout -- .`, `git restore .`
- `DROP TABLE`, `TRUNCATE`
- `Write` / `Edit` to paths outside the project directory

Turn Auto on for low-risk tasks (documentation, UI tweaks, isolated refactors in a worktree). Keep it off if you want a human-in-the-loop for every tool call.

---

## When the agent misses the mark

Xylocopa assumes the agent will sometimes get it wrong. The recovery path is **Try → Summarize → Retry**:

1. Stop the agent.
2. In the task detail, tap **Summarize**: Xylocopa reads the full session and writes a concise "what was tried, what didn't work, what to try next."
3. Edit that summary, add your feedback, and tap **Redo**. A fresh agent picks up with the summary in context, so it doesn't repeat the same mistakes.

Durable lessons (not session-specific) get saved to `PROGRESS.md` in the project, which is automatically retrieved by future agents via RAG. Your project gets smarter over time instead of resetting.

---

## Common scenarios

**"I just had an idea on the subway."**
Open the PWA, tap `+`, speak it into the mic, hit ⚡ (Quick save). Done in five seconds. Triage later.

**"I have 10 things stacked up and I want to deal with them in one go."**
Open the inbox, tap the **AI** button top-right. The triage agent refines and assigns each one. Review, then bulk dispatch.

**"I don't like organizing, I just want one place to put everything."**
Create a single project called `random-things` (or `misc`, or your first name). Put every task there. You lose per-project RAG, but you keep everything else.

**"Agent started going in circles."**
Stop it. Summarize the session. Add a one-line correction. Retry. Don't let a bad trajectory run to the token limit.

**"I want to monitor three agents at once."**
On desktop: tap the split-screen button (bottom-right of any page), pick 2/3/4 panes. Each pane navigates independently. On mobile: use the **Attention button** (draggable FAB), it turns cyan when any agent has new messages, tap to jump to the oldest unread.

---

## Where to go next

- [README · The Loop](../README.md#the-loop): the full feature tour
- [Workflow](workflow.md): a worked example of one day's tasks, capture through retry
- [README · Durable by Default](../README.md#durable-by-default): how your work survives crashes
- [ARCHITECTURE.md](ARCHITECTURE.md): system design
- [install-cert.md](install-cert.md): trust the self-signed HTTPS cert on your client devices
