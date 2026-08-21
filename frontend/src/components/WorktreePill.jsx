import { useState, useEffect, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import PopoverArrow from "./PopoverArrow";

const LONG_PRESS_DELAY = 500;

export default function WorktreePill({ name, padY = "py-px", onCopy }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const pillRef = useRef(null);
  const popRef = useRef(null);
  const pressTimerRef = useRef(null);
  const longPressFiredRef = useRef(false);
  const hoverCloseTimerRef = useRef(null);

  const cancelHoverClose = useCallback(() => {
    if (hoverCloseTimerRef.current) {
      clearTimeout(hoverCloseTimerRef.current);
      hoverCloseTimerRef.current = null;
    }
  }, []);
  const scheduleHoverClose = useCallback(() => {
    cancelHoverClose();
    hoverCloseTimerRef.current = setTimeout(() => {
      hoverCloseTimerRef.current = null;
      setOpen(false);
    }, 150);
  }, [cancelHoverClose]);

  const openAt = useCallback(() => {
    const rect = pillRef.current?.getBoundingClientRect();
    if (rect) setPos({ top: rect.bottom, left: rect.left + rect.width / 2 });
    setOpen(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (pillRef.current?.contains(e.target)) return;
      if (popRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, [open]);

  const doCopy = useCallback(() => {
    if (!name) return;
    navigator.clipboard.writeText(name).catch(() => {});
    setOpen(false);
    onCopy?.(name);
  }, [name, onCopy]);

  const display = name || "(unnamed)";

  return (
    <span className="shrink-0 inline-flex items-center">
      <span
        ref={pillRef}
        role="button"
        tabIndex={0}
        onPointerEnter={(e) => {
          if (e.pointerType !== "mouse") return;
          cancelHoverClose();
          openAt();
        }}
        onPointerLeave={(e) => {
          if (e.pointerType !== "mouse") return;
          scheduleHoverClose();
        }}
        onPointerDown={(e) => {
          if (e.pointerType === "mouse") return;
          longPressFiredRef.current = false;
          pressTimerRef.current = setTimeout(() => {
            pressTimerRef.current = null;
            longPressFiredRef.current = true;
            openAt();
          }, LONG_PRESS_DELAY);
        }}
        onPointerUp={(e) => {
          if (e.pointerType === "mouse") return;
          if (pressTimerRef.current) {
            clearTimeout(pressTimerRef.current);
            pressTimerRef.current = null;
          }
        }}
        onPointerCancel={() => {
          if (pressTimerRef.current) {
            clearTimeout(pressTimerRef.current);
            pressTimerRef.current = null;
          }
        }}
        onClick={(e) => {
          e.stopPropagation();
          if (longPressFiredRef.current) {
            longPressFiredRef.current = false;
          }
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          doCopy();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openAt();
          }
        }}
        onContextMenu={(e) => e.preventDefault()}
        style={{ touchAction: "manipulation", userSelect: "none", WebkitUserSelect: "none", WebkitTouchCallout: "none" }}
        className={`text-[10px] font-medium px-1.5 ${padY} rounded-full bg-purple-500/15 text-purple-500 dark:text-purple-400 inline-flex items-center cursor-pointer hover:bg-purple-500/25 transition-colors`}
      >
        <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
        </svg>
        {/* Zero-width spacer: contributes a text-[10px] line-box (15px) so the
            inline-flex pill matches the height of text-only sibling pills,
            which size from line-height. Without this the SVG-only pill is
            ~3px shorter than text pills under any padY value. */}
        <span aria-hidden="true" className="invisible w-0 overflow-hidden">x</span>
      </span>
      {open && pos && createPortal(
        <div
          ref={popRef}
          className="fixed z-[61]"
          style={{ top: pos.top, left: pos.left, transform: "translateX(-50%)", paddingTop: 6 }}
          onPointerEnter={cancelHoverClose}
          onPointerLeave={(e) => { if (e.pointerType === "mouse") scheduleHoverClose(); }}
        >
          <div className="relative">
            <div className="px-2 py-1.5 rounded-lg bg-surface border border-divider shadow-popover flex items-center gap-2 whitespace-nowrap">
              <span className="text-[11px] text-dim">worktree:</span>
              <span className="text-[11px] font-mono text-body select-all">{display}</span>
              {name && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    doCopy();
                  }}
                  className="text-[10px] text-accent hover:underline"
                >
                  Copy
                </button>
              )}
            </div>
            <PopoverArrow size={8} />
          </div>
        </div>,
        document.body
      )}
    </span>
  );
}
