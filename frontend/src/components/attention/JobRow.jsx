import { useState } from "react";

import {
  STATUS_LABELS,
  absoluteTime,
  describeAction,
  describeCooldown,
  describeTrigger,
  kindMeta,
  relativeTime,
} from "./jobKinds";

/**
 * One job in the assistant panel.
 *
 * All presentation goes through jobKinds.js, so a job whose trigger or
 * action this bundle has never heard of still renders a readable row rather
 * than a blank one — the backend registries can move ahead of the frontend.
 */

const ICONS = {
  bell: "M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9",
  eye: "M2.5 12S5.5 5.5 12 5.5 21.5 12 21.5 12 18.5 18.5 12 18.5 2.5 12 2.5 12z M12 14.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
  sparkle: "M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z",
  bolt: "M13 2L4.5 13.5H11l-1 8.5L19.5 10H13l0-8z",
};

function Icon({ name, className }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d={ICONS[name] || ICONS.bell} />
    </svg>
  );
}

export default function JobRow({ job, onSnooze, onToggle, onDelete, onRunNow }) {
  const [expanded, setExpanded] = useState(false);
  const meta = kindMeta(job.kind);
  const paused = job.status === "paused";
  const failed = job.status === "error";
  const done = job.status === "done";

  const when = describeTrigger(job);
  const what = describeAction(job);
  const cooldown = describeCooldown(job.min_interval_seconds);

  return (
    <div
      className={`rounded-xl border px-3 py-2.5 transition-colors ${
        failed
          ? "border-red-500/30 bg-red-500/5"
          : paused || done
            ? "border-divider bg-surface opacity-60"
            : "attn-edge-25 attn-tint-6"
      }`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="w-full flex items-start gap-2.5 text-left"
      >
        <Icon
          name={meta.icon}
          className={`w-4 h-4 mt-0.5 shrink-0 ${failed ? "text-red-400" : paused || done ? "text-faint" : "text-attn"}`}
        />
        <span className="flex-1 min-w-0">
          <span className="block text-[13px] font-medium text-heading leading-snug truncate">
            {job.title || "Untitled job"}
          </span>
          <span className="block text-[11px] text-dim leading-snug truncate">
            {when}
            {what ? ` · ${what}` : ""}
          </span>
        </span>
        <span className="shrink-0 flex flex-col items-end gap-0.5">
          {job.next_run_at && !paused && !done && (
            <span className="text-[10px] font-medium text-attn tabular-nums">
              {relativeTime(job.next_run_at)}
            </span>
          )}
          {(paused || done || failed) && (
            <span className="text-[10px] font-medium text-faint">
              {STATUS_LABELS[job.status] || job.status}
            </span>
          )}
          {job.recurring && !done && (
            <span className="text-[9px] text-faint">repeats</span>
          )}
        </span>
      </button>

      {failed && job.last_error && (
        <p className="mt-1.5 text-[10.5px] text-red-500 dark:text-red-400 leading-snug break-words">
          {job.last_error}
        </p>
      )}

      {expanded && (
        <div className="mt-2 pt-2 border-t border-divider space-y-2">
          {job.source_text && (
            <p className="text-[11px] text-dim leading-snug">
              <span className="text-faint">You said:</span> {job.source_text}
            </p>
          )}
          <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[10.5px]">
            <dt className="text-faint">Trigger</dt>
            <dd className="text-dim">{job.trigger_type}</dd>
            <dt className="text-faint">Action</dt>
            <dd className="text-dim">
              {job.action_type}
              {job.costly && <span className="text-attn"> · spends tokens</span>}
            </dd>
            {job.next_run_at && (
              <>
                <dt className="text-faint">Next</dt>
                <dd className="text-dim">{absoluteTime(job.next_run_at)}</dd>
              </>
            )}
            {job.last_fired_at && (
              <>
                <dt className="text-faint">Last fired</dt>
                <dd className="text-dim">
                  {absoluteTime(job.last_fired_at)}
                  {job.fire_count > 1 ? ` (${job.fire_count}\u00d7)` : ""}
                </dd>
              </>
            )}
            {cooldown && (
              <>
                <dt className="text-faint">Rate limit</dt>
                <dd className="text-dim">{cooldown}</dd>
              </>
            )}
          </dl>

          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            <RowAction onClick={onRunNow}>Run now</RowAction>
            {!done && (
              <RowAction onClick={onSnooze}>Snooze 15m</RowAction>
            )}
            {!done && (
              <RowAction onClick={onToggle}>{paused ? "Resume" : "Pause"}</RowAction>
            )}
            <RowAction onClick={onDelete} danger>Delete</RowAction>
          </div>
        </div>
      )}
    </div>
  );
}

function RowAction({ onClick, children, danger = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2 py-0.5 rounded-full text-[10.5px] font-medium transition-colors ${
        danger
          ? "text-red-500 dark:text-red-400 hover:bg-red-500/12"
          : "text-dim hover:text-heading hover:bg-hover"
      }`}
    >
      {children}
    </button>
  );
}
