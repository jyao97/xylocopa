import { useState, useCallback, useEffect, useRef } from "react";

/**
 * Persist a draft value in localStorage across navigation and refresh.
 * Returns [value, setValue, clearDraft].
 * setValue auto-syncs to localStorage on every call.
 * clearDraft removes the key (call after successful submission).
 */
export default function useDraft(key, initialValue = "") {
  const storageKey = key ? `draft:${key}` : null;
  // Use simple string mode only when initialValue is a string
  const stringMode = typeof initialValue === "string";

  const [value, _setValue] = useState(() => {
    if (!storageKey) return initialValue;
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) {
        return stringMode ? stored : JSON.parse(stored);
      }
    } catch (err) {
      // Expected: localStorage may throw in private browsing, or stored value may not be valid JSON
      console.warn("useDraft: failed to read draft from localStorage:", err);
    }
    return initialValue;
  });

  // Reload value when storageKey changes (e.g. ChatInput is reused across
  // agentId navigation without remounting). Without this, the in-memory state
  // from the previous key leaks into the new chat's textarea — including any
  // voice transcript that just landed before navigation.
  const lastKeyRef = useRef(storageKey);
  useEffect(() => {
    if (lastKeyRef.current === storageKey) return;
    lastKeyRef.current = storageKey;
    if (!storageKey) {
      _setValue(initialValue);
      return;
    }
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored !== null) {
        _setValue(stringMode ? stored : JSON.parse(stored));
      } else {
        _setValue(initialValue);
      }
    } catch (err) {
      console.warn("useDraft: failed to reload draft from localStorage:", err);
      _setValue(initialValue);
    }
    // initialValue intentionally omitted — capturing it would reset on every
    // render when callers pass an inline default like `[]` or `""`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey, stringMode]);

  const setValue = useCallback((v) => {
    _setValue((prev) => {
      const next = typeof v === "function" ? v(prev) : v;
      if (storageKey) {
        try {
          if (stringMode) {
            if (next) localStorage.setItem(storageKey, next);
            else localStorage.removeItem(storageKey);
          } else {
            localStorage.setItem(storageKey, JSON.stringify(next));
          }
        } catch (err) {
          // Expected: localStorage may throw in private browsing or when quota is exceeded
          console.warn("useDraft: failed to write draft to localStorage:", err);
        }
      }
      return next;
    });
  }, [storageKey, stringMode]);

  const clearDraft = useCallback(() => {
    if (storageKey) {
      try {
        localStorage.removeItem(storageKey);
      } catch (err) {
        // Expected: localStorage may throw in private browsing
        console.warn("useDraft: failed to clear draft from localStorage:", err);
      }
    }
  }, [storageKey]);

  return [value, setValue, clearDraft];
}
