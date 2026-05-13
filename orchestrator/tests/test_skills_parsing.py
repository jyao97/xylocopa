"""Tests for skill folding in jsonl_parser + decoupled skills module."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skills as skills_mod
from jsonl_parser import format_tool_summary, parse_session_turns_from_lines
from skills import (
    BUNDLED_SKILLS,
    _scan_command_dir,
    clear_skills_cache,
    format_skill_summary,
    is_hidden_meta_entry,
    list_skills,
    refresh_skills_cache,
    skill_turn_metadata,
)
from slash_commands import COMMANDS


# ---------------------------------------------------------------------------
# skills.py — pure helpers
# ---------------------------------------------------------------------------

class TestSkillHelpers:
    def test_format_skill_summary_with_name(self):
        assert format_skill_summary({"skill": "debug"}) == "> `Skill` debug"

    def test_format_skill_summary_missing_name(self):
        assert format_skill_summary({}) == "> `Skill` "

    def test_skill_turn_metadata(self):
        assert skill_turn_metadata({"skill": "loop"}) == {"skill_name": "loop"}

    def test_skill_turn_metadata_missing(self):
        assert skill_turn_metadata({}) == {"skill_name": ""}

    def test_is_hidden_meta_entry_true(self):
        assert is_hidden_meta_entry({"isMeta": True}) is True

    def test_is_hidden_meta_entry_false(self):
        assert is_hidden_meta_entry({"isMeta": False}) is False

    def test_is_hidden_meta_entry_missing(self):
        assert is_hidden_meta_entry({}) is False

    def test_bundled_skills_have_name_and_description(self):
        assert len(BUNDLED_SKILLS) > 0
        for s in BUNDLED_SKILLS:
            assert s["name"] and isinstance(s["name"], str)
            assert "description" in s


# ---------------------------------------------------------------------------
# list_skills — built-in command merging + dedup
# ---------------------------------------------------------------------------

class TestListSkillsMerging:
    def test_includes_builtin_commands(self):
        names_by_source = {(s["name"], s["source"]) for s in list_skills()}
        # Every COMMANDS entry should appear (unless overridden by personal/etc).
        for cmd in COMMANDS:
            bare = cmd.lstrip("/")
            sources = {src for (n, src) in names_by_source if n == bare}
            assert sources, f"missing built-in command: {cmd}"

    def test_command_source_label_present(self):
        sources = {s["source"] for s in list_skills()}
        assert "command" in sources

    def test_no_duplicate_names(self):
        all_skills = list_skills()
        names = [s["name"] for s in all_skills]
        assert len(names) == len(set(names)), "list_skills produced duplicate names"

    def test_command_overrides_bundled_on_collision(self):
        """Names appearing in both COMMANDS and BUNDLED_SKILLS should resolve
        to source='command' (precedence rule)."""
        bundled_names = {b["name"] for b in BUNDLED_SKILLS}
        command_names = {c.lstrip("/") for c in COMMANDS}
        overlap = bundled_names & command_names
        assert overlap, "expected at least one overlap to validate precedence"
        by_name = {s["name"]: s["source"] for s in list_skills()}
        for name in overlap:
            assert by_name.get(name) == "command", (
                f"{name} should resolve as command, got {by_name.get(name)}"
            )


# ---------------------------------------------------------------------------
# format_tool_summary integration
# ---------------------------------------------------------------------------

class TestFormatToolSummarySkill:
    def test_skill_routes_through_helper(self):
        assert format_tool_summary("Skill", {"skill": "simplify"}) == "> `Skill` simplify"


# ---------------------------------------------------------------------------
# parse_session_turns_from_lines — folding behavior
# ---------------------------------------------------------------------------

def _line(entry: dict) -> str:
    return json.dumps(entry) + "\n"


class TestSkillFolding:
    def test_skill_tool_use_emits_one_turn_with_skill_name(self):
        """Skill tool_use becomes a single assistant turn carrying skill_name."""
        lines = [
            _line({
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-04-18T00:00:00Z",
                "message": {"role": "user", "content": "/debug"},
            }),
            _line({
                "type": "assistant",
                "uuid": "a1",
                "timestamp": "2026-04-18T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Skill",
                        "input": {"skill": "debug"},
                    }],
                },
            }),
        ]
        turns = parse_session_turns_from_lines(lines)
        skill_turns = [t for t in turns if t[2] and t[2].get("tool_name") == "Skill"]
        assert len(skill_turns) == 1
        role, content, meta, _uuid, kind, _ts = skill_turns[0]
        assert role == "assistant"
        assert content == "> `Skill` debug"
        assert meta["skill_name"] == "debug"
        assert kind == "tool_use"

    def test_command_wrapper_emits_slash_signal_turn(self):
        """``<command-message>`` wrappers surface as user turns with
        ``kind="slash_signal"`` and canonical ``/<cmd> <args>`` content.
        The sync engine matches them against the pre-dispatched web/task
        row via ContentMatcher (exact / normalized strategies); on miss it
        does *not* synthesize a CLI row (handled in sync_engine, not here).
        ``<command-name>``-only fragments are still dropped."""
        lines = [
            _line({
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-04-18T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": "<command-message>paper-finder</command-message>\n<command-name>/paper-finder</command-name>\n<command-args>corl 2025 generalizable safety</command-args>",
                },
            }),
            _line({
                "type": "user",
                "uuid": "u2",
                "timestamp": "2026-04-18T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": "<command-message>claude-api</command-message>\n<command-name>/claude-api</command-name>",
                },
            }),
            _line({
                "type": "user",
                "uuid": "u3",
                "timestamp": "2026-04-18T00:00:02Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/claude-api</command-name>",
                },
            }),
            # Claude Code v2.1.140+ flipped the tag order: <command-name>
            # now comes BEFORE <command-message>. Parser must accept both.
            _line({
                "type": "user",
                "uuid": "u4",
                "timestamp": "2026-05-13T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n<command-args>write done to /tmp/x</command-args>",
                },
            }),
            _line({
                "type": "user",
                "uuid": "u5",
                "timestamp": "2026-05-13T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/compact</command-name>\n<command-message>compact</command-message>",
                },
            }),
        ]
        turns = parse_session_turns_from_lines(lines)
        signal_turns = [t for t in turns if len(t) > 4 and t[4] == "slash_signal"]
        assert len(signal_turns) == 4  # u1, u2 (old fmt) + u4, u5 (new fmt); u3 dropped
        contents = [t[1] for t in signal_turns]
        assert "/paper-finder corl 2025 generalizable safety" in contents
        assert "/claude-api" in contents  # u2 unwrapped (no args)
        assert "/goal write done to /tmp/x" in contents  # u4 new format with args
        assert "/compact" in contents  # u5 new format without args
        # Wrapper text never leaks into any turn's content
        all_contents = [t[1] for t in turns]
        assert not any("<command-message>" in c for c in all_contents)
        assert not any("<command-name>" in c for c in all_contents)

    def test_unrecognized_command_wrapper_logs_and_drops(self, caplog):
        """A ``<command-*>``-prefixed turn that *fails* to parse must NOT
        silently disappear via the skip-list — it should log a warning so
        wrapper-format drift (e.g. CC renaming a tag) is observable.

        The parser owns the ``<command-*>`` namespace exclusively; if it
        can't extract a ``(cmd, args)`` pair, drop the turn AND log.  This
        guards against the prior bug where parser failure fell through to
        the system-injection skip-list and was indistinguishable from a
        legitimate ``<system-reminder>`` drop."""
        import logging
        lines = [
            # Malformed: <command-name> only — parser returns None
            _line({
                "type": "user",
                "uuid": "u-malformed-1",
                "timestamp": "2026-05-13T00:00:00Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/mystery</command-name>",
                },
            }),
            # Hypothetical future-CC wrapper variant we don't recognize
            _line({
                "type": "user",
                "uuid": "u-malformed-2",
                "timestamp": "2026-05-13T00:00:01Z",
                "message": {
                    "role": "user",
                    "content": "<command-message>mystery</command-message>\n<command-renamed-tag>future</command-renamed-tag>",
                },
            }),
            # Sanity: a *legitimately parseable* wrapper to confirm
            # parsing still works alongside the malformed cases.
            _line({
                "type": "user",
                "uuid": "u-ok",
                "timestamp": "2026-05-13T00:00:02Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n<command-args>x</command-args>",
                },
            }),
        ]
        with caplog.at_level(logging.WARNING, logger="orchestrator.jsonl_parser"):
            turns = parse_session_turns_from_lines(lines)
        # Only the well-formed wrapper emits a turn
        signal_turns = [t for t in turns if len(t) > 4 and t[4] == "slash_signal"]
        assert len(signal_turns) == 1
        assert signal_turns[0][1] == "/goal x"
        # The two malformed wrappers must both have logged a warning
        warning_msgs = [r.getMessage() for r in caplog.records
                        if r.levelno == logging.WARNING]
        unrecognized = [m for m in warning_msgs if "unrecognized <command-*>" in m]
        assert len(unrecognized) == 2, (
            f"expected 2 unrecognized-wrapper warnings, got "
            f"{len(unrecognized)}: {warning_msgs}"
        )

    def test_system_injection_prefixes_still_silent(self, caplog):
        """The decoupling must NOT make system-injection prefixes
        (``<system-reminder>``, ``<local-command-caveat>``, etc.) start
        logging — they're known-and-expected drops, not drift signals."""
        import logging
        lines = [
            _line({
                "type": "user",
                "uuid": "u-sys-1",
                "timestamp": "2026-05-13T00:00:00Z",
                "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"},
            }),
            _line({
                "type": "user",
                "uuid": "u-sys-2",
                "timestamp": "2026-05-13T00:00:01Z",
                "message": {"role": "user", "content": "<local-command-caveat>x</local-command-caveat>"},
            }),
            _line({
                "type": "user",
                "uuid": "u-sys-3",
                "timestamp": "2026-05-13T00:00:02Z",
                "message": {"role": "user", "content": "<local-command-stdout>x</local-command-stdout>"},
            }),
            _line({
                "type": "user",
                "uuid": "u-sys-4",
                "timestamp": "2026-05-13T00:00:03Z",
                "message": {"role": "user", "content": "<task-notification>x</task-notification>"},
            }),
        ]
        with caplog.at_level(logging.WARNING, logger="orchestrator.jsonl_parser"):
            turns = parse_session_turns_from_lines(lines)
        # None emit user turns
        user_turns = [t for t in turns if t[0] == "user"]
        assert user_turns == []
        # None log warnings (they're intentional drops, not drift)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings == [], (
            f"system-injection prefixes should drop silently, got warnings: "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_ismeta_user_entries_dropped(self):
        """isMeta:true user entries (skill bodies, system reminders) are filtered out."""
        lines = [
            _line({
                "type": "user",
                "uuid": "u1",
                "timestamp": "2026-04-18T00:00:00Z",
                "message": {"role": "user", "content": "real user message"},
            }),
            _line({
                "type": "user",
                "uuid": "u2",
                "isMeta": True,
                "timestamp": "2026-04-18T00:00:01Z",
                "message": {"role": "user", "content": "<<SKILL BODY>>"},
            }),
            _line({
                "type": "user",
                "uuid": "u3",
                "timestamp": "2026-04-18T00:00:02Z",
                "message": {"role": "user", "content": "second real message"},
            }),
        ]
        turns = parse_session_turns_from_lines(lines)
        contents = [t[1] for t in turns if t[0] == "user"]
        assert "<<SKILL BODY>>" not in contents
        assert "real user message" in contents
        assert "second real message" in contents


# ---------------------------------------------------------------------------
# _scan_command_dir — file-per-command markdown layout
# ---------------------------------------------------------------------------

class TestScanCommandDir:
    def test_returns_empty_for_missing_dir(self, tmp_path):
        assert _scan_command_dir(str(tmp_path / "nope"), "project") == []

    def test_picks_up_md_files(self, tmp_path):
        (tmp_path / "ship.md").write_text("Ship the build")
        (tmp_path / "lint.md").write_text("Run linter")
        out = _scan_command_dir(str(tmp_path), "project")
        names = sorted(s["name"] for s in out)
        assert names == ["lint", "ship"]
        assert all(s["source"] == "project" for s in out)
        assert all(s["path"].endswith(".md") for s in out)

    def test_ignores_non_md_files(self, tmp_path):
        (tmp_path / "ship.md").write_text("body")
        (tmp_path / "README.txt").write_text("nope")
        (tmp_path / "notes").write_text("nope")
        out = _scan_command_dir(str(tmp_path), "personal")
        assert [s["name"] for s in out] == ["ship"]

    def test_uses_frontmatter_description(self, tmp_path):
        (tmp_path / "deploy.md").write_text(
            "---\ndescription: Push to prod\n---\nbody here\n"
        )
        out = _scan_command_dir(str(tmp_path), "project")
        assert out[0]["description"] == "Push to prod"

    def test_filters_user_invocable_false(self, tmp_path):
        (tmp_path / "internal.md").write_text(
            "---\nuser-invocable: false\n---\nbody\n"
        )
        (tmp_path / "public.md").write_text("body")
        out = _scan_command_dir(str(tmp_path), "project")
        assert [s["name"] for s in out] == ["public"]


class TestProjectCommandsInListSkills:
    def test_project_commands_appear_with_project_source(self, tmp_path, monkeypatch):
        clear_skills_cache()
        cmds_dir = tmp_path / ".claude" / "commands"
        cmds_dir.mkdir(parents=True)
        (cmds_dir / "ship-it.md").write_text(
            "---\ndescription: project-only command\n---\nbody\n"
        )
        skills = list_skills(project_path=str(tmp_path))
        match = [s for s in skills if s["name"] == "ship-it"]
        assert match, "expected project command 'ship-it' in list_skills output"
        assert match[0]["source"] == "project"
        assert match[0]["description"] == "project-only command"


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------

class TestSkillsCache:
    def test_list_skills_caches_result(self, monkeypatch):
        clear_skills_cache()
        calls = {"n": 0}
        original = skills_mod._build_skills

        def counting_build(p):
            calls["n"] += 1
            return original(p)

        monkeypatch.setattr(skills_mod, "_build_skills", counting_build)

        list_skills(None)
        list_skills(None)
        list_skills(None)
        assert calls["n"] == 1, "second/third calls should hit cache"

    def test_distinct_project_paths_cached_separately(self, monkeypatch):
        clear_skills_cache()
        calls = {"n": 0}
        original = skills_mod._build_skills

        def counting_build(p):
            calls["n"] += 1
            return original(p)

        monkeypatch.setattr(skills_mod, "_build_skills", counting_build)

        list_skills("/tmp/proj-a")
        list_skills("/tmp/proj-b")
        list_skills("/tmp/proj-a")
        list_skills("/tmp/proj-b")
        assert calls["n"] == 2, "each unique project_path builds once"

    def test_refresh_rebuilds_and_clears_old_keys(self, monkeypatch):
        clear_skills_cache()
        list_skills("/tmp/old-project")
        # /tmp/old-project is now cached
        assert "/tmp/old-project" in skills_mod._cache

        n = refresh_skills_cache(["/tmp/new-project"])
        # Old key dropped; only None + the new project remain
        assert n == 2
        assert "/tmp/old-project" not in skills_mod._cache
        assert None in skills_mod._cache
        assert "/tmp/new-project" in skills_mod._cache

    def test_refresh_with_no_paths_keeps_only_global(self):
        clear_skills_cache()
        list_skills("/tmp/p1")
        n = refresh_skills_cache(None)
        assert n == 1
        assert set(skills_mod._cache.keys()) == {None}
