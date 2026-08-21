import { memo, useRef, useState, useEffect, useContext, createContext } from "react";

/** Context for swipe actions — provided by TasksPage */
export const CardSwipeContext = createContext(null);

const DELETE_REVEAL = 80;
const LONG_SWIPE = 200;
const SELECT_THRESHOLD = 60;

/** Padding class for the clickable content area inside a card. */
export function cardPadding(expanded, selecting) {
  return expanded && !selecting ? "py-5" : "py-[18px]";
}

/** Collapse-zone check (blacklist model). Decision order:
 *   1. Explicit opt-in [data-collapse-zone] → ALWAYS collapse (overrides
 *      both blacklists — use for chrome that looks like a button or text:
 *      timestamps, status labels, drag handles, etc).
 *   2. Inside an interactive element (button/input/textarea/select/a/
 *      [role=button]/[contenteditable]) OR explicit
 *      [data-no-collapse]/[data-no-longpress] → don't collapse.
 *   3. The click point falls on a text node (caretRangeFromPoint returns
 *      a TEXT_NODE) → don't collapse, the user might be selecting text.
 *   4. Otherwise → collapse zone.
 *
 * Note: takes the SyntheticEvent (not just target) because rule 3 needs
 * clientX/clientY for hit-testing.
 */
export function isCollapseZone(event, root) {
  const target = event?.target;
  if (!(target instanceof Element)) return false;

  // Walk from target up to (but excluding) root. The card root itself
  // commonly carries role='button' / cursor-pointer for collapsed-state
  // tap-to-expand — if we let closest() cross it, every click would match
  // [role=button] and nothing would ever count as a collapse zone.
  const ancestorMatch = (sel) => {
    let cur = target;
    while (cur && cur !== root) {
      if (cur.matches(sel)) return cur;
      cur = cur.parentElement;
    }
    return null;
  };

  // 1. Explicit opt-in.
  if (ancestorMatch("[data-collapse-zone]")) return true;
  // 2. Interactive / explicit opt-out.
  if (ancestorMatch(
    "button, input, textarea, select, a, [role='button'], " +
    "[contenteditable='true'], [data-no-collapse], [data-no-longpress]"
  )) return false;
  // 3. Geometric text-hit-test — the browser knows where it laid glyphs
  //    out. caretRangeFromPoint snaps to the nearest text node even when
  //    the click is in surrounding padding/margin (its job is to give a
  //    useful caret target for any click), so the snapped node alone
  //    isn't enough — we also have to verify the click coords actually
  //    fall within one of that node's rendered line rects. Otherwise
  //    clicks in the card's outer px-5 padding would be reported as "on
  //    text" (snapped to a description text node) and never collapse.
  const x = event.clientX, y = event.clientY;
  if (typeof x === "number" && typeof y === "number") {
    const r = (typeof document.caretRangeFromPoint === "function")
      ? document.caretRangeFromPoint(x, y)
      : (typeof document.caretPositionFromPoint === "function"
        ? document.caretPositionFromPoint(x, y) : null);
    const node = r && (r.startContainer || r.offsetNode);
    if (node && node.nodeType === Node.TEXT_NODE && root.contains(node)) {
      const range = document.createRange();
      range.selectNodeContents(node);
      const rects = range.getClientRects();
      for (const rect of rects) {
        if (x >= rect.left && x <= rect.right &&
            y >= rect.top && y <= rect.bottom) {
          return false; // genuinely on the text glyphs
        }
      }
      // Snapped to nearby text but click was actually in whitespace —
      // fall through to "collapse zone".
    }
  }
  return true;
}

/**
 * Shared card wrapper with pop-out expand effect + swipe gestures.
 * All task cards should use this as their outer container.
 *
 * Swipe gestures (when CardSwipeContext is provided + taskId is set):
 *   - Swipe left  → enter multi-select mode
 *   - Swipe right → reveal delete bar
 *   - Long swipe right → direct delete
 */
export default memo(function CardShell({
  taskId, expanded, selecting, selected, className = "", children, ...props
}) {
  const swipeCtx = useContext(CardSwipeContext);
  const contentRef = useRef(null);
  const [phase, setPhase] = useState("idle"); // idle | revealed | removing
  const phaseRef = useRef("idle");
  const gestureRef = useRef({
    startX: 0, startY: 0, locked: null,
    dragging: false, currentX: 0, suppressClick: false,
  });

  const canSwipe = !!swipeCtx && !!taskId && !selecting;

  // Keep ref in sync with state (for use inside native event listeners)
  phaseRef.current = phase;

  // Reset when entering select mode
  useEffect(() => {
    if (selecting && phase !== "idle") {
      setPhase("idle");
      const el = contentRef.current;
      if (el) { el.style.transition = ""; el.style.transform = ""; }
    }
  }, [selecting, phase]);

  // Attach native touch listeners (passive: false needed for preventDefault in touchmove)
  useEffect(() => {
    const el = contentRef.current;
    if (!el || !canSwipe) return;

    const g = gestureRef.current;

    const snapBack = () => {
      el.style.transition = "transform 0.3s cubic-bezier(0.22, 1.15, 0.36, 1)";
      el.style.transform = "translateX(0)";
      setPhase("idle");
    };

    const slideOut = () => {
      el.style.transition = "transform 0.25s ease-in";
      el.style.transform = "translateX(110%)";
      setPhase("removing");
      setTimeout(() => swipeCtx?.onDelete?.(taskId), 300);
    };

    const onStart = (e) => {
      if (phaseRef.current === "removing") return;
      if (phaseRef.current === "revealed") {
        // Touch card while delete is showing → close it, suppress the click
        snapBack();
        g.dragging = false;
        g.suppressClick = true;
        return;
      }
      const t = e.touches[0];
      g.startX = t.clientX;
      g.startY = t.clientY;
      g.locked = null;
      g.dragging = true;
      g.currentX = 0;
      g.suppressClick = false;
      el.style.transition = "none";
    };

    const onMove = (e) => {
      if (!g.dragging) return;
      const t = e.touches[0];
      const dx = t.clientX - g.startX;
      const dy = t.clientY - g.startY;

      // Lock direction on first significant movement
      if (g.locked === null && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) {
        g.locked = Math.abs(dx) > Math.abs(dy) ? "h" : "v";
        if (g.locked === "v") { g.dragging = false; return; }
      }
      if (g.locked !== "h") return;

      e.preventDefault(); // prevent scroll while swiping horizontally
      g.suppressClick = true; // any horizontal movement = suppress the tap
      g.currentX = dx > 0 ? Math.min(dx, 320) : Math.max(dx * 0.4, -120);
      el.style.transform = `translateX(${g.currentX}px)`;
    };

    const onEnd = () => {
      if (!g.dragging) return;
      g.dragging = false;
      const x = g.currentX;

      if (x > LONG_SWIPE) { slideOut(); return; }
      if (x > DELETE_REVEAL) {
        el.style.transition = "transform 0.3s cubic-bezier(0.22, 1.15, 0.36, 1)";
        el.style.transform = `translateX(${DELETE_REVEAL}px)`;
        setPhase("revealed");
        return;
      }
      // Snap back first, then enter select mode after animation settles
      el.style.transition = "transform 0.3s cubic-bezier(0.22, 1.15, 0.36, 1)";
      el.style.transform = "translateX(0)";
      if (x < -SELECT_THRESHOLD) {
        setTimeout(() => swipeCtx?.onEnterSelect?.(taskId), 250);
      }
    };

    // Capture-phase click listener to suppress clicks after swipe/close gestures
    const onClick = (e) => {
      if (g.suppressClick) {
        g.suppressClick = false;
        e.stopPropagation();
        e.preventDefault();
      }
    };

    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd, { passive: true });
    el.addEventListener("click", onClick, { capture: true });

    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("click", onClick, { capture: true });
    };
  }, [canSwipe, taskId, swipeCtx]);

  // NOTE: don't use `transform: scale()` for elevation — it leaves the
  // layout box unchanged but extends the visual bounds, which overlaps
  // sibling cards in any flow layout. Express "expanded" through
  // non-layout properties only: shadow, ring, brightness.
  const baseClasses = `w-full text-left rounded-2xl bg-surface overflow-hidden transition-[box-shadow,ring-color,opacity,background-color,filter] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${
    expanded && !selecting
      ? "shadow-2xl ring-1 ring-accent-50 z-10"
      : "shadow-card active:bg-input"
  } ${selecting && selected ? "ring-2 ring-accent-50 brightness-[0.88]" : ""} ${className}`;

  // No swipe support — plain card
  if (!swipeCtx || !taskId) {
    return (
      <div data-card className={baseClasses} {...props}>
        {children}
      </div>
    );
  }

  // With swipe support — wrapper + red delete bg + slidable card
  const handleDeleteClick = (e) => {
    e.stopPropagation();
    if (phaseRef.current !== "revealed") return;
    const el = contentRef.current;
    if (el) {
      el.style.transition = "transform 0.25s ease-in";
      el.style.transform = "translateX(110%)";
    }
    setPhase("removing");
    setTimeout(() => swipeCtx?.onDelete?.(taskId), 300);
  };

  return (
    <div data-card className="relative rounded-2xl overflow-hidden">
      {/* Red delete background — always mounted, visible when card slides right */}
      <div
        className={`absolute inset-0 flex items-center rounded-2xl transition-colors duration-150 ${
          phase === "removing" ? "bg-red-600" : "bg-red-500"
        }`}
        onClick={handleDeleteClick}
      >
        <div className="flex items-center gap-2 pl-5">
          <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
          </svg>
          <span className="text-white text-sm font-medium">Delete</span>
        </div>
      </div>

      {/* Slidable card content */}
      <div ref={contentRef} className={`relative ${baseClasses}`} {...props}>
        {children}
      </div>
    </div>
  );
});
