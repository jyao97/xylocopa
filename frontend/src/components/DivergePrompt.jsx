import { useState, useEffect, useRef } from "react";
import { divergeMessage } from "../lib/api";
import { useToast } from "../contexts/ToastContext";

/**
 * Centered modal shown when the user taps Diverge in a chat bubble's
 * action menu. Collects an optional branch purpose, then POSTs
 * /api/agents/{id}/messages/{mid}/diverge and hands the created agent
 * back to the caller via onDiverged(agent, purpose).
 *
 * Semantics (mirrors the backend):
 *   AGENT message → branch keeps history up to and including this reply.
 *   USER message  → branch cuts before this message (edit-and-resend);
 *                   the caller prefills the composer with its text.
 *
 * Chrome mirrors BookmarkNotePrompt's card (white/blur, 14px radius).
 */
export default function DivergePrompt({ agentId, message, onClose, onDiverged }) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const taRef = useRef(null);
  const toast = useToast();

  useEffect(() => {
    const t = setTimeout(() => taRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  if (!message?.id) return null;
  const isUser = message.role === "USER";

  const go = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const purpose = draft.trim();
      const agent = await divergeMessage(agentId, message.id, {
        purpose: purpose || null,
      });
      onDiverged?.(agent, purpose);
    } catch (err) {
      toast.error(err?.message || "Failed to diverge");
      setBusy(false);
    }
  };

  return (
    <div className="diverge-overlay" onClick={() => !busy && onClose?.()}>
      <div className="diverge-card" onClick={(e) => e.stopPropagation()}>
        <p className="diverge-title flex items-center gap-1.5">
          <svg className="w-[18px] h-[18px] text-cyan-500 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 17h-8l-3.5-5H3" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 7h-8l-3.5 5" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M18 10l3-3-3-3" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M18 20l3-3-3-3" />
          </svg>
          Diverge conversation
        </p>
        <p className="diverge-hint">
          {isUser
            ? "Branches before this message — its text will prefill the new chat's composer."
            : "Branches after this reply — full history and tool context carry over."}
        </p>
        <textarea
          ref={taRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && !busy) onClose?.();
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) go();
          }}
          placeholder="Purpose of this branch (optional — becomes the Task title)"
          rows={2}
          className="diverge-textarea"
          disabled={busy}
        />
        <div className="flex items-center justify-end gap-2 mt-2">
          <button
            type="button"
            onClick={() => onClose?.()}
            disabled={busy}
            className="diverge-btn-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={go}
            disabled={busy}
            className="diverge-btn-go"
          >
            {busy ? "Diverging…" : "Diverge"}
          </button>
        </div>
      </div>
      <style>{`
        .diverge-overlay {
          position: fixed;
          inset: 0;
          z-index: 9998;
          background: rgba(0, 0, 0, 0.25);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 16px;
        }
        .diverge-card {
          background: rgba(255, 255, 255, 0.97);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border-radius: 14px;
          box-shadow: 0 2px 16px rgba(0,0,0,0.12), 0 0 0 0.5px rgba(0,0,0,0.06);
          padding: 14px;
          width: min(380px, calc(100vw - 32px));
        }
        .dark .diverge-card {
          background: rgba(44, 44, 46, 0.95);
          box-shadow: 0 2px 16px rgba(0,0,0,0.30), 0 0 0 0.5px rgba(255,255,255,0.08);
        }
        .diverge-title {
          color: #1c1c1e;
          font-size: 13px;
          font-weight: 600;
          line-height: 1.3;
        }
        .dark .diverge-title { color: #f5f5f7; }
        .diverge-hint {
          font-size: 11px;
          color: rgba(28, 28, 30, 0.5);
          margin: 6px 0 8px;
          line-height: 1.4;
        }
        .dark .diverge-hint { color: rgba(245, 245, 247, 0.5); }
        .diverge-textarea {
          width: 100%;
          border-radius: 10px;
          background: rgba(0, 0, 0, 0.03);
          border: 0.5px solid rgba(0, 0, 0, 0.08);
          padding: 8px 10px;
          font-size: 13px;
          color: #1c1c1e;
          resize: vertical;
          outline: none;
          transition: border-color 0.15s;
        }
        .diverge-textarea:focus { border-color: rgba(0, 0, 0, 0.20); }
        .diverge-textarea::placeholder { color: rgba(28, 28, 30, 0.35); }
        .dark .diverge-textarea {
          background: rgba(255, 255, 255, 0.04);
          border-color: rgba(255, 255, 255, 0.10);
          color: #f5f5f7;
        }
        .dark .diverge-textarea:focus { border-color: rgba(255, 255, 255, 0.25); }
        .dark .diverge-textarea::placeholder { color: rgba(245, 245, 247, 0.35); }
        .diverge-btn-cancel {
          font-size: 12px;
          padding: 4px 12px;
          border-radius: 9999px;
          color: rgba(28, 28, 30, 0.55);
          transition: background-color 0.15s;
        }
        .diverge-btn-cancel:hover { background-color: rgba(0, 0, 0, 0.05); }
        .dark .diverge-btn-cancel { color: rgba(245, 245, 247, 0.55); }
        .dark .diverge-btn-cancel:hover { background-color: rgba(255, 255, 255, 0.06); }
        .diverge-btn-go {
          font-size: 12px;
          padding: 4px 12px;
          border-radius: 9999px;
          background-color: rgba(28, 28, 30, 0.85);
          color: #fff;
          font-weight: 500;
          transition: background-color 0.15s, opacity 0.15s;
        }
        .diverge-btn-go:hover:not(:disabled) { background-color: #1c1c1e; }
        .diverge-btn-go:disabled { opacity: 0.4; cursor: not-allowed; }
        .dark .diverge-btn-go { background-color: rgba(245, 245, 247, 0.92); color: #1c1c1e; }
        .dark .diverge-btn-go:hover:not(:disabled) { background-color: #f5f5f7; }
      `}</style>
    </div>
  );
}
