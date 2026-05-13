"""Claude Code skills — discovery, JSONL parsing helpers, source taxonomy.

Single home for all skill-related logic so jsonl_parser, routers/skills,
and slash_commands stay independent. If Claude Code changes how skills
are stored or invoked, only this file needs to track it.

Skill sources scanned by `list_skills`:
  personal   — ~/.claude/skills/<name>/SKILL.md
               ~/.claude/commands/<name>.md
  project    — <project>/.claude/skills/<name>/SKILL.md
               <project>/.claude/commands/<name>.md
  plugin     — ~/.claude/plugins/*/skills/<name>/SKILL.md
  command    — built-in slash commands from slash_commands.COMMANDS
  bundled    — hardcoded list of skills that ship with Claude Code CLI

Frontmatter rule: skills with `user-invocable: false` are filtered out
(model-only — should not appear in the user-facing picker).

Caching: `list_skills(project_path)` results are cached per project_path
in a module-level dict. Call `refresh_skills_cache(project_paths)` at
startup (and on demand) to warm or rebuild the cache. The cache is
keyed by project_path (None = global-only); on cache miss `list_skills`
builds and stores the entry inline.
"""

import logging
import os
import re
import threading

import yaml

from config import CLAUDE_HOME

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bundled skills (ship with Claude Code; no on-disk SKILL.md to scan)
# Source: Claude Code "Slash Commands" reference, entries marked [Skill].
# ---------------------------------------------------------------------------

# Built-in slash commands shown in the picker.  Two categories live here:
#  (1) bundled CLI skills with no on-disk SKILL.md (e.g. /debug, /simplify)
#  (2) model-invoking slash commands that follow the default USP→Stop
#      lifecycle and therefore have no slash_commands.COMMANDS entry
#      (e.g. /init, /commit) — listed here so the picker can advertise
#      them.  The picker is decorative; removing an entry here does not
#      prevent the command from working, it just hides it from the menu.
BUNDLED_SKILLS: list[dict] = [
    {"name": "batch",                    "description": "Run multiple agent tasks in batch"},
    {"name": "claude-api",               "description": "Interact with Claude API directly"},
    {"name": "commit",                   "description": "Create a git commit"},
    {"name": "debug",                    "description": "Debug an issue"},
    {"name": "fewer-permission-prompts", "description": "Reduce permission prompt frequency"},
    {"name": "init",                     "description": "Initialize project with CLAUDE.md"},
    {"name": "insights",                 "description": "Generate codebase insights"},
    # /loop is also a COMMANDS lifecycle exception; listed here too so the
    # source-precedence rule (command > bundled) has a real collision case
    # exercised in tests, and so the picker still surfaces it if COMMANDS
    # somehow drops it in the future.
    {"name": "loop",                     "description": "Run a repeating loop task"},
    {"name": "review",                   "description": "Review code changes (deprecated)"},
    {"name": "security-review",          "description": "Run a security review"},
    {"name": "simplify",                 "description": "Simplify code"},
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_skill_file(skill_path: str) -> dict | None:
    """Return {name, description} for a SKILL.md, or None if filtered out."""
    try:
        with open(skill_path, "r", errors="replace") as f:
            head = f.read(8192)
    except OSError as e:
        logger.debug("Skipping unreadable skill %s: %s", skill_path, e)
        return None
    fm = _parse_frontmatter(head)
    if fm.get("user-invocable") is False:
        return None
    return {
        "name": str(fm.get("name") or os.path.basename(os.path.dirname(skill_path))),
        "description": str(fm.get("description", "")).strip(),
    }


def _read_command_file(cmd_path: str) -> dict | None:
    """Return {name, description} for a `.claude/commands/<name>.md` file.

    Project/personal slash commands are file-per-command markdown documents
    (not the SKILL.md folder layout). Frontmatter is optional; if present
    the `description` field is used. The command name is the filename
    without the `.md` extension.
    """
    try:
        with open(cmd_path, "r", errors="replace") as f:
            head = f.read(8192)
    except OSError as e:
        logger.debug("Skipping unreadable command %s: %s", cmd_path, e)
        return None
    fm = _parse_frontmatter(head)
    if fm.get("user-invocable") is False:
        return None
    name = os.path.splitext(os.path.basename(cmd_path))[0]
    return {
        "name": str(fm.get("name") or name),
        "description": str(fm.get("description", "")).strip(),
    }


def _scan_skill_dir(skills_dir: str, source: str) -> list[dict]:
    """List all SKILL.md children of `skills_dir`, tagged with `source`."""
    out: list[dict] = []
    if not os.path.isdir(skills_dir):
        return out
    try:
        entries = sorted(os.listdir(skills_dir))
    except OSError as e:
        logger.warning("Failed to list %s: %s", skills_dir, e)
        return out
    for name in entries:
        skill_path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_path):
            continue
        info = _read_skill_file(skill_path)
        if info is None:
            continue
        info["source"] = source
        info["path"] = skill_path
        out.append(info)
    return out


def _scan_command_dir(commands_dir: str, source: str) -> list[dict]:
    """List `*.md` files in `commands_dir` (Claude Code slash commands)."""
    out: list[dict] = []
    if not os.path.isdir(commands_dir):
        return out
    try:
        entries = sorted(os.listdir(commands_dir))
    except OSError as e:
        logger.warning("Failed to list %s: %s", commands_dir, e)
        return out
    for fname in entries:
        if not fname.endswith(".md"):
            continue
        cmd_path = os.path.join(commands_dir, fname)
        if not os.path.isfile(cmd_path):
            continue
        info = _read_command_file(cmd_path)
        if info is None:
            continue
        info["source"] = source
        info["path"] = cmd_path
        out.append(info)
    return out


def _build_skills(project_path: str | None) -> list[dict]:
    """Enumerate skills from all known sources (uncached)."""
    skills: list[dict] = []
    seen: set[str] = set()

    def _add(entry: dict) -> None:
        name = entry.get("name")
        if not name or name in seen:
            return
        skills.append(entry)
        seen.add(name)

    # Personal — skills folder + commands folder
    for s in _scan_skill_dir(os.path.join(CLAUDE_HOME, "skills"), "personal"):
        _add(s)
    for s in _scan_command_dir(os.path.join(CLAUDE_HOME, "commands"), "personal"):
        _add(s)

    # Project — skills folder + commands folder
    if project_path:
        for s in _scan_skill_dir(
            os.path.join(project_path, ".claude", "skills"), "project",
        ):
            _add(s)
        for s in _scan_command_dir(
            os.path.join(project_path, ".claude", "commands"), "project",
        ):
            _add(s)

    # Plugin (one-level glob)
    plugins_root = os.path.join(CLAUDE_HOME, "plugins")
    if os.path.isdir(plugins_root):
        try:
            for plugin_name in sorted(os.listdir(plugins_root)):
                plugin_skills_dir = os.path.join(plugins_root, plugin_name, "skills")
                if os.path.isdir(plugin_skills_dir):
                    for s in _scan_skill_dir(
                        plugin_skills_dir, f"plugin:{plugin_name}",
                    ):
                        _add(s)
        except OSError as e:
            logger.warning("Failed to enumerate plugins: %s", e)

    # Built-in slash commands with known lifecycle (steers users away from
    # KNOWN_PROBLEMATIC ones — anything in COMMANDS is guaranteed safe to
    # send through the orchestrator).
    from slash_commands import COMMANDS
    for cmd, cfg in COMMANDS.items():
        _add({
            "name": cmd.lstrip("/"),
            "description": cfg.description,
            "source": "command",
        })

    # Bundled CLI skills (no on-disk SKILL.md)
    for b in BUNDLED_SKILLS:
        _add({**b, "source": "bundled"})

    return skills


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[str | None, list[dict]] = {}
_cache_lock = threading.Lock()


def list_skills(project_path: str | None = None) -> list[dict]:
    """Enumerate skills from all known sources (cached per project_path).

    Returns: list of {name, description, source, path?} dicts.
    Skills with `user-invocable: false` are filtered out.

    Source precedence (first wins on name collision):
      personal > project > plugin > command > bundled

    Cache: per `project_path` key. On miss, builds and stores the entry.
    Call `refresh_skills_cache()` to invalidate or pre-warm.
    """
    with _cache_lock:
        cached = _cache.get(project_path)
        if cached is not None:
            return cached

    built = _build_skills(project_path)
    with _cache_lock:
        _cache[project_path] = built
    return built


def refresh_skills_cache(project_paths: list[str] | None = None) -> int:
    """Rebuild the cache for the global key + each given project path.

    Pass the list of currently active project paths at startup (and on
    project-config changes). Returns the number of cache entries built.
    """
    keys: list[str | None] = [None]
    if project_paths:
        keys.extend(project_paths)

    rebuilt: dict[str | None, list[dict]] = {k: _build_skills(k) for k in keys}
    with _cache_lock:
        _cache.clear()
        _cache.update(rebuilt)
    return len(rebuilt)


def clear_skills_cache() -> None:
    """Drop all cached entries (useful for tests)."""
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# JSONL parsing helpers (consumed by jsonl_parser)
# ---------------------------------------------------------------------------

def is_hidden_meta_entry(entry: dict) -> bool:
    """True if a user JSONL entry is a hidden injection (skill body, system
    reminder, etc.) that should be dropped from the visible turn stream.

    All `isMeta` entries are dropped: the Skill tool_use turn that precedes
    a skill body already conveys the invocation; the body itself is on disk
    if a user wants to read it.
    """
    return bool(entry.get("isMeta"))


def format_skill_summary(skill_input: dict) -> str:
    """Display string for a `Skill` tool_use turn."""
    return f"> `Skill` {skill_input.get('skill', '')}"


def skill_turn_metadata(skill_input: dict) -> dict:
    """Extra metadata to attach to a `Skill` tool_use turn."""
    return {"skill_name": skill_input.get("skill", "")}
