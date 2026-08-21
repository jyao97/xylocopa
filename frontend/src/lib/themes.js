// Theme palette registry + custom-theme plumbing.
//
// A palette = a `theme-<id>` class on <html> layered over a light/dark base
// (dark palettes also carry `.dark` so Tailwind `dark:` variants keep
// working). Preset token values live in index.css; this module only knows
// each preset's id, base, and preview swatches. The "custom" palette is
// user-authored: 6 core colors + base are stored in localStorage, the full
// ~20-token set is derived here, compiled to CSS, and injected as a
// <style id="xy-custom-theme"> that sits BEFORE the bundled stylesheet
// (html.theme-custom at (0,1,1) beats .dark/:root by specificity, while
// html.eink / html.no-glass — same specificity, later source — still win).
//
// Storage:
//   xylocopa-theme        — base mode "light" | "dark" (pre-existing key)
//   xy:palette-light      — palette id chosen for the light base
//   xy:palette-dark       — palette id chosen for the dark base
//   xy:custom-theme       — JSON {base, colors:{page,surface,heading,body,edge}}
//   xy:custom-theme-css   — derived CSS, injected pre-paint by index.html
//   xy:themecolor-<base>  — active page color, for the pre-paint meta tag
//
// The sun/moon toggle keeps its meaning: it flips between the palette you
// chose for light and the one you chose for dark.

const PALETTE_KEY = (base) => `xy:palette-${base}`;
const CUSTOM_KEY = "xy:custom-theme";
const CUSTOM_CSS_KEY = "xy:custom-theme-css";
const META_COLOR_KEY = (base) => `xy:themecolor-${base}`;
export const THEME_EVENT = "xy:theme-changed";

// `core` mirrors the preset's page/surface/heading/body/edge tokens in
// index.css — it seeds the custom editor when starting from that preset.
export const PRESETS = [
  { id: "light", name: "Light", base: "light",
    preview: { page: "#ffffff", surface: "#f6f7f8", text: "#222222" },
    core: { page: "#ffffff", surface: "#f6f7f8", heading: "#222222", body: "#374151", edge: "#d1d5db", bubble: "#0891b2" } },
  { id: "dark", name: "Dark", base: "dark",
    preview: { page: "#030712", surface: "#111827", text: "#f3f4f6" },
    core: { page: "#030712", surface: "#111827", heading: "#f3f4f6", body: "#d1d5db", edge: "#374151", bubble: "#155e75" } },
  { id: "soft-dark", name: "Soft Dark", base: "dark",
    preview: { page: "#17181c", surface: "#1e2024", text: "#e8e6e3" },
    core: { page: "#17181c", surface: "#1e2024", heading: "#e8e6e3", body: "#c9c7c3", edge: "#383b41", bubble: "#155e75" } },
  { id: "solarized-light", name: "Solarized Light", base: "light",
    preview: { page: "#fdf6e3", surface: "#f3ecd9", text: "#073642" },
    core: { page: "#fdf6e3", surface: "#f3ecd9", heading: "#073642", body: "#586e75", edge: "#d5cdb4", bubble: "#1f74b0" } },
  { id: "solarized-dark", name: "Solarized Dark", base: "dark",
    preview: { page: "#002b36", surface: "#073642", text: "#aebcba" },
    core: { page: "#002b36", surface: "#073642", heading: "#aebcba", body: "#90a2a4", edge: "#29525e", bubble: "#135b84" } },
  { id: "nord", name: "Nord", base: "dark",
    preview: { page: "#2e3440", surface: "#3b4252", text: "#eceff4" },
    core: { page: "#2e3440", surface: "#3b4252", heading: "#eceff4", body: "#d8dee9", edge: "#4c566a", bubble: "#526e91" } },
  { id: "everforest", name: "Everforest", base: "dark",
    preview: { page: "#272e33", surface: "#2d353b", text: "#d3c6aa" },
    core: { page: "#272e33", surface: "#2d353b", heading: "#d3c6aa", body: "#b9ad93", edge: "#4a555b", bubble: "#3a515d" } },
];

// Seed values for the custom editor, per base (= the default palettes).
export const CUSTOM_SEEDS = {
  light: { page: "#ffffff", surface: "#f6f7f8", heading: "#222222", body: "#374151", edge: "#d1d5db", bubble: "#0891b2" },
  dark:  { page: "#030712", surface: "#111827", heading: "#f3f4f6", body: "#d1d5db", edge: "#374151", bubble: "#155e75" },
};

export const CUSTOM_COLOR_FIELDS = [
  { key: "page", label: "Background" },
  { key: "surface", label: "Card" },
  { key: "heading", label: "Heading text" },
  { key: "body", label: "Body text" },
  { key: "edge", label: "Border" },
  { key: "bubble", label: "User bubble" },
];

/* ── color math ── */

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
}

// Linear mix of two hex colors; t=0 → a, t=1 → b.
function mix(a, b, t) {
  const ra = hexToRgb(a), rb = hexToRgb(b);
  if (!ra || !rb) return a;
  return rgbToHex(ra.map((v, i) => v + (rb[i] - v) * t));
}

function rgba(hex, alpha) {
  const c = hexToRgb(hex);
  return c ? `rgba(${c[0]},${c[1]},${c[2]},${alpha})` : hex;
}

// Space-separated channels for rgb(var(--x-rgb) / a) token consumers.
function channels(hex) {
  const c = hexToRgb(hex);
  return c ? `${c[0]} ${c[1]} ${c[2]}` : "0 0 0";
}

// Perceived brightness (YIQ); > 150 means dark ink reads better on it.
function isLightColor(hex) {
  const c = hexToRgb(hex);
  return c ? (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000 > 150 : false;
}

/* ── custom theme ── */

export function hasCustomConfig() {
  try {
    return localStorage.getItem(CUSTOM_KEY) !== null;
  } catch {
    return false;
  }
}

// Custom config seeded from a preset's core colors — the editor starts
// from what the user is currently looking at, not from the stock palette.
export function customConfigFromPreset(id) {
  const p = PRESETS.find((x) => x.id === id) || PRESETS[0];
  return { base: p.base, colors: { ...p.core } };
}

export function getCustomConfig() {
  try {
    const raw = JSON.parse(localStorage.getItem(CUSTOM_KEY) || "null");
    if (raw && (raw.base === "light" || raw.base === "dark") && raw.colors) {
      const colors = { ...CUSTOM_SEEDS[raw.base] };
      for (const f of CUSTOM_COLOR_FIELDS) {
        if (hexToRgb(raw.colors[f.key])) colors[f.key] = raw.colors[f.key].toLowerCase();
      }
      return { base: raw.base, colors };
    }
  } catch { /* corrupt JSON → fall through to seed */ }
  return { base: "dark", colors: { ...CUSTOM_SEEDS.dark } };
}

// Derive the full token set from the 5 core colors. Mixing pole is white on
// dark bases / black on light ones, so "elevated" always moves away from the
// page and text steps always fade toward it.
export function deriveCustomTokens({ base, colors }) {
  const { page, surface, heading, body, edge, bubble } = colors;
  const pole = base === "dark" ? "#ffffff" : "#000000";
  const glass = mix(surface, pole, 0.04);
  const glassNav = mix(surface, pole, 0.08);
  const bubbleInk = isLightColor(bubble) ? "#1f2937" : "#ffffff";
  return {
    page, surface, heading, body, edge, bubble,
    bubbleRgb: channels(bubble),
    bubbleInk,
    bubbleInkRgb: channels(bubbleInk),
    bubbleDim: mix(bubble, bubbleInk, 0.62),
    input: mix(surface, pole, 0.05),
    elevated: mix(surface, pole, 0.14),
    hover: mix(surface, pole, 0.22),
    inset: page,
    label: mix(body, page, 0.22),
    dim: mix(body, page, 0.38),
    faint: mix(body, page, 0.56),
    ghost: mix(body, page, 0.7),
    divider: mix(edge, page, 0.45),
    ringHover: mix(edge, pole, 0.15),
    hint: mix(body, page, 0.38),
    shadow: base === "dark" ? "rgba(0,0,0,0.4)" : "rgba(0,0,0,0.08)",
    skel: mix(surface, pole, 0.05),
    glass,
    glassNav,
    glassEdge: base === "dark" ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.5)",
    glassRgba: rgba(glass, 0.7),
    glassNavRgba: rgba(glassNav, 0.68),
  };
}

function buildCustomCss(cfg) {
  const t = deriveCustomTokens(cfg);
  return [
    "html.theme-custom{",
    `--color-page:${t.page};--color-surface:${t.surface};--color-input:${t.input};`,
    `--color-elevated:${t.elevated};--color-hover:${t.hover};--color-inset:${t.inset};`,
    `--color-heading:${t.heading};--color-body:${t.body};--color-label:${t.label};`,
    `--color-dim:${t.dim};--color-faint:${t.faint};--color-ghost:${t.ghost};`,
    `--color-divider:${t.divider};--color-edge:${t.edge};--color-ring-hover:${t.ringHover};`,
    `--color-hint:${t.hint};--color-shadow:${t.shadow};--color-skel:${t.skel};`,
    `--color-glass:${t.glass};--color-glass-nav:${t.glassNav};--color-glass-edge:${t.glassEdge};`,
    `--color-bubble:${t.bubble};--color-bubble-rgb:${t.bubbleRgb};`,
    `--color-bubble-ink:${t.bubbleInk};--color-bubble-ink-rgb:${t.bubbleInkRgb};`,
    `--color-bubble-dim:${t.bubbleDim};`,
    "}",
    "@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))){",
    `html.theme-custom{--color-glass:${t.glassRgba};--color-glass-nav:${t.glassNavRgba};}`,
    "}",
  ].join("");
}

// Create/update the injected <style>. Prepend so the bundled stylesheet
// stays later in source order — that's what lets html.eink / html.no-glass
// (same specificity) still beat html.theme-custom.
function ensureCustomStyle(css) {
  if (typeof document === "undefined") return;
  let el = document.getElementById("xy-custom-theme");
  if (!el) {
    el = document.createElement("style");
    el.id = "xy-custom-theme";
    document.head.prepend(el);
  }
  // No arg → rebuild from config rather than trusting the stored CSS: a
  // schema change (e.g. new bubble tokens) would otherwise stick around
  // until the user next edits a color. Keep the pre-paint copy fresh too.
  let text = css;
  if (text == null) {
    text = buildCustomCss(getCustomConfig());
    try { localStorage.setItem(CUSTOM_CSS_KEY, text); } catch { /* blocked */ }
  }
  if (el.textContent !== text) el.textContent = text;
}

export function saveCustomConfig(cfg) {
  const css = buildCustomCss(cfg);
  try {
    localStorage.setItem(CUSTOM_KEY, JSON.stringify(cfg));
    localStorage.setItem(CUSTOM_CSS_KEY, css);
  } catch { /* private mode */ }
  ensureCustomStyle(css);
  // Re-select so a base flip (dark custom → light custom) re-resolves the
  // .dark class, palette bookkeeping, and meta color.
  if (getActivePaletteId() === "custom") selectPalette("custom");
}

/* ── palette resolution / application ── */

export function getBase() {
  const stored = localStorage.getItem("xylocopa-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// Validated palette id for a base; falls back to the base default.
export function getPaletteId(base) {
  let id;
  try { id = localStorage.getItem(PALETTE_KEY(base)); } catch { /* blocked */ }
  if (id === "custom") return getCustomConfig().base === base ? "custom" : base;
  const preset = PRESETS.find((p) => p.id === id);
  return preset && preset.base === base ? id : base;
}

export function getActivePaletteId() {
  return getPaletteId(getBase());
}

function pageColorOf(id) {
  if (id === "custom") return getCustomConfig().colors.page;
  return (PRESETS.find((p) => p.id === id) || PRESETS[0]).preview.page;
}

// Sync <html> classes + theme-color meta to the palette stored for `base`.
// Idempotent; called from useTheme's effect and from selectPalette.
export function applyPalette(base) {
  const root = document.documentElement;
  root.classList.toggle("dark", base === "dark");
  const id = getPaletteId(base);
  for (const c of [...root.classList]) {
    if (c.startsWith("theme-")) root.classList.remove(c);
  }
  if (id !== "light" && id !== "dark") root.classList.add(`theme-${id}`);
  if (id === "custom") ensureCustomStyle();
  const pageColor = pageColorOf(id);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", pageColor);
  try { localStorage.setItem(META_COLOR_KEY(base), pageColor); } catch { /* blocked */ }
}

// User picked a palette: remember it for its base, switch to that base,
// apply, and broadcast so every useTheme instance / settings UI syncs.
export function selectPalette(id) {
  const base = id === "custom"
    ? getCustomConfig().base
    : (PRESETS.find((p) => p.id === id) || PRESETS[0]).base;
  try {
    localStorage.setItem(PALETTE_KEY(base), id);
    localStorage.setItem("xylocopa-theme", base);
  } catch { /* blocked */ }
  applyPalette(base);
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: { base } }));
}
