#!/usr/bin/env python3
"""Diff local slash-command catalog against the official Claude Code docs.

Source of truth: https://code.claude.com/docs/en/commands.md  (Mintlify serves
raw markdown at the `.md` URL; the page lists every shipped command and tags
bundled skills with `**[Skill]...**`, removed commands with `Removed in vX.Y.Z`.)

Local data scanned:
  - orchestrator/slash_commands.py  (COMMANDS, KNOWN_PROBLEMATIC)
  - orchestrator/skills.py          (BUNDLED_SKILLS)

Usage:
  python tools/sync_slash_commands.py            # color stdout, default
  python tools/sync_slash_commands.py --plain    # no ANSI (CI-friendly)

Exit code:
  0  — local catalog matches docs
  1  — drift detected (new commands, removed-but-still-tracked, skill-tag drift,
       or local entries no longer in docs)
"""

import re
import sys
import urllib.request
from pathlib import Path

DOCS_URL = "https://code.claude.com/docs/en/commands.md"
REPO_ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = REPO_ROOT / "orchestrator"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty() and "--plain" not in sys.argv

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text

def GREEN(s): return _c(s, "32")
def RED(s): return _c(s, "31")
def YELLOW(s): return _c(s, "33")
def DIM(s): return _c(s, "2")
def BOLD(s): return _c(s, "1")


# ---------------------------------------------------------------------------
# Docs fetch + parse
# ---------------------------------------------------------------------------

# Matches a table row like:  | `/cmd [arg]` | body... |
_ROW_RE = re.compile(
    r"^\|\s*`(?P<cmd>/[a-z][\w-]*)(?P<args>[^`]*)`\s*\|\s*(?P<body>.+?)\s*\|\s*$",
    re.MULTILINE,
)
_REMOVED_RE = re.compile(r"Removed in v(\d+\.\d+\.\d+)")
_SKILL_TAG_RE = re.compile(r"\*\*\[Skill\]")


def fetch_docs() -> str:
    req = urllib.request.Request(
        DOCS_URL, headers={"User-Agent": "xylocopa-sync-slash-commands"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def parse_docs(md: str) -> dict[str, dict]:
    """Return {cmd: {args_form, description, is_skill, removed_in}}."""
    out: dict[str, dict] = {}
    for m in _ROW_RE.finditer(md):
        cmd = m.group("cmd")
        body = m.group("body")
        rm = _REMOVED_RE.search(body)
        desc = re.sub(r"\{/\*[^}]*\*/\}", "", body)            # drop max-version comments
        desc = re.sub(r"Removed in v\d+\.\d+\.\d+\.\s*", "", desc)
        desc = re.sub(r"\*\*\[Skill\][^*]*\*\*\s*", "", desc)  # drop [Skill] badge
        out[cmd] = {
            "args_form": m.group("args").strip(),
            "description": desc.strip(),
            "is_skill": bool(_SKILL_TAG_RE.search(body)),
            "removed_in": rm.group(1) if rm else None,
        }
    return out


# ---------------------------------------------------------------------------
# Local catalog load
# ---------------------------------------------------------------------------

def load_local() -> dict:
    sys.path.insert(0, str(ORCH_DIR))
    import slash_commands as sc  # noqa: E402
    import skills as sk          # noqa: E402
    return {
        "commands": dict(sc.COMMANDS),
        "problematic": set(sc.KNOWN_PROBLEMATIC),
        "bundled_skills": {b["name"] for b in sk.BUNDLED_SKILLS},
    }


# ---------------------------------------------------------------------------
# Classification heuristic for new commands
# ---------------------------------------------------------------------------

_UI_KW = re.compile(
    r"\b(open[s]? (the |an )?(dialog|picker|interface|wizard|settings|page)|"
    r"interactive (dialog|picker|wizard|slider)|"
    r"set the |toggle |alias for|show[s]? .{0,30}(grid|status|qr|stats|usage|version)|"
    r"view (the |your )|copy the |list (and|of)|"
    r"diagnose|exit the cli|sign (in|out))",
    re.IGNORECASE,
)
_MODEL_KW = re.compile(
    r"\b(review|analy[sz]e|generate|orchestrate|load[s]?|spawn[s]?|scan[s]?|"
    r"research(es)?|decompose|walks? through|fix(es)?|implement|update[s]?|"
    r"detach the (current )?session)",
    re.IGNORECASE,
)


def heuristic_class(desc: str, is_skill: bool) -> str:
    if is_skill:
        return "BUNDLED_SKILLS (skill — default lifecycle USP+Stop)"
    if _UI_KW.search(desc):
        return "KNOWN_PROBLEMATIC (likely UI-only — verify it doesn't write JSONL)"
    if _MODEL_KW.search(desc):
        return "COMMANDS (likely model-invoking — default USP+Stop)"
    return "unclear — empirically test before classifying"


# ---------------------------------------------------------------------------
# Main diff
# ---------------------------------------------------------------------------

def main() -> int:
    md = fetch_docs()
    docs = parse_docs(md)
    local = load_local()

    docs_active = {c: d for c, d in docs.items() if not d["removed_in"]}
    docs_removed = {c: d for c, d in docs.items() if d["removed_in"]}
    docs_skills = {c for c, d in docs_active.items() if d["is_skill"]}

    local_cmds = local["commands"]
    local_problematic = local["problematic"]
    local_skill_names = {f"/{n}" for n in local["bundled_skills"]}
    local_known = set(local_cmds) | local_problematic | local_skill_names

    print(BOLD("Source: ") + DIM(DOCS_URL))
    print(BOLD("Local:  ") + DIM(f"{ORCH_DIR.relative_to(REPO_ROOT)}/{{slash_commands,skills}}.py"))
    print(DIM(f"Parsed {len(docs)} docs entries ({len(docs_active)} active, "
              f"{len(docs_removed)} removed); local has {len(local_cmds)} COMMANDS, "
              f"{len(local_problematic)} KNOWN_PROBLEMATIC, "
              f"{len(local['bundled_skills'])} BUNDLED_SKILLS."))
    print()

    drift = False

    # 1. Removed in docs but still tracked locally
    stale = sorted(c for c in docs_removed
                   if c in local_cmds or c in local_problematic or c in local_skill_names)
    if stale:
        drift = True
        print(BOLD(RED("== Removed in docs but still tracked locally ==")))
        for cmd in stale:
            ver = docs_removed[cmd]["removed_in"]
            where = []
            if cmd in local_cmds: where.append("COMMANDS")
            if cmd in local_problematic: where.append("KNOWN_PROBLEMATIC")
            if cmd in local_skill_names: where.append("BUNDLED_SKILLS")
            print(f"  {RED(cmd)}  removed in v{ver}  →  drop from {'/'.join(where)}")
        print()

    # 2. BUNDLED_SKILLS membership drift
    docs_skill_names = {c.lstrip("/") for c in docs_skills}
    add_skill = sorted(docs_skill_names - local["bundled_skills"])
    drop_skill = sorted(local["bundled_skills"] - docs_skill_names)
    if add_skill or drop_skill:
        drift = True
        print(BOLD(YELLOW("== BUNDLED_SKILLS drift ==")))
        for n in add_skill:
            print(f"  {GREEN('+ ' + n)}  marked [Skill] in docs, missing in BUNDLED_SKILLS")
        for n in drop_skill:
            print(f"  {RED('- ' + n)}  in BUNDLED_SKILLS but no [Skill] tag in docs")
        print()

    # 3. New commands not seen locally
    new_cmds = sorted(c for c in docs_active if c not in local_known)
    if new_cmds:
        drift = True
        print(BOLD(GREEN(f"== New commands not in local catalog ({len(new_cmds)}) ==")))
        for cmd in new_cmds:
            info = docs_active[cmd]
            args = f" {info['args_form']}" if info["args_form"] else ""
            desc = info["description"]
            desc_short = desc[:140] + ("…" if len(desc) > 140 else "")
            print(f"  {GREEN(cmd + args)}")
            print(f"    {DIM(desc_short)}")
            print(f"    {YELLOW('→ ' + heuristic_class(desc, info['is_skill']))}")
        print()

    # 4. Local COMMANDS entries not present in docs at all
    docs_all = set(docs.keys())
    extras = sorted(c for c in local_cmds if c not in docs_all)
    if extras:
        drift = True
        print(BOLD(YELLOW(f"== Local COMMANDS not in docs ({len(extras)}) ==")))
        for cmd in extras:
            print(f"  {YELLOW(cmd)}  not on docs page (silently removed? renamed? "
                  f"user/plugin command?)")
        print()

    # Summary
    if drift:
        print(BOLD(RED("DRIFT DETECTED")))
        print("Update one or more of:")
        print(f"  - {ORCH_DIR.relative_to(REPO_ROOT)}/slash_commands.py  (COMMANDS, "
              f"KNOWN_PROBLEMATIC)")
        print(f"  - {ORCH_DIR.relative_to(REPO_ROOT)}/skills.py          (BUNDLED_SKILLS)")
        return 1

    print(GREEN(f"✓ All {len(docs_active)} active docs commands accounted for locally"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
