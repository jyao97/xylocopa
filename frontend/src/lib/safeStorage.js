// Quota-safe localStorage writes.
//
// localStorage throws QuotaExceededError ("The quota has been exceeded" on
// Safari/Firefox) once full — historically caused by the unbounded
// persisted detail caches (xy:*-cache:v1) accumulating one entry per agent
// ever seen. When that happens, user-critical writes (chat drafts, voice
// transcripts, diverge prefills) were silently dropped.
//
// safeSetItem retries after evicting advisory data, largest key first:
//   - xy:*-cache* persisted detail caches (rebuilt from the backend on use)
//   - draft:*:attachments chips (files are already uploaded server-side)
// Real drafts (draft:*) and auth/settings keys are never evicted.

function isQuotaError(err) {
  return !!err && (
    err.name === "QuotaExceededError" ||
    err.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
    err.code === 22 ||
    err.code === 1014
  );
}

const EVICTABLE = [
  (k) => k.startsWith("xy:") && k.includes("-cache"),
  (k) => k.startsWith("draft:") && k.endsWith(":attachments"),
];

export function safeSetItem(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (err) {
    if (!isQuotaError(err)) {
      console.warn("safeSetItem: write failed:", key, err);
      return false;
    }
  }
  try {
    const candidates = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k !== key && EVICTABLE.some((match) => match(k))) {
        candidates.push([k, (localStorage.getItem(k) || "").length]);
      }
    }
    candidates.sort((a, b) => b[1] - a[1]);
    for (const [k] of candidates) {
      localStorage.removeItem(k);
      try {
        localStorage.setItem(key, value);
        return true;
      } catch (err) {
        if (!isQuotaError(err)) break;
      }
    }
  } catch {
    // localStorage disabled entirely — fall through
  }
  console.warn("safeSetItem: quota exceeded, write dropped:", key);
  return false;
}
