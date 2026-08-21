import { useState, useRef, useCallback, useEffect } from "react";

const DRAG_THRESHOLD = 8;

// Position stored as {right, bottom} — offset from viewport edges.
// This ensures the button stays in the same relative position on resize.

function toAbs(rb, w, h) {
  return { x: window.innerWidth - rb.right - w, y: window.innerHeight - rb.bottom - h };
}

function toRB(abs, w, h) {
  return { right: window.innerWidth - abs.x - w, bottom: window.innerHeight - abs.y - h };
}

function clampRB(rb, w, h) {
  return {
    right: Math.max(0, Math.min(rb.right, window.innerWidth - w)),
    bottom: Math.max(0, Math.min(rb.bottom, window.innerHeight - h)),
  };
}

// Keep-out rectangles: the nav bar and text-input bar islands. These are
// the ONLY places the FAB may not park — it used to be barred from a
// full-width strip as tall as the tallest bottom bar, which on the chat
// page (composer island ~180px) forbade the entire lower fifth of the
// screen including the empty margins beside the islands.
function keepOutRects() {
  const rects = [];
  // A bar counts as pinned when it, or a near ancestor, is out-of-flow —
  // both islands are statically-positioned children of positioned
  // wrappers (the nav in a fixed flex row, the composer in an ABSOLUTE
  // overlay inside the non-scrolling app shell), so checking only the
  // element's own position misses them. Absolute counts as pinned here
  // because the shell never scrolls and the candidate set is already
  // restricted to bottom bars.
  const pinned = (el) => {
    let n = el;
    for (let i = 0; i < 5 && n && n !== document.body; i++) {
      const pos = window.getComputedStyle(n).position;
      if (pos === "fixed" || pos === "sticky" || pos === "absolute") return true;
      n = n.parentElement;
    }
    return false;
  };
  document.querySelectorAll("nav, [class*='glass-bar-nav']").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    // Bottom-bar sanity guard (as before): ignore headers and in-flow navs
    // that happen to scroll near the bottom.
    const fromBottom = window.innerHeight - r.top;
    if (fromBottom <= 0 || fromBottom >= 200) return;
    if (!pinned(el)) return;
    rects.push(r);
  });
  return rects;
}

// Push an intended landing spot out of any keep-out rect by the smallest
// displacement that stays on screen. Two passes: resolving against one
// island can land on another (nav + input bar overlap zones).
const KEEPOUT_MARGIN = 6;
function resolveKeepOut(abs, w, h, rects) {
  let { x, y } = abs;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  for (let pass = 0; pass < 2; pass++) {
    let movedAny = false;
    for (const r of rects) {
      const overlapsX = x < r.right + KEEPOUT_MARGIN && x + w > r.left - KEEPOUT_MARGIN;
      const overlapsY = y < r.bottom + KEEPOUT_MARGIN && y + h > r.top - KEEPOUT_MARGIN;
      if (!overlapsX || !overlapsY) continue;
      const candidates = [
        { dx: 0, dy: (r.top - KEEPOUT_MARGIN - h) - y },   // up, above the bar
        { dx: (r.left - KEEPOUT_MARGIN - w) - x, dy: 0 },  // left of the bar
        { dx: (r.right + KEEPOUT_MARGIN) - x, dy: 0 },     // right of the bar
        { dx: 0, dy: (r.bottom + KEEPOUT_MARGIN) - y },    // below the bar
      ].filter((c) => {
        const nx = x + c.dx;
        const ny = y + c.dy;
        return nx >= 0 && nx + w <= vw && ny >= 0 && ny + h <= vh;
      }).sort((a, b) =>
        (Math.abs(a.dx) + Math.abs(a.dy)) - (Math.abs(b.dx) + Math.abs(b.dy)));
      if (candidates.length) {
        x += candidates[0].dx;
        y += candidates[0].dy;
        movedAny = true;
      }
    }
    if (!movedAny) break;
  }
  return { x, y };
}

export default function DraggableFab({ storageKey, defaultPosition, onClick, onLongPress, onDragChange, ariaLabel, className, children, outerRef }) {
  const fabRef = useRef(null);
  const sizeRef = useRef({ w: 44, h: 44 });
  const [rb, setRB] = useState(null); // {right, bottom}
  const rbRef = useRef(rb);
  rbRef.current = rb;
  const dragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0 });
  const absStart = useRef({ x: 0, y: 0 });
  const moved = useRef(false);
  const longPressTimer = useRef(null);
  const longPressFired = useRef(false);
  const cachedRects = useRef([]);
  const onClickRef = useRef(onClick);
  onClickRef.current = onClick;
  const onLongPressRef = useRef(onLongPress);
  onLongPressRef.current = onLongPress;
  const onDragChangeRef = useRef(onDragChange);
  onDragChangeRef.current = onDragChange;

  // Resolve position on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const p = JSON.parse(saved);
        if (p.right != null && p.bottom != null) {
          setRB(clampRB(p, sizeRef.current.w, sizeRef.current.h));
          return;
        }
        if (p.x != null && p.y != null) {
          setRB(clampRB(toRB(p, sizeRef.current.w, sizeRef.current.h), sizeRef.current.w, sizeRef.current.h));
          return;
        }
      }
    } catch { /* use default */ }
    const dp = typeof defaultPosition === "function" ? defaultPosition() : defaultPosition;
    setRB(toRB(dp, sizeRef.current.w, sizeRef.current.h));
  }, [storageKey, defaultPosition]);

  // Measure actual size after first render
  useEffect(() => {
    if (fabRef.current) {
      const rect = fabRef.current.getBoundingClientRect();
      sizeRef.current = { w: rect.width, h: rect.height };
    }
  });

  // Use a stable onStart via ref — avoids re-creating the callback when rb changes
  const lastTouchAt = useRef(0);
  const onStart = useCallback((e) => {
    // After a touch, browsers replay a synthetic mousedown/mouseup pair
    // (React's root touchstart listener is passive, so preventDefault
    // below can't suppress them). Without this guard every touch tap runs
    // onClick twice — harmless for idempotent opens, but it instantly
    // re-closes anything the first tap toggled open.
    if (e.type === "touchstart") lastTouchAt.current = Date.now();
    if (e.type === "mousedown") {
      if (Date.now() - lastTouchAt.current < 700) return;
      if (e.button !== 0) return;
    }
    const t = e.touches ? e.touches[0] : e;
    const { w, h } = sizeRef.current;
    const currentRB = rbRef.current;
    dragStart.current = { x: t.clientX, y: t.clientY };
    absStart.current = currentRB ? toAbs(currentRB, w, h) : { x: 0, y: 0 };
    moved.current = false;
    longPressFired.current = false;
    dragging.current = true;
    // Cache keep-out rects once at drag start (expensive DOM query); the
    // bars don't move mid-drag.
    cachedRects.current = keepOutRects();
    // Start long-press timer
    clearTimeout(longPressTimer.current);
    longPressTimer.current = setTimeout(() => {
      longPressFired.current = true;
      if (navigator.vibrate) navigator.vibrate(30);
      onLongPressRef.current?.();
    }, 600);
    e.preventDefault();
  }, []); // stable — reads rb via rbRef

  useEffect(() => {
    const onMove = (e) => {
      if (!dragging.current) return;
      const t = e.touches ? e.touches[0] : e;
      const dx = t.clientX - dragStart.current.x;
      const dy = t.clientY - dragStart.current.y;
      if (!moved.current && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!moved.current) {
        // First move: disable CSS transitions so transform updates are instant
        if (fabRef.current) fabRef.current.style.transition = 'none';
        // Notified once per drag, on the first movement only — never per
        // frame, so the per-move path below stays free of React re-renders.
        onDragChangeRef.current?.(true);
      }
      moved.current = true;
      clearTimeout(longPressTimer.current); // Cancel long-press on drag
      // Direct DOM manipulation — no React re-render during drag
      if (fabRef.current) {
        fabRef.current.style.transform = `translate3d(${dx}px, ${dy}px, 0)`;
      }
    };

    const onEnd = () => {
      if (!dragging.current) return;
      dragging.current = false;
      clearTimeout(longPressTimer.current);
      if (moved.current) onDragChangeRef.current?.(false);
      if (moved.current) {
        // Commit final position to React state (single re-render)
        const el = fabRef.current;
        if (el) {
          const rect = el.getBoundingClientRect();
          el.style.transform = "";
          const { w, h } = sizeRef.current;
          const abs = resolveKeepOut(
            { x: rect.left, y: rect.top }, w, h, cachedRects.current,
          );
          const final_ = clampRB(toRB(abs, w, h), w, h);
          // Restore CSS transitions after position committed
          requestAnimationFrame(() => { if (el) el.style.transition = ''; });
          setRB(final_);
          try { localStorage.setItem(storageKey, JSON.stringify(final_)); } catch { /* ok */ }
        }
      } else if (!longPressFired.current) {
        // Not dragged, not long-pressed — this was a tap
        onClickRef.current?.();
      }
    };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onEnd);
    window.addEventListener("touchmove", onMove, { passive: false });
    window.addEventListener("touchend", onEnd);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onEnd);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
    };
  }, [storageKey]);

  // Block ALL click events — taps are handled in onEnd above
  const blockClick = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  if (!rb) return null;

  return (
    <button
      ref={(el) => {
        fabRef.current = el;
        // Exposes the live element (not a snapshot) so the parent can
        // anchor popovers to it and write CSS vars onto it.
        if (outerRef) outerRef.current = el;
      }}
      type="button"
      onMouseDown={onStart}
      onTouchStart={onStart}
      onClick={blockClick}
      aria-label={ariaLabel}
      className={className}
      style={{ position: "fixed", right: rb.right, bottom: rb.bottom, zIndex: 50, touchAction: "none", willChange: "transform" }}
    >
      {children}
    </button>
  );
}
