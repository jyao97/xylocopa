import { useId } from "react";

import {
  DEFAULT_CHARACTER,
  paletteVars,
  resolveFill,
} from "./attention/characters";

/**
 * The attention assistant's face — a modern-emoji ball, skinnable.
 *
 * Styling follows the Fluent/Noto 😊 school: thick rounded strokes, soft
 * blush, a pale-core → deep-rim body gradient. A `character` prop reskins
 * it (palette, add-on paths like ears/whiskers, nose, mouth-shape
 * overrides) while the living face system — blink, pupil tracking, the
 * mood set — is shared by every character. Characters are DATA (validated
 * server-side for generated ones): paths render only as `d` attributes
 * with fixed presentation, never as raw markup.
 *
 * One `mood` prop drives the expression; an optional one-shot `gesture`
 * ("hop") plays on top of it. All continuous motion is CSS (see the
 * "Attention orb" block in index.css): this component is mounted on every
 * route in the app, so it owns no timers and no rAF loop. The only script
 * involvement is the pupil tracking, and even that lives in the parent —
 * it writes --orb-px/--orb-py onto the FAB element and the pupils here
 * just inherit the vars.
 *
 * The default character keeps colors on the --color-orb* tokens (same in
 * light and dark — emoji don't theme-shift); custom palettes are inline
 * vars scoped to this svg, which e-ink CSS overrides with !important so
 * every skin still degrades to black/white line art. Every mood must stay
 * legible with all animation removed — `prefers-reduced-motion` and
 * `html.eink` both strip it — which is why each mood changes the *shape*
 * of the eyes and mouth rather than relying on movement to read.
 *
 * moods:
 *   idle      blink + breathe + smile        nothing pending
 *   unread    glow + pulse ring + "o!"       messages waiting
 *   thinking  eyes up-left + spinning arc    assistant is working
 *   done      😊 — ^^ eyes + big smile       something succeeded
 *   speak     open chewing mouth + bob       reading a message out
 *   error     x-eyes + frown + nudge         something failed
 *   dragging  eyes shifted, no blink         being repositioned
 */

const MOODS = new Set([
  "idle", "unread", "thinking", "done", "speak", "error", "dragging",
]);

// The face is drawn once at 52×52 and scaled by the wrapper, so all the
// geometry below is in that fixed coordinate space. Stroke weights sit
// near 3 — the reference emojis' ~30px strokes on a 512 canvas.
const EYE_L = 18;
const EYE_R = 34;
const EYE_Y = 24;
const STROKE = 3.1;

function Pupil({ cx, cy, rx, ry, blinkClass }) {
  // Pupil + catchlights share one group so the sparkle blinks with the lid.
  return (
    <g className={blinkClass}>
      <ellipse cx={cx} cy={cy} rx={rx} ry={ry}
        fill="var(--color-orb-face)" stroke="none" />
      <circle cx={cx - 0.9} cy={cy - 1.6} r="1"
        fill="var(--color-orb-spark)" stroke="none" />
      <circle cx={cx + 1} cy={cy + 1.4} r="0.5"
        fill="var(--color-orb-spark)" opacity="0.7" stroke="none" />
    </g>
  );
}

// The ^^ smiling eye — the reference emoji's signature stroke.
function SmileEye({ cx }) {
  return <path d={`M${cx - 4} 25.5 Q${cx} 20.6 ${cx + 4} 25.5`} />;
}

function Eyes({ mood, wander }) {
  if (mood === "thinking") {
    // Gazing up and to the side — "hmm". Static pupils (no track/wander):
    // the pose IS the expression, and it must hold while frozen.
    return (
      <>
        <Pupil cx={EYE_L + 1.6} cy={EYE_Y - 2.2} rx={2.7} ry={4} />
        <Pupil cx={EYE_R + 1.6} cy={EYE_Y - 2.2} rx={2.7} ry={4} />
      </>
    );
  }
  if (mood === "done") {
    return (
      <g fill="none" stroke="var(--color-orb-face)" strokeWidth={STROKE}
        strokeLinecap="round">
        <SmileEye cx={EYE_L} />
        <SmileEye cx={EYE_R} />
      </g>
    );
  }
  if (mood === "error") {
    return (
      <g fill="none" stroke="var(--color-orb-face)" strokeWidth="2.6"
        strokeLinecap="round">
        <path d={`M${EYE_L - 2.8} 21.5 l5.6 5 M${EYE_L + 2.8} 21.5 l-5.6 5`} />
        <path d={`M${EYE_R - 2.8} 21.5 l5.6 5 M${EYE_R + 2.8} 21.5 l-5.6 5`} />
      </g>
    );
  }

  // Round pupil eyes. Dragging shifts them sideways (looking where it's
  // going) and drops the blink so the face stays steady while being moved.
  const shift = mood === "dragging" ? 3 : 0;
  const blink = mood === "dragging" ? "" : "orb-eye";
  // Unread widens the eyes — reads as "oh!" even with motion off.
  const grow = mood === "unread" ? 0.5 : 0;
  const pupils = (
    <>
      <Pupil cx={EYE_L + shift} cy={EYE_Y - grow} rx={2.9 + grow} ry={4.7 + grow}
        blinkClass={blink} />
      <Pupil cx={EYE_R + shift} cy={EYE_Y - grow} rx={2.9 + grow} ry={4.7 + grow}
        blinkClass={blink ? `${blink} orb-eye-b` : ""} />
    </>
  );
  // Two nested groups: the outer follows the cursor (CSS vars written by
  // the parent), the inner wanders on its own when there is no cursor to
  // follow. Separate layers because both are `transform` and would clobber
  // each other on one element.
  return (
    <g className="orb-pupils">
      {wander ? <g className="orb-wander">{pupils}</g> : pupils}
    </g>
  );
}

// Stroked mouth for one mood; characters may override the path shape for
// the stroked moods only — the open speak/unread mouths stay fixed.
function StrokedMouth({ d, width }) {
  return <path d={d} fill="none" stroke="var(--color-orb-face)"
    strokeWidth={width} strokeLinecap="round" />;
}

function Mouth({ mood, overrides }) {
  const od = overrides?.[mood];
  if (mood === "unread") {
    // Small open "o" — surprised that something arrived.
    return <circle cx="26" cy="33.8" r="3"
      fill="var(--color-orb-face)" stroke="none" />;
  }
  if (mood === "thinking") {
    return <StrokedMouth d={od || "M22 34 h8"} width={2.6} />;
  }
  if (mood === "done") {
    // The reference's wide, generous arc.
    return <StrokedMouth d={od || "M17.5 30 Q26 39 34.5 30"} width={STROKE} />;
  }
  if (mood === "speak") {
    // Open mouth; orb-talk chews it while speaking.
    return <ellipse className="orb-talk" cx="26" cy="33.8" rx="3.6" ry="3"
      fill="var(--color-orb-face)" stroke="none" />;
  }
  if (mood === "error") {
    // Inverted curve — a frown.
    return <StrokedMouth d={od || "M20.5 35.5 Q26 31.5 31.5 35.5"} width={2.6} />;
  }
  if (mood === "dragging") {
    return <StrokedMouth d={od || "M22.5 33 Q28 35.5 33.5 32.5"} width={2.6} />;
  }
  return <StrokedMouth d={od || "M20 31 Q26 37.8 32 31"} width={STROKE} />;
}

function Extras({ items }) {
  if (!items?.length) return null;
  return items.map((e, i) => (
    <path
      key={i}
      d={e.d}
      fill={resolveFill(e.fill)}
      stroke={e.stroke ? resolveFill(e.stroke) : "none"}
      strokeWidth={e.strokeWidth || 0}
      strokeLinecap="round"
      opacity={e.opacity ?? 1}
    />
  ));
}

export default function AttentionOrb({
  mood = "idle",
  badge = null,
  className = "w-11 h-11",
  // Character skin (see attention/characters.js). Default: token-driven.
  character = null,
  // One-shot gesture layered over the mood. Bump gestureKey to replay.
  gesture = null,
  gestureKey = 0,
  // Autonomous glances for viewports with no cursor to track. The parent
  // turns this off while it is writing tracking vars.
  wander = false,
}) {
  const m = MOODS.has(mood) ? mood : "idle";
  const c = character || DEFAULT_CHARACTER;
  // Gradient ids must be per-instance: the character picker renders many
  // orbs with different palettes at once, and shared ids would make every
  // preview paint with the first one's gradient.
  const uid = useId();
  const fillId = `orb-fill-${uid}`;
  const blushId = `orb-blush-${uid}`;

  const behind = c.extras?.filter((e) => e.behind);
  const front = c.extras?.filter((e) => !e.behind);

  // Only idle breathes. The other moods either have their own motion
  // (unread glows, speak bobs, thinking spins) or are transient states
  // where a second overlapping animation reads as noise.
  const bodyClass = [
    m === "idle" ? "orb-breathe" : "",
    m === "unread" ? "orb-glow" : "",
  ].filter(Boolean).join(" ");

  return (
    <svg
      viewBox="0 0 52 52"
      className={`attn-orb ${className} ${m === "error" ? "orb-nudge" : ""} block overflow-visible`}
      style={paletteVars(c.palette)}
      // Decorative: the surrounding button carries the accessible label,
      // so announcing the face again would just be noise.
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {/* Soft-3D body: pale top-light core deepening toward the rim —
            the modern-emoji shading, no hard specular. Plain custom
            properties per stop (color-mix() is unreliable inside SVG
            presentation attributes); all three collapse to one flat color
            under e-ink. */}
        <radialGradient id={fillId} cx="35%" cy="27%" r="82%">
          <stop offset="0%" stopColor="var(--color-orb-hi)" />
          <stop offset="52%" stopColor="var(--color-orb)" />
          <stop offset="100%" stopColor="var(--color-orb-lo)" />
        </radialGradient>
        {/* Airbrushed cheeks: pink fading to nothing, like the reference —
            a hard-edged ellipse here is what made the old blush read as
            shading instead of blush. */}
        <radialGradient id={blushId}>
          <stop offset="0%" stopColor="var(--color-orb-blush)" stopOpacity="0.5" />
          <stop offset="70%" stopColor="var(--color-orb-blush)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--color-orb-blush)" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* Expanding ring behind the body — unread only. */}
      {m === "unread" && (
        <circle
          className="orb-ring"
          cx="26" cy="26" r="21"
          fill="none" stroke="var(--color-attn)" strokeWidth="2"
        />
      )}

      {/* Sweeping arc — thinking only. Sits outside the body radius, and
          outside the gesture group so a hop can't wobble the orbit. */}
      {m === "thinking" && (
        <circle
          className="orb-arc"
          cx="26" cy="26" r="24"
          fill="none" stroke="var(--color-attn)" strokeWidth="2"
          strokeDasharray="26 90" strokeLinecap="round"
        />
      )}

      {/* key restarts the hop animation on every gestureKey bump. */}
      <g key={gestureKey} className={gesture === "hop" ? "orb-hop" : undefined}>
        <g className={m === "speak" ? "orb-bob" : undefined}>
          {/* Ears, hair, anything tucked behind the ball. */}
          <Extras items={behind} />
          <circle
            className={bodyClass}
            cx="26" cy="26" r="21"
            fill={`url(#${fillId})`}
          />
          {/* Blush sits behind the features, slightly low and wide. */}
          <g className="orb-blush">
            <circle cx="11.5" cy="30.5" r="4.6" fill={`url(#${blushId})`} />
            <circle cx="40.5" cy="30.5" r="4.6" fill={`url(#${blushId})`} />
          </g>

          {/* Whiskers, spots — in front of the ball, behind the face. */}
          <Extras items={front} />

          <Eyes mood={m} wander={wander} />
          {c.nose && (
            <path d={c.nose.d} fill={resolveFill(c.nose.fill)} stroke="none" />
          )}
          <Mouth mood={m} overrides={c.mouths} />
        </g>
      </g>

      {badge != null && badge !== "" && (
        <g>
          <circle
            cx="41" cy="11" r="8.5"
            fill="var(--color-attn)"
            stroke="var(--color-page)" strokeWidth="1.6"
          />
          <text
            x="41" y="14.4"
            textAnchor="middle"
            fontSize={String(badge).length > 2 ? "7.5" : "9"}
            fontWeight="800"
            fill="var(--color-attn-ink)"
          >
            {badge}
          </text>
        </g>
      )}
    </svg>
  );
}
