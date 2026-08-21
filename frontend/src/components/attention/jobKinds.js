/**
 * Frontend counterpart to the backend's registries.
 *
 * The panel never branches on trigger_type or action_type directly — it
 * looks a job up here. Adding a backend capability that needs custom
 * presentation means adding one entry, not editing the panel.
 *
 * Anything not listed still renders: the fallbacks below describe a job
 * from its own fields, so a job created by a newer backend than this
 * bundle degrades to a readable row instead of a blank one.
 */

export const KIND_META = {
  reminder: { label: "Reminder", icon: "bell" },
  watch: { label: "Watch", icon: "eye" },
  digest: { label: "Digest", icon: "sparkle" },
  automation: { label: "Automation", icon: "bolt" },
};

export const kindMeta = (kind) =>
  KIND_META[kind] || { label: kind || "Job", icon: "bell" };

// ---------------------------------------------------------------------------
// Time formatting
// ---------------------------------------------------------------------------

/**
 * Backend datetimes are naive UTC (SQLite). Without an explicit marker
 * `new Date("2026-07-30T16:00:00")` is parsed as *local* time, which would
 * silently shift every displayed run time by the UTC offset — the exact
 * class of bug this feature already hit once on the backend side.
 */
export function parseUtc(iso) {
  if (!iso) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/**
 * "in 42m", "in 1h27m", "in 3h", "in 2d", "now", "5m ago"
 *
 * Sub-day values keep the minute component: rounding 87 minutes to "in 1h"
 * is misleading on the confirmation card, which is precisely where the user
 * is checking whether the assistant understood the time they asked for.
 */
export function relativeTime(iso, now = Date.now()) {
  const d = parseUtc(iso);
  if (!d) return "";
  const delta = d.getTime() - now;
  const abs = Math.abs(delta);
  if (abs < 45 * 1000) return "now";

  let value;
  if (abs < HOUR) {
    value = `${Math.round(abs / MIN)}m`;
  } else if (abs < DAY) {
    const hours = Math.floor(abs / HOUR);
    const mins = Math.round((abs % HOUR) / MIN);
    // 59.6m rounds to 60 — carry it rather than printing "1h60m".
    value = mins === 60 ? `${hours + 1}h` : mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
  } else {
    value = `${Math.round(abs / DAY)}d`;
  }

  return delta > 0 ? `in ${value}` : `${value} ago`;
}

/** "Thu 9:00 AM" for this week, "Aug 3, 9:00 AM" beyond it. */
export function absoluteTime(iso, now = Date.now()) {
  const d = parseUtc(iso);
  if (!d) return "";
  const withinWeek = Math.abs(d.getTime() - now) < 6 * DAY;
  return d.toLocaleString(undefined, withinWeek
    ? { weekday: "short", hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// ---------------------------------------------------------------------------
// Job description
// ---------------------------------------------------------------------------

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function describeInterval(seconds) {
  if (!seconds) return "repeating";
  if (seconds % 86400 === 0) {
    const d = seconds / 86400;
    return d === 1 ? "daily" : `every ${d} days`;
  }
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return h === 1 ? "hourly" : `every ${h}h`;
  }
  return `every ${Math.round(seconds / 60)}m`;
}

function describeWeekdays(days) {
  if (!Array.isArray(days) || days.length === 0 || days.length === 7) return "";
  const sorted = [...days].sort((a, b) => a - b);
  if (sorted.join() === "0,1,2,3,4") return " on weekdays";
  if (sorted.join() === "5,6") return " on weekends";
  return ` on ${sorted.map((d) => DAY_NAMES[d] ?? d).join(", ")}`;
}

/** One-line "when does this fire" text for a job row. */
export function describeTrigger(job, now = Date.now()) {
  const cfg = job?.trigger_config || {};

  if (job?.trigger_type === "at") {
    return job.next_run_at
      ? `${absoluteTime(job.next_run_at, now)} · ${relativeTime(job.next_run_at, now)}`
      : "one-off";
  }

  if (job?.trigger_type === "every") {
    if (cfg.daily_at) {
      // daily_at is LOCAL wall-clock on the backend, so it is already the
      // hour the user asked for — show it verbatim, do not convert.
      return `${cfg.daily_at}${describeWeekdays(cfg.weekdays)}`;
    }
    return describeInterval(cfg.interval_seconds);
  }

  if (job?.trigger_type === "signal") {
    return summarizeCondition(cfg.condition);
  }

  if (job?.trigger_type === "probe") return "on external trigger";

  // Unknown trigger from a newer backend — say so rather than lying.
  return job?.trigger_type ? `trigger: ${job.trigger_type}` : "";
}

/** Mirror of the backend's conditions.summarize(), for the row subtitle. */
export function summarizeCondition(node) {
  if (!node || typeof node !== "object") return "condition";
  if (Array.isArray(node.all)) return node.all.map(summarizeCondition).join(" and ");
  if (Array.isArray(node.any)) return node.any.map(summarizeCondition).join(" or ");
  if (node.not) return `not (${summarizeCondition(node.not)})`;

  const name = friendlySignal(node.signal);
  if (node.op === "changed") return `when ${name} changes`;
  if (node.op === "became") {
    // The end-of-turn watch is the common case and deserves plain words
    // rather than "when agent generating becomes false".
    if (node.signal === "agent.is_generating" && node.value === false) {
      return "when the agent finishes a turn";
    }
    return `when ${name} becomes ${friendlyValue(node.value)}`;
  }
  const op = {
    eq: "is", ne: "is not", gt: ">", gte: "≥", lt: "<", lte: "≤",
    in: "is one of", contains: "contains",
  }[node.op] || node.op;
  const value = Array.isArray(node.value) ? node.value.join(" / ") : node.value;
  return `when ${name} ${op} ${value}`;
}

const SIGNAL_LABELS = {
  "agent.status": "agent status",
  "agent.unread_count": "unread count",
  "agent.last_message_at": "agent activity",
  "agent.last_message_preview": "latest message",
  "agent.is_generating": "agent generating",
  "agent.has_pending_suggestions": "pending insights",
  "agent.context_percent": "context usage",
  "task.status": "task status",
  "task.attempt_number": "retry count",
  "agents.count": "agent count",
  "agents.unread_total": "total unread",
  "tasks.count": "task count",
};

export const friendlySignal = (name) => SIGNAL_LABELS[name] || name || "signal";

const friendlyValue = (v) =>
  v === true ? "yes" : v === false ? "no" : String(v);

/** "at most once every 5m" — the anti-spam floor, shown on the job detail. */
export function describeCooldown(seconds) {
  if (!seconds) return null;
  if (seconds < 60) return `at most once every ${seconds}s`;
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return `at most once every ${h}h`;
  }
  return `at most once every ${Math.round(seconds / 60)}m`;
}

/** What the job will do, for the row's secondary line. */
export function describeAction(job) {
  switch (job?.action_type) {
    case "notify": return "notify me";
    case "message_agent": return "message the agent";
    case "dispatch_task":
      return job.action_config?.dispatch ? "dispatch a task" : "file a task";
    case "run_prompt": return "summarize and notify";
    default: return job?.action_type || "";
  }
}

// ---------------------------------------------------------------------------
// Quick presets — the zero-typing path
// ---------------------------------------------------------------------------

/**
 * Presets build a spec directly, skipping the LLM entirely. They exist so
 * the common cases ("remind me in an hour") cost nothing, stay instant, and
 * keep working when the model is unavailable.
 */
export const PRESETS = [
  { label: "In 15m", minutes: 15 },
  { label: "In 1h", minutes: 60 },
  { label: "In 3h", minutes: 180 },
  { label: "Tomorrow 9am", dailyAt: "09:00" },
];

export function presetSpec(preset, text) {
  const title = (text || "").trim().slice(0, 300) || "Reminder";
  if (preset.dailyAt) {
    return {
      kind: "reminder",
      title,
      source_text: text || null,
      trigger_type: "every",
      trigger_config: { daily_at: preset.dailyAt },
      action_type: "notify",
      action_config: { title, body: text || title },
      // Fire once, then retire — "tomorrow 9am" is a one-off expressed
      // through the recurring trigger, so cap it explicitly.
      max_fires: 1,
    };
  }
  const at = new Date(Date.now() + preset.minutes * 60_000);
  return {
    kind: "reminder",
    title,
    source_text: text || null,
    trigger_type: "at",
    trigger_config: { at: at.toISOString() },
    action_type: "notify",
    action_config: { title, body: text || title },
  };
}

export const STATUS_LABELS = {
  active: "Active",
  paused: "Paused",
  done: "Done",
  error: "Failed",
};
