// The bubble's conversation memory.
//
// sessionStorage on purpose: the transcript is a light, per-tab surface —
// it should survive a route change or an accidental close, but a fresh tab
// starting clean is a feature (yesterday's "remind me in an hour" would
// only be confusing today). Jobs themselves are the durable record and
// live in the backend.

const KEY = "xy:attn-chat:v1";
const CAP = 40;

let counter = 0;
export function makeMsg(role, text, extra = {}) {
  counter += 1;
  return {
    id: `${Date.now().toString(36)}-${counter}`,
    role,
    text,
    ts: Date.now(),
    ...extra,
  };
}

export function loadTranscript() {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.slice(-CAP) : [];
  } catch {
    return [];
  }
}

export function saveTranscript(list) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(list.slice(-CAP)));
  } catch {
    // storage full or disabled — the in-memory copy still works
  }
}
