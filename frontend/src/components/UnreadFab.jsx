import { useNavigate, useLocation } from "react-router-dom";
import { useCallback } from "react";
import DraggableFab from "./DraggableFab";
import { useUnread } from "../contexts/UnreadContext";
import { forwardState } from "../lib/nav";

// Classic pre-orb attention FAB, rendered when the assistant character is
// switched off (Monitor > Display): tap jumps to the oldest unread chat
// (FIFO) or opens split screen when nothing is unread; long-press always
// opens split screen. Unread state is the cyan count badge (matching the
// send button) — the red unread markers live on the bottom nav bar, not
// here. Restored verbatim from the pre-orb version (1b2d0134~1).

const defaultPos = () => ({
  x: window.innerWidth - 64,
  y: window.innerHeight - 140,
});

export default function UnreadFab() {
  const navigate = useNavigate();
  const location = useLocation();
  const { list, total } = useUnread();

  // Hide on split screen page itself and on login
  const hidden = location.pathname === "/split" || location.pathname === "/login";

  const hasUnread = total > 0 && list.length > 0;

  const handleTap = useCallback(() => {
    if (hasUnread) {
      // Jump to oldest unread (FIFO — list[0] is oldest)
      const next = list[0];
      if (next) {
        navigate(`/agents/${next.id}`, { state: forwardState(location) });
        return;
      }
    }
    navigate("/split", { state: { initialPath: location.pathname } });
  }, [hasUnread, list, navigate, location]);

  const handleLongPress = useCallback(() => {
    // Always open split-screen, even with unread messages (escape hatch)
    navigate("/split", { state: { initialPath: location.pathname } });
  }, [navigate, location.pathname]);

  if (hidden) return null;

  if (hasUnread) {
    const label = total > 99 ? "99+" : String(total);
    return (
      <DraggableFab
        storageKey="ah:fab-pos-split-v3"
        defaultPosition={defaultPos}
        onClick={handleTap}
        onLongPress={handleLongPress}
        className="w-11 h-11 flex items-center justify-center rounded-full bg-accent hover:opacity-90 shadow-lg text-white font-semibold text-base transition-colors"
      >
        {label}
      </DraggableFab>
    );
  }

  return (
    <DraggableFab
      storageKey="ah:fab-pos-split-v3"
      defaultPosition={defaultPos}
      onClick={handleTap}
      onLongPress={handleLongPress}
      className="w-11 h-11 flex items-center justify-center rounded-full bg-surface shadow-lg border border-edge text-dim hover:text-accent hover:accent-edge-60 transition-colors "
    >
      {/* Mobile: top-bottom split icon */}
      <svg className="w-5 h-5 md:hidden" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
        <rect x="3" y="2" width="18" height="9" rx="2" />
        <rect x="3" y="13" width="18" height="9" rx="2" />
      </svg>
      {/* Desktop: left-right split icon */}
      <svg className="w-5 h-5 hidden md:block" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
        <rect x="2" y="3" width="9" height="18" rx="2" />
        <rect x="13" y="3" width="9" height="18" rx="2" />
      </svg>
    </DraggableFab>
  );
}
