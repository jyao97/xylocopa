import { useState, useEffect, useCallback } from "react";
import { applyPalette, THEME_EVENT } from "../lib/themes";

const STORAGE_KEY = "xylocopa-theme";
const LEGACY_STORAGE_KEY = "agenthive-theme";

// One-time migration of legacy key
try {
  const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
  if (legacy !== null && localStorage.getItem(STORAGE_KEY) === null) {
    localStorage.setItem(STORAGE_KEY, legacy);
  }
  if (legacy !== null) localStorage.removeItem(LEGACY_STORAGE_KEY);
} catch {}

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// `theme` is the resolved base mode ("light" | "dark"). Which palette that
// base maps to (default, soft-dark, solarized, custom, …) is resolved by
// applyPalette from localStorage — see lib/themes.js. The toggle flips
// between the palette chosen for light and the one chosen for dark.
export default function useTheme() {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    // Migrate old "system" preference to actual system value
    if (!stored || stored === "system") return getSystemTheme();
    return stored;
  });

  useEffect(() => {
    applyPalette(theme);
  }, [theme]);

  // Palette picker (Monitor > Display) may switch the base — stay in sync.
  useEffect(() => {
    const onChange = (e) => setTheme(e.detail.base);
    window.addEventListener(THEME_EVENT, onChange);
    return () => window.removeEventListener(THEME_EVENT, onChange);
  }, []);

  const toggle = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
