# A Day with Xylocopa

Five things popped into my head between coffee and standup. Here's what I did with them.

## 1. Dump everything into the inbox

Hit **+** for each one, type, hit **Save** — no project, no priorities yet:

- a contact form for the home page
- the 404 page bug on mobile
- the blog post draft about Bun
- the domain renewal coming up next Friday
- the electricity bill

<p align="center"><img src="getting-started/02-inbox.png" alt="Inbox with several queued tasks" width="320"></p>

## 2. Defer the one I can't act on yet

The domain renewal isn't due until next Friday. Tap to expand the card, tap the moon icon, pick a date. It disappears into a **Deferred** section at the bottom, hidden until its date arrives.

<p align="center"><img src="getting-started/10-inbox-defer-annotated-en.png" alt="Defer button on an expanded inbox card" width="360"></p>

## 3. Dispatch the most concrete one

The contact form is well-specified, start there. Tap into the task, set model and effort, tap **Launch**. An agent spawns in a fresh git worktree, runs the work, streams its output to the chat view.

<p align="center"><img src="getting-started/06-new-task-dispatch-ready.png" alt="New Task sheet ready to dispatch" width="320"></p>

## 4. Walk away

The agent is on its own; push notifications fire when it finishes or asks for input. Split-screen on desktop shows several agents at once.

<p align="center"><img src="getting-started/09-desktop-inbox.png" alt="Desktop inbox view" width="640"></p>

## 5. Retry when it misses the mark

Form looks right, but the email validation only runs client-side. Stop the agent, hit **Summarize**, add "validate server-side too" to the summary, hit **Retry**. The next agent picks up with the summary in context, it doesn't need to re-discover what was tried.

## 6. End of day

Two tasks done, one in progress, one deferred until next Friday, one still in the inbox. Each completed task left a resumable session; the project carries forward what was learned, so the next form-validation task starts with today's lesson; the deferred renewal will surface on its date.

## See also

- [Getting Started](getting-started.md): full beginner walkthrough with all the buttons explained.
- [README · The Loop](../README.md#the-loop): the Capture → Dispatch → Monitor → Review → Remember framework.
