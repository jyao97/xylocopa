"""Attention-orb character generation — one strong-model call, validated.

A "character" reskins the orb while keeping its living face system (blink,
pupil tracking, the mood set) untouched: a palette, optional add-on paths
(ears, whiskers), an optional nose, and optional per-mood mouth overrides.
The model only ever produces DATA matching CHAR_SCHEMA_DOC; the frontend
renders paths exclusively as `d` attributes with fixed presentation
attributes, so there is no SVG-injection surface. validate_character() is
the single gate — the API refuses anything it cannot prove safe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess

logger = logging.getLogger("orchestrator.attention")

CHARGEN_TIMEOUT_SECONDS = 150
_CHARGEN_SLOTS = asyncio.Semaphore(1)

# Fills may reference palette slots by name (theme-consistent) or be a
# literal hex. Path data is restricted to plain SVG path commands.
FILL_VOCAB = {"body", "face", "blush", "hi", "lo", "spark", "none"}
PALETTE_KEYS = ("hi", "body", "lo", "face", "spark", "blush")
MOUTH_KEYS = {"idle", "done", "thinking", "dragging", "error"}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PATH_RE = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9,.\s+-]+$")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
SLUG_RE = re.compile(r"^[a-z0-9-]{2,24}$")

MAX_EXTRAS = 8
MAX_PATH_LEN = 500
MAX_MOUTH_LEN = 220
# Paths live in the 52×52 viewBox; ears may poke a little outside.
COORD_MIN, COORD_MAX = -30.0, 90.0


class CharGenError(RuntimeError):
    """The request could not be turned into a valid character."""


CHAR_SCHEMA_DOC = """\
{
  "id": "<slug, a-z0-9-, 2..24 chars>",
  "name": "<display name, <= 24 chars>",
  "palette": {
    "hi":    "#rrggbb   (body gradient: pale top-light core)",
    "body":  "#rrggbb   (body gradient: main color)",
    "lo":    "#rrggbb   (body gradient: deeper rim)",
    "face":  "#rrggbb   (eyes/mouth strokes — must contrast hard with body)",
    "spark": "#rrggbb   (eye catchlight — opposite pole of face)",
    "blush": "#rrggbb   (soft cheek tint)"
  },
  "extras": [
    {
      "d": "<SVG path data, plain commands only>",
      "fill": "body|face|blush|hi|lo|spark|none|#rrggbb",
      "stroke": "<same vocabulary, optional>",
      "strokeWidth": <0..6, optional>,
      "opacity": <0..1, optional>,
      "behind": <true = painted behind the ball body (ears), false = in front (whiskers, spots)>
    }
  ],
  "nose": { "d": "<path>", "fill": "<fill vocab>" } | null,
  "mouths": { "idle"|"done"|"thinking"|"dragging"|"error": "<path d, stroked by the renderer>" }
}"""

# The cat ships in the prompt as the style reference — a worked example
# beats prose for teaching coordinate conventions.
EXAMPLE_CHARACTER = {
    "id": "mochi-cat",
    "name": "Mochi",
    "palette": {
        "hi": "#ffd08a", "body": "#ffb057", "lo": "#ef8f33",
        "face": "#4a2c15", "spark": "#ffffff", "blush": "#ff97ac",
    },
    "extras": [
        {"d": "M7.5 15 Q7 4.5 14.5 2.2 Q20.5 6.5 21 13 Q13.5 11.5 7.5 15 Z",
         "fill": "body", "behind": True},
        {"d": "M44.5 15 Q45 4.5 37.5 2.2 Q31.5 6.5 31 13 Q38.5 11.5 44.5 15 Z",
         "fill": "body", "behind": True},
        {"d": "M10.5 12.5 Q10.8 7.5 14.6 5.6 Q17.6 8.2 17.9 11.6 Q13.8 10.9 10.5 12.5 Z",
         "fill": "blush", "behind": True, "opacity": 0.85},
        {"d": "M41.5 12.5 Q41.2 7.5 37.4 5.6 Q34.4 8.2 34.1 11.6 Q38.2 10.9 41.5 12.5 Z",
         "fill": "blush", "behind": True, "opacity": 0.85},
        {"d": "M2.5 25.5 Q7.5 26 11.5 27", "fill": "none", "stroke": "face",
         "strokeWidth": 1.5, "opacity": 0.85},
        {"d": "M3 30.5 Q7.5 30.2 11.5 30", "fill": "none", "stroke": "face",
         "strokeWidth": 1.5, "opacity": 0.85},
        {"d": "M49.5 25.5 Q44.5 26 40.5 27", "fill": "none", "stroke": "face",
         "strokeWidth": 1.5, "opacity": 0.85},
        {"d": "M49 30.5 Q44.5 30.2 40.5 30", "fill": "none", "stroke": "face",
         "strokeWidth": 1.5, "opacity": 0.85},
    ],
    "nose": {"d": "M24.1 29.8 L27.9 29.8 L26 32.3 Z", "fill": "blush"},
    "mouths": {
        "idle": "M21 32.8 Q23.5 35.6 26 33 Q28.5 35.6 31 32.8",
        "done": "M19.5 32 Q23 36.6 26 32.6 Q29 36.6 32.5 32",
    },
}

CHARGEN_RULES = f"""\
You design a CHARACTER SKIN for a small animated assistant ball ("orb") in
a developer tool. The orb has a living face — blinking pupil eyes that
track the cursor, a smiling-eyes happy pose, a talking mouth — and that
face system is FIXED. Your job is only the skin: colors, add-on shapes
(ears, whiskers, hair, antennae, spots), an optional nose, and optional
mouth-shape overrides. Output ONLY one JSON object. No prose, no fences.

SCHEMA:
{CHAR_SCHEMA_DOC}

CANVAS RULES (all coordinates in a 52×52 viewBox):
1. The body is a fixed circle: center (26,26), radius 21. You do NOT draw
   it — pick its gradient via palette hi/body/lo (pale core → deeper rim,
   soft modern-emoji shading).
2. The eyes are fixed dark ovals around (18,24) and (34,24) spanning
   roughly x 13..39, y 18..30, with the mouth around (26,33). NEVER cover
   that region with a filled extra in front (behind:true is safe; thin
   front strokes like whiskers may pass beside it, x<13 or x>39).
3. Ears/hair go BEHIND the body (behind:true) and should tuck at least
   4 units under the circle edge so no seam shows. Keep every coordinate
   within -20..72.
4. face color must contrast strongly against body (eyes must read at
   44px). spark is the catchlight inside the pupils — near-white for dark
   faces. blush is a soft warm tint, used as airbrushed cheek circles.
5. Mouth overrides are stroked (round caps, width ~3) by the renderer —
   give bare path data only, roughly within x 18..34, y 29..38. Provide
   "idle" (small resting) and "done" (big happy) when the species has a
   signature mouth (cat ω, etc.); omit "mouths" entirely to keep the
   default smiles. The open speak/surprised mouths cannot be overridden.
6. At most {MAX_EXTRAS} extras. Simple, chunky, kawaii shapes — this
   renders at 44px. No text, no realistic detail, nothing outside the
   character (no backgrounds, no accessories floating off-body).
7. STRICT JSON: no comments, no trailing commas, every palette value a
   6-digit hex like "#aabbcc". Your entire reply must be the one object.
8. Style reference (the built-in cat) — match its level of simplicity:
{json.dumps(EXAMPLE_CHARACTER, indent=2)}
"""


def _resolve_fill(value, palette_ok=True):
    if value is None:
        return None
    if not isinstance(value, str):
        raise CharGenError("fills must be strings")
    v = value.strip()
    if v in FILL_VOCAB:
        return v
    if HEX_RE.match(v):
        return v.lower()
    raise CharGenError(f"invalid fill {value!r} — use {'/'.join(sorted(FILL_VOCAB))} or #rrggbb")


def _check_path(d, max_len, what):
    if not isinstance(d, str) or not d.strip():
        raise CharGenError(f"{what}: path data missing")
    d = d.strip()
    if len(d) > max_len:
        raise CharGenError(f"{what}: path data too long ({len(d)} > {max_len})")
    if not PATH_RE.match(d):
        raise CharGenError(f"{what}: path data may only contain plain SVG path commands")
    for num in NUM_RE.findall(d):
        n = float(num)
        if n < COORD_MIN or n > COORD_MAX:
            raise CharGenError(f"{what}: coordinate {n} outside {COORD_MIN}..{COORD_MAX}")
    return d


def validate_character(obj) -> dict:
    """Normalize and validate a character definition. Raises CharGenError.

    Everything the frontend renders passes through here — treat any hole
    in this function as an XSS surface.
    """
    if not isinstance(obj, dict):
        raise CharGenError("character must be a JSON object")

    slug = str(obj.get("id") or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise CharGenError("id must be a short a-z0-9- slug")
    name = str(obj.get("name") or "").strip()
    if not name or len(name) > 24:
        raise CharGenError("name must be 1..24 chars")

    palette_in = obj.get("palette")
    if not isinstance(palette_in, dict):
        raise CharGenError("palette object is required")
    palette = {}
    for key in PALETTE_KEYS:
        v = str(palette_in.get(key) or "").strip()
        if not HEX_RE.match(v):
            raise CharGenError(f"palette.{key} must be #rrggbb")
        palette[key] = v.lower()

    extras_in = obj.get("extras") or []
    if not isinstance(extras_in, list) or len(extras_in) > MAX_EXTRAS:
        raise CharGenError(f"extras must be a list of at most {MAX_EXTRAS}")
    extras = []
    for i, e in enumerate(extras_in):
        if not isinstance(e, dict):
            raise CharGenError(f"extras[{i}] must be an object")
        item = {
            "d": _check_path(e.get("d"), MAX_PATH_LEN, f"extras[{i}]"),
            "fill": _resolve_fill(e.get("fill") or "body"),
            "behind": bool(e.get("behind", False)),
        }
        if e.get("stroke") is not None:
            item["stroke"] = _resolve_fill(e.get("stroke"))
        sw = e.get("strokeWidth")
        if sw is not None:
            sw = float(sw)
            if not (0 <= sw <= 6):
                raise CharGenError(f"extras[{i}].strokeWidth must be 0..6")
            item["strokeWidth"] = sw
        op = e.get("opacity")
        if op is not None:
            op = float(op)
            if not (0 <= op <= 1):
                raise CharGenError(f"extras[{i}].opacity must be 0..1")
            item["opacity"] = op
        extras.append(item)

    nose_in = obj.get("nose")
    nose = None
    if nose_in:
        if not isinstance(nose_in, dict):
            raise CharGenError("nose must be an object or null")
        nose = {
            "d": _check_path(nose_in.get("d"), MAX_MOUTH_LEN, "nose"),
            "fill": _resolve_fill(nose_in.get("fill") or "face"),
        }

    mouths_in = obj.get("mouths") or {}
    if not isinstance(mouths_in, dict):
        raise CharGenError("mouths must be an object")
    mouths = {}
    for key, d in mouths_in.items():
        if key not in MOUTH_KEYS:
            raise CharGenError(f"mouths.{key} is not overridable")
        mouths[key] = _check_path(d, MAX_MOUTH_LEN, f"mouths.{key}")

    return {
        "id": slug,
        "name": name,
        "palette": palette,
        "extras": extras,
        "nose": nose,
        "mouths": mouths,
    }


def _claude_p(prompt: str) -> tuple[int, str, str]:
    from config import ATTENTION_CHARGEN_MODEL, CLAUDE_BIN
    from route_helpers import subprocess_clean_env

    proc = subprocess.run(
        [CLAUDE_BIN, "-p", "-", "--output-format", "text",
         "--no-session-persistence", "--model", ATTENTION_CHARGEN_MODEL],
        input=prompt,
        capture_output=True, text=True,
        timeout=CHARGEN_TIMEOUT_SECONDS,
        cwd="/tmp",
        env=subprocess_clean_env(),
    )
    return proc.returncode, proc.stdout, proc.stderr


async def generate_character(text: str) -> dict:
    """One strong-model call → validated character. Raises CharGenError."""
    from attention.compiler import _extract_json

    text = (text or "").strip()
    if not text:
        raise CharGenError("describe the character you want")
    if len(text) > 300:
        raise CharGenError("keep the description under 300 characters")

    prompt = f"{CHARGEN_RULES}\nUSER REQUEST\n{text}\n"

    # One free retry: a strong model occasionally emits malformed JSON
    # (comments, truncation); the second attempt almost always lands, and
    # the user has already committed to a ~minute-long wait.
    last_error = None
    for attempt in (1, 2):
        async with _CHARGEN_SLOTS:
            try:
                rc, out, err = await asyncio.to_thread(_claude_p, prompt)
            except subprocess.TimeoutExpired:
                raise CharGenError(
                    f"generation timed out after {CHARGEN_TIMEOUT_SECONDS}s — try again"
                )
            except FileNotFoundError:
                raise CharGenError("the claude CLI is not available on this host")

        if rc != 0:
            logger.warning("attention chargen failed rc=%d: %s", rc, (err or "")[:300])
            raise CharGenError("the designer model could not be reached — try again")

        try:
            character = validate_character(_extract_json(out))
            logger.info(
                "attention: generated character %r (%s, %d extras) on attempt %d",
                character["name"], character["id"], len(character["extras"]), attempt,
            )
            return character
        except (CharGenError, Exception) as exc:
            last_error = exc
            logger.warning(
                "attention chargen attempt %d unusable: %s | raw head: %r",
                attempt, exc, (out or "")[:400],
            )

    raise CharGenError(f"the design did not validate: {last_error}")
