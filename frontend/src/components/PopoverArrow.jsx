// SVG popover arrow (caret pointing up, base sits on the popover's top edge).
//
// Replaces the older rotated-square pattern (`w-2 h-2 bg-surface border-l
// border-t rotate-45`). That pattern had a seam in e-ink mode because the
// `.bg-surface` rule adds a 1px border on ALL FOUR sides — visible as the
// bottom-left/right diagonal edges of the diamond. Using an SVG triangle
// avoids that class entirely. The fill is +1px taller than the visible
// triangle so it bleeds over the popover's top border, hiding the seam.
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
      style={{ top: -h, ...positionStyle }}
      aria-hidden="true"
    >
      <path
        className="popover-arrow-fill"
        d={`M0 ${h + 1} L${w / 2} 0 L${w} ${h + 1} Z`}
      />
      <path
        className="popover-arrow-stroke"
        d={`M0 ${h} L${w / 2} 0 L${w} ${h}`}
        fill="none"
        strokeWidth="1"
        strokeLinejoin="miter"
      />
    </svg>
  );
}
