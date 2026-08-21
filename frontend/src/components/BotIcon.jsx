import beeLogo from "../assets/xylocopa-bee.svg";

/**
 * Xylocopa bee logo inside a liquid-glass badge (reuses .glass-bar).
 * state: "idle" | "running" | "error" | "planning" | "completed"
 *
 * The bee art is a fixed-color pixel SVG; `state` drives the surrounding
 * glow so status is still readable at a glance.
 */
const STATE_GLOW = {
  idle: "",
  running:
    "shadow-[0_0_16px_rgb(var(--color-accent-rgb)/0.55),inset_0_0.5px_0_var(--color-glass-edge)] animate-glow",
  error:
    "shadow-[0_0_14px_rgba(248,113,113,0.5),inset_0_0.5px_0_var(--color-glass-edge)]",
  planning:
    "shadow-[0_0_14px_rgba(251,191,36,0.5),inset_0_0.5px_0_var(--color-glass-edge)]",
  completed:
    "shadow-[0_0_10px_rgba(52,211,153,0.35),inset_0_0.5px_0_var(--color-glass-edge)]",
};

export default function BotIcon({ state = "idle", className = "w-9 h-9" }) {
  const glow = STATE_GLOW[state] || "";
  return (
    <div
      className={`${className} glass-bar relative shrink-0 inline-flex items-center justify-center rounded-full overflow-hidden ${glow}`}
    >
      <img
        src={beeLogo}
        alt="Xylocopa"
        className="relative z-10 w-[78%] h-[78%] object-contain select-none"
        draggable={false}
      />
    </div>
  );
}
