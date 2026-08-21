// Built-in orb characters + the shared fill resolver.
//
// A character reskins the orb (palette, add-on paths, nose, mouth shapes)
// while the living face system — blink, pupil tracking, moods — stays
// shared. The shape mirrors the backend's validate_character() output;
// generated characters arrive already validated and slot in unchanged.

// The default keeps palette null so it renders from the --color-orb-*
// CSS tokens (and therefore keeps its dedicated e-ink degradation).
export const DEFAULT_CHARACTER = {
  id: "sunny",
  name: "Sunny",
  palette: null,
  extras: [],
  nose: null,
  mouths: {},
};

const MOCHI_CAT = {
  id: "mochi-cat",
  name: "Mochi",
  palette: {
    hi: "#ffd08a", body: "#ffb057", lo: "#ef8f33",
    face: "#4a2c15", spark: "#ffffff", blush: "#ff97ac",
  },
  extras: [
    { d: "M7.5 15 Q7 4.5 14.5 2.2 Q20.5 6.5 21 13 Q13.5 11.5 7.5 15 Z", fill: "body", behind: true },
    { d: "M44.5 15 Q45 4.5 37.5 2.2 Q31.5 6.5 31 13 Q38.5 11.5 44.5 15 Z", fill: "body", behind: true },
    { d: "M10.5 12.5 Q10.8 7.5 14.6 5.6 Q17.6 8.2 17.9 11.6 Q13.8 10.9 10.5 12.5 Z", fill: "blush", behind: true, opacity: 0.85 },
    { d: "M41.5 12.5 Q41.2 7.5 37.4 5.6 Q34.4 8.2 34.1 11.6 Q38.2 10.9 41.5 12.5 Z", fill: "blush", behind: true, opacity: 0.85 },
    { d: "M2.5 25.5 Q7.5 26 11.5 27", fill: "none", stroke: "face", strokeWidth: 1.5, opacity: 0.85 },
    { d: "M3 30.5 Q7.5 30.2 11.5 30", fill: "none", stroke: "face", strokeWidth: 1.5, opacity: 0.85 },
    { d: "M49.5 25.5 Q44.5 26 40.5 27", fill: "none", stroke: "face", strokeWidth: 1.5, opacity: 0.85 },
    { d: "M49 30.5 Q44.5 30.2 40.5 30", fill: "none", stroke: "face", strokeWidth: 1.5, opacity: 0.85 },
  ],
  nose: { d: "M24.1 29.8 L27.9 29.8 L26 32.3 Z", fill: "blush" },
  mouths: {
    idle: "M21 32.8 Q23.5 35.6 26 33 Q28.5 35.6 31 32.8",
    done: "M19.5 32 Q23 36.6 26 32.6 Q29 36.6 32.5 32",
  },
};

const BISCUIT_DOG = {
  id: "biscuit-dog",
  name: "Biscuit",
  palette: {
    hi: "#ffe9c2", body: "#f6c88b", lo: "#dfa05e",
    face: "#46301f", spark: "#ffffff", blush: "#ffa9a0",
  },
  extras: [
    // Floppy ears, hanging along the sides, painted behind the ball —
    // pushed well outside the circle edge so the droop actually shows.
    { d: "M9.5 5.5 Q-2.5 8.5 -3 21.5 Q-3 32 4.5 36 Q11.5 33 13 15 Q12.5 7.5 9.5 5.5 Z", fill: "lo", behind: true },
    { d: "M42.5 5.5 Q54.5 8.5 55 21.5 Q55 32 47.5 36 Q40.5 33 39 15 Q39.5 7.5 42.5 5.5 Z", fill: "lo", behind: true },
    // A little crown tuft.
    { d: "M23 6.5 Q26 2.5 29 6.5 Q27.5 8.5 26 8 Q24.5 8.5 23 6.5 Z", fill: "lo", behind: true },
  ],
  nose: { d: "M23.3 29.4 a2.7 2.1 0 1 0 5.4 0 a2.7 2.1 0 1 0 -5.4 0", fill: "face" },
  mouths: {
    idle: "M21 32.6 Q23.5 35.2 26 32.8 Q28.5 35.2 31 32.6",
  },
};

export const PRESET_CHARACTERS = [DEFAULT_CHARACTER, MOCHI_CAT, BISCUIT_DOG];

// Fill vocabulary → concrete paint. Palette-slot names resolve through the
// CSS vars so the DEFAULT character stays theme/e-ink aware; literal hexes
// pass through (validated upstream).
const SLOT_VAR = {
  body: "var(--color-orb)",
  hi: "var(--color-orb-hi)",
  lo: "var(--color-orb-lo)",
  face: "var(--color-orb-face)",
  spark: "var(--color-orb-spark)",
  blush: "var(--color-orb-blush)",
};

export function resolveFill(value) {
  if (!value || value === "none") return "none";
  return SLOT_VAR[value] || value;
}

// Inline CSS vars for a custom palette — scoped to the svg element so
// simultaneous previews with different palettes don't fight. E-ink CSS
// overrides these with !important (see index.css).
export function paletteVars(palette) {
  if (!palette) return undefined;
  return {
    "--color-orb": palette.body,
    "--color-orb-hi": palette.hi,
    "--color-orb-lo": palette.lo,
    "--color-orb-face": palette.face,
    "--color-orb-spark": palette.spark,
    "--color-orb-blush": palette.blush,
  };
}
