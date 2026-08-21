import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import FluentEmoji from "./FluentEmoji";
import { relativeTime } from "../lib/formatters";
import { updateBookmark } from "../lib/api";
import { forwardState } from "../lib/nav";
import { useToast } from "../contexts/ToastContext";

// Map backend `kind` → fallback emoji when AI summary_emoji is missing.
const KIND_FALLBACK_EMOJI = {
  message: "💬",
  image: "🖼️",
  file: "📄",
};

function pickEmoji(item) {
  if (item.summary_emoji && item.summary_emoji.trim()) return item.summary_emoji;
  return KIND_FALLBACK_EMOJI[item.kind] || "💬";
}

function BookmarkRow({ projectName, item, onOpen, isPendingDelete, onTogglePendingDelete, onPatched }) {
  const isFile = item.kind === "file";
  const meta = item.created_at ? relativeTime(item.created_at) : "";

  // Editing state for the top text (the user-editable "title" — backed by user_note).
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const taRef = useRef(null);
  const toast = useToast();

  const topText =
    item.body || (item.summary === null ? "Summarizing…" : "(no summary)");

  useEffect(() => {
    if (!editing) return;
    const el = taRef.current;
    if (!el) return;
    el.focus();
    const len = el.value.length;
    el.setSelectionRange(len, len);
  }, [editing]);

  const startEditing = (e) => {
    e?.stopPropagation();
    if (isPendingDelete || editing) return;
    setDraft(item.body || "");
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setDraft("");
  };

  const saveEditing = async () => {
    if (saving) return;
    const next = draft.trim();
    const original = (item.body || "").trim();
    if (next === original) {
      cancelEditing();
      return;
    }
    setSaving(true);
    try {
      const updated = await updateBookmark(projectName, item.message_id, next);
      onPatched?.(item.message_id, updated);
      toast.success(next ? "Bookmark title saved" : "Reverted to AI summary");
      setEditing(false);
    } catch (err) {
      toast.error(err?.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleRowClick = () => {
    if (editing) return;
    onOpen?.();
  };

  return (
    <div className="rounded-2xl bg-surface shadow-card overflow-hidden">
      <div
        role={editing ? undefined : "button"}
        tabIndex={editing ? -1 : 0}
        onClick={handleRowClick}
        onKeyDown={(e) => {
          if (editing) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpen?.();
          }
        }}
        style={{ WebkitTouchCallout: "none", WebkitTapHighlightColor: "transparent" }}
        className={`w-full text-left transition-[background-color] duration-200 ${
          editing
            ? ""
            : "active:bg-input hover:bg-input/40 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        }`}
      >
        <div className="flex items-start gap-3 px-5 py-3">
          <div className="shrink-0 w-7 h-7 flex items-center justify-center -ml-1 mt-0.5">
            <FluentEmoji char={pickEmoji(item)} size={22} />
          </div>

          <div className="min-w-0 flex-1">
            {/* Top row: title + time */}
            <div className="flex items-start justify-between gap-3">
              {editing ? (
                <textarea
                  ref={taRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  onBlur={saveEditing}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.preventDefault();
                      cancelEditing();
                    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault();
                      taRef.current?.blur();
                    }
                  }}
                  placeholder="Title — leave empty to use AI summary"
                  rows={Math.min(6, Math.max(1, draft.split("\n").length))}
                  disabled={saving}
                  className="w-full bg-input rounded-md px-2 py-0.5 text-[13px] leading-snug font-medium text-heading resize-none outline-none focus:ring-2 focus:ring-accent disabled:opacity-60"
                />
              ) : (
                <p className="text-[13px] leading-snug font-medium text-heading truncate">
                  {topText}
                </p>
              )}
              <span className="text-[11px] text-faint shrink-0 mt-0.5">{meta}</span>
            </div>
            {/* Subtitle row: title text + (edit / bookmark badges, accumulated rightward) */}
            <div className="flex items-center gap-2 mt-0.5">
              <p
                className={`text-xs truncate min-w-0 flex-1 ${
                  isFile ? "font-mono text-dim" : "text-dim"
                }`}
              >
                {item.title || "(untitled)"}
              </p>
              {!isPendingDelete && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); startEditing(e); }}
                  disabled={editing}
                  title="Edit title"
                  aria-label="Edit bookmark title"
                  className={`shrink-0 p-0.5 -my-0.5 rounded transition-colors ${
                    editing
                      ? "text-faint/40 cursor-default"
                      : "text-faint hover:text-heading hover:bg-input"
                  }`}
                >
                  {/* Heroicons v2 pencil — single clean diagonal stroke.
                      Nudge down 1px because the glyph sits in the upper-right
                      of its viewBox, so visual centroid is above geometric. */}
                  <svg className="w-[15px] h-[15px] block translate-y-px" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125" />
                  </svg>
                </button>
              )}
              {typeof onTogglePendingDelete === "function" && (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation();
                    onTogglePendingDelete(item.message_id);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.stopPropagation();
                      onTogglePendingDelete(item.message_id);
                    }
                  }}
                  title={isPendingDelete ? "Re-bookmark" : "Remove bookmark"}
                  className={`shrink-0 p-0.5 -my-0.5 rounded transition-colors cursor-pointer ${
                    isPendingDelete
                      ? "text-faint hover:text-amber-500 hover:bg-amber-500/10"
                      : "text-amber-500 hover:bg-amber-500/15"
                  }`}
                >
                  <svg className="w-[15px] h-[15px] block" fill={isPendingDelete ? "none" : "currentColor"} stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
                  </svg>
                </span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function BookmarksSection({ projectName, items, pendingDeletes, onTogglePendingDelete, onPatched }) {
  const navigate = useNavigate();
  const location = useLocation();
  const bookmarks = items || [];

  const handleOpen = (item) => {
    if (item?.agent_id && item?.message_id) {
      navigate(
        `/agents/${item.agent_id}?focus=${encodeURIComponent(item.message_id)}`,
        { state: forwardState(location) },
      );
    }
  };

  if (!bookmarks.length) {
    return (
      <div>
        <h2 className="text-sm font-semibold text-label uppercase tracking-wider px-1 mb-3">
          Bookmarks
        </h2>
        <div className="rounded-2xl bg-surface shadow-card py-10 text-center">
          <div className="mb-2 opacity-70">
            <FluentEmoji char="📌" size={28} />
          </div>
          <p className="text-sm text-dim">No bookmarks yet</p>
          <p className="text-xs text-faint mt-1">Double-tap any message to save it</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-sm font-semibold text-label uppercase tracking-wider px-1 mb-3">
        Bookmarks
      </h2>
      <div className="space-y-2">
        {bookmarks.map((item) => (
          <BookmarkRow
            key={item.message_id}
            projectName={projectName}
            item={item}
            onOpen={() => handleOpen(item)}
            isPendingDelete={pendingDeletes?.has?.(item.message_id) ?? false}
            onTogglePendingDelete={onTogglePendingDelete}
            onPatched={onPatched}
          />
        ))}
      </div>
    </div>
  );
}
