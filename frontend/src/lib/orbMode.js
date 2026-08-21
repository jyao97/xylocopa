// Assistant-character (orb) kill switch.
//
// The orb/bubble assistant UI is feature-flagged while the character is
// being polished: with the flag off, AttentionButton renders the classic
// unread FAB (tap → oldest unread chat, long-press → split screen, red
// count badge) and none of the orb code mounts. The attention job ENGINE
// (backend) is independent of this flag and keeps running either way.
//
// Default is OFF. Toggled in Monitor > Display.

const STORAGE_KEY = "xy:orb-mode";
export const ORB_EVENT = "xy:orb-mode-changed";

export function getOrbEnabled() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setOrbEnabled(on) {
  try {
    if (on) localStorage.setItem(STORAGE_KEY, "1");
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent(ORB_EVENT));
}
