// SVG popover arrow (caret pointing up, base sits on the popover's top edge).
//
// Replaces the older rotated-square pattern (`w-2 h-2 bg-surface border-l
// border-t rotate-45`). That pattern had a seam in e-ink mode because the
// `.bg-surface` rule adds a 1px border on ALL FOUR sides — visible as the
// bottom-left/right diagonal edges of the diamond. Using an SVG triangle
// avoids that class entirely.
//
// Seam alignment: V stroke endpoints sit at the *middle* of the popover's
// 1px top border (parent y=0.5), so the 1px-wide stroke spans parent y=0..1
// and visually merges with the border line. The fill polygon includes a
// full-width horizontal strip at the border level so the border can't peek
// through the SVG's empty corners outside the V triangle.
//
// Colors come from CSS variables that are already overridden in e-ink
// (see index.css `.popover-arrow-*` rules).
//
// Positioning: defaults to horizontally centered. Pass align="right"
// (or "left") + offset for popovers anchored to one side.
export default function PopoverArrow({ size = 12, align = "center", offset = 12 }) {
  const w = size;
  const h = Math.round(size * 0.6);
  let positionStyle;
  if (align === "right") positionStyle = { right: offset };
  else if (align === "left") positionStyle = { left: offset };
  else positionStyle = { left: "50%", transform: "translateX(-50%)" };
  return (
    <svg
      width={w}
      height={h + 1}
      viewBox={`0 0 ${w} ${h + 1}`}
      className="absolute pointer-events-none"
      style={{ top: -h, overflow: "visible", ...positionStyle }}
      aria-hidden="true"
    >
      {/* Fill polygon: V interior + full-width strip at the popover border
          level (SVG y=h..h+1, parent y=0..1) so the border under the SVG
          can't peek through the corners outside the V triangle. */}
      <path
        className="popover-arrow-fill"
        d={`M0 ${h + 1} L0 ${h} L${w / 2} 0 L${w} ${h} L${w} ${h + 1} Z`}
      />
      {/* V stroke endpoints at SVG y=h+0.5 (parent y=0.5, middle of the
          popover's 1px top border) — the centered 1px stroke spans parent
          y=0..1, matching the border line for a seamless corner join. */}
      <path
        className="popover-arrow-stroke"
        d={`M0 ${h + 0.5} L${w / 2} 0 L${w} ${h + 0.5}`}
        fill="none"
        strokeWidth="1"
        strokeLinejoin="miter"
      />
    </svg>
  );
}
