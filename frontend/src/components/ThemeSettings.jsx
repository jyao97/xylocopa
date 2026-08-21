import { useEffect, useState, useCallback } from "react";
import {
  PRESETS, CUSTOM_SEEDS, CUSTOM_COLOR_FIELDS, THEME_EVENT,
  getActivePaletteId, selectPalette, getCustomConfig, saveCustomConfig,
  hasCustomConfig, customConfigFromPreset,
} from "../lib/themes";

// Monitor > Display > Theme: preset palette picker + custom-theme editor.
// `theme` is the resolved base from useTheme (via App), used to re-derive
// the active palette when the header sun/moon toggle flips the base.

function Swatch({ page, surface, text, edge }) {
  return (
    <div
      className="w-full h-9 rounded-md border overflow-hidden flex items-center gap-1.5 px-2"
      style={{ backgroundColor: page, borderColor: edge || "var(--color-edge)" }}
    >
      <span className="w-3.5 h-3.5 rounded-full shrink-0" style={{ backgroundColor: surface, border: `1px solid ${text}22` }} />
      <span className="h-1.5 rounded-full flex-1" style={{ backgroundColor: surface }} />
      <span className="h-1.5 w-5 rounded-full shrink-0" style={{ backgroundColor: text, opacity: 0.85 }} />
    </div>
  );
}

function PaletteCard({ name, base, active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active ? "true" : "false"}
      className={`text-left rounded-lg p-2 border transition-colors ${
        active
          ? "border-cyan-500 ring-1 ring-cyan-500"
          : "border-edge hover:border-ring-hover"
      }`}
    >
      {children}
      <div className="mt-1.5 flex items-center justify-between gap-1">
        <span className="text-xs font-medium text-body truncate">{name}</span>
        <span className="text-[10px] text-faint shrink-0">{base === "dark" ? "dark" : "light"}</span>
      </div>
    </button>
  );
}

export default function ThemeSettings({ theme }) {
  const [active, setActive] = useState(() => getActivePaletteId());
  const [custom, setCustom] = useState(() => getCustomConfig());
  const [editorOpen, setEditorOpen] = useState(() => getActivePaletteId() === "custom");

  // Re-resolve on base flips (header toggle) and palette picks made elsewhere.
  useEffect(() => { setActive(getActivePaletteId()); }, [theme]);
  useEffect(() => {
    const onChange = () => setActive(getActivePaletteId());
    window.addEventListener(THEME_EVENT, onChange);
    return () => window.removeEventListener(THEME_EVENT, onChange);
  }, []);

  const handleCustomChange = useCallback((next) => {
    setCustom(next);
    saveCustomConfig(next);
    if (getActivePaletteId() !== "custom") selectPalette("custom");
  }, []);

  // Track the preset the user came from, so the editor can offer it as a
  // starting point ("Copy from Nord") after they've switched to Custom.
  const [sourcePreset, setSourcePreset] = useState(null);

  const handlePickCustom = useCallback(() => {
    const current = getActivePaletteId();
    if (current !== "custom") {
      setSourcePreset(current);
      // First time customizing: start from what's on screen right now,
      // not from the stock light/dark palette.
      if (!hasCustomConfig()) {
        const seeded = customConfigFromPreset(current);
        setCustom(seeded);
        saveCustomConfig(seeded);
      }
    }
    selectPalette("custom");
    setEditorOpen(true);
  }, []);

  const handleCopyFromPreset = useCallback((id) => {
    const seeded = customConfigFromPreset(id);
    setCustom(seeded);
    saveCustomConfig(seeded);
  }, []);

  return (
    <section className="rounded-xl bg-surface shadow-card p-4">
      <h3 className="text-sm font-medium text-heading">Theme</h3>
      <p className="text-xs text-dim mt-1 leading-relaxed">
        Picking a palette also switches to its light/dark base. The sun/moon
        button in page headers flips between your chosen light and dark palettes.
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
        {PRESETS.map((p) => (
          <PaletteCard
            key={p.id}
            name={p.name}
            base={p.base}
            active={active === p.id}
            onClick={() => selectPalette(p.id)}
          >
            <Swatch page={p.preview.page} surface={p.preview.surface} text={p.preview.text} />
          </PaletteCard>
        ))}
        <PaletteCard
          name="Custom"
          base={custom.base}
          active={active === "custom"}
          onClick={handlePickCustom}
        >
          <Swatch
            page={custom.colors.page}
            surface={custom.colors.surface}
            text={custom.colors.heading}
            edge={custom.colors.edge}
          />
        </PaletteCard>
      </div>

      {/* Custom editor */}
      <div className="mt-3 border-t border-divider pt-3">
        <button
          type="button"
          onClick={() => setEditorOpen((v) => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-label hover:text-body"
        >
          <svg
            className={`w-3 h-3 transition-transform ${editorOpen ? "rotate-90" : ""}`}
            fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          Customize
        </button>

        {editorOpen && (
          <div className="mt-3 space-y-3">
            {/* Base picker */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-dim w-24 shrink-0">Base</span>
              <div className="flex rounded-lg bg-input p-0.5">
                {["light", "dark"].map((b) => (
                  <button
                    key={b}
                    type="button"
                    onClick={() => handleCustomChange({ base: b, colors: { ...CUSTOM_SEEDS[b] } })}
                    className={`px-3 py-1 text-xs rounded-md capitalize ${
                      custom.base === b ? "bg-elevated text-heading" : "text-dim"
                    }`}
                  >
                    {b}
                  </button>
                ))}
              </div>
              <span className="text-[10px] text-faint">controls dark-mode accents</span>
            </div>

            {/* Core colors; the remaining tokens are derived automatically. */}
            {CUSTOM_COLOR_FIELDS.map((f) => (
              <div key={f.key} className="flex items-center gap-2">
                <span className="text-xs text-dim w-24 shrink-0">{f.label}</span>
                <input
                  type="color"
                  value={custom.colors[f.key]}
                  aria-label={f.label}
                  onChange={(e) =>
                    handleCustomChange({ ...custom, colors: { ...custom.colors, [f.key]: e.target.value } })
                  }
                  className="w-8 h-8 rounded-md border border-edge bg-transparent cursor-pointer p-0.5"
                />
                <code className="text-xs text-label font-mono">{custom.colors[f.key]}</code>
              </div>
            ))}

            <div className="flex items-center gap-4">
              {sourcePreset && sourcePreset !== custom.base && (
                <button
                  type="button"
                  onClick={() => handleCopyFromPreset(sourcePreset)}
                  className="text-xs text-cyan-600 dark:text-cyan-400 hover:underline"
                >
                  Copy from {PRESETS.find((p) => p.id === sourcePreset)?.name}
                </button>
              )}
              <button
                type="button"
                onClick={() => handleCustomChange({ base: custom.base, colors: { ...CUSTOM_SEEDS[custom.base] } })}
                className="text-xs text-cyan-600 dark:text-cyan-400 hover:underline"
              >
                Reset to {custom.base} defaults
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
