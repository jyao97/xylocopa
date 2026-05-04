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

/** Geometric collapse-zone check. Decision order:
 *   1. Explicit opt-in [data-collapse-zone] → ALWAYS counts as collapse zone
 *      (overrides interactive + text rules — use this for elements that
 *      look like buttons/text but are semantically "chrome": timestamps,
 *      status labels, drag handles, etc).
 *   2. Explicit opt-out [data-no-collapse] / [data-no-longpress] → never
 *      a collapse zone (existing tag-picker convention).
 *   3. Inside any interactive element (button/input/textarea/select/a/
 *      [role=button]/[contenteditable]) → not a collapse zone.
 *   4. Any ancestor up to `root` is an inline text element → not a
 *      collapse zone (the user might be selecting text).
 *   5. Otherwise → collapse zone (layout div, gap, icon container).
 */
const TEXT_TAGS = new Set([
  "P", "STRONG", "EM", "CODE", "PRE", "LABEL",
  "H1", "H2", "H3", "H4", "H5", "H6",
]);
export function isCollapseZone(target, root) {
  if (!(target instanceof Element)) return false;
  // 1. Explicit opt-in — wins over everything.
  const optIn = target.closest("[data-collapse-zone]");
  if (optIn && root.contains(optIn)) return true;
  // 2. Explicit opt-out.
  if (target.closest("[data-no-collapse], [data-no-longpress]")) return false;
  // 3. Interactive element.
  if (target.closest(
    "button, input, textarea, select, a, [role='button'], [contenteditable='true']"
  )) return false;
  // 4. Inline text content (note: SPAN is intentionally NOT in TEXT_TAGS —
  //    spans are too generic to blanket-block, and chrome spans like
  //    timestamps would otherwise need data-collapse-zone everywhere.
  //    Real prose text is in <p>/<h*>; if you have a span you don't want
  //    collapsing, wrap or mark with data-no-collapse).
  let cur = target;
  while (cur && cur !== root) {
    if (TEXT_TAGS.has(cur.tagName)) return false;
    cur = cur.parentElement;
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
      ? "shadow-2xl ring-1 ring-cyan-500/30 z-10"
      : "shadow-card active:bg-input"
  } ${selecting && selected ? "ring-2 ring-cyan-500/50 brightness-[0.88]" : ""} ${className}`;

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
