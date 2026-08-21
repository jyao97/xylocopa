// Active orb character + user-generated collection, persisted per device.
//
// localStorage (not the backend): a skin is a per-device cosmetic, and
// keeping it client-side means zero migration surface. Subscribers let the
// FAB re-render when the picker (inside the bubble) changes the skin.

import { safeSetItem } from "../../lib/safeStorage";
import { DEFAULT_CHARACTER, PRESET_CHARACTERS } from "./characters";

const ACTIVE_KEY = "xy:attn-character:v1";
const SAVED_KEY = "xy:attn-characters:v1";
const MAX_SAVED = 8;

const subscribers = new Set();

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function getCharacter() {
  const c = read(ACTIVE_KEY, null);
  // Minimal shape check — a corrupt entry falls back to the default
  // rather than exploding the FAB on every route.
  if (c && typeof c === "object" && c.id && Array.isArray(c.extras || [])) return c;
  return DEFAULT_CHARACTER;
}

export function setCharacter(character) {
  try {
    if (!character || character.id === DEFAULT_CHARACTER.id) {
      localStorage.removeItem(ACTIVE_KEY);
    } else {
      safeSetItem(ACTIVE_KEY, JSON.stringify(character));
    }
  } catch { /* private browsing — in-memory subscribers still update */ }
  for (const fn of subscribers) {
    try { fn(); } catch { /* subscriber errors are theirs */ }
  }
}

export function subscribeCharacter(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function listSavedCharacters() {
  const list = read(SAVED_KEY, []);
  return Array.isArray(list) ? list : [];
}

export function saveGeneratedCharacter(character) {
  const list = listSavedCharacters().filter((c) => c.id !== character.id);
  list.unshift(character);
  try {
    safeSetItem(SAVED_KEY, JSON.stringify(list.slice(0, MAX_SAVED)));
  } catch { /* quota — active selection still works */ }
}

export function deleteSavedCharacter(id) {
  try {
    safeSetItem(SAVED_KEY, JSON.stringify(
      listSavedCharacters().filter((c) => c.id !== id),
    ));
  } catch { /* ignore */ }
}

export { PRESET_CHARACTERS, DEFAULT_CHARACTER };
