"""Tests for slash command lifecycle polarity + COMMANDS shape.

These tests guard the two structural decisions made when collapsing
xylocopa's slash-command catalog:

  (1) COMMANDS lists *only* lifecycle exceptions (commands whose
      completed_by ≠ Stop, or whose delivered_by ≠ USP).  Adding a
      new model-invoking command should NOT require a code change.

  (2) completes_on_stop() defaults to True for unrecognized commands.
      The old polarity (`return False` for unknowns) silently stranded
      model-invoking commands in EXECUTING when Claude Code shipped
      a new slash command before xylocopa learned about it.

If either of these regresses, the symptom is: "I sent /<new-cmd> from
the web UI and it shows EXECUTING forever, never completing."
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from slash_commands import (
    COMMANDS,
    KNOWN_PROBLEMATIC,
    completes_on_stop,
    is_allowed,
)


class TestCommandsShape:
    """COMMANDS holds only lifecycle exceptions, nothing else."""

    def test_only_exceptions_listed(self):
        """The dict should contain *only* /clear, /compact, /loop, /goal."""
        assert set(COMMANDS.keys()) == {"/clear", "/compact", "/loop", "/goal"}

    def test_every_entry_is_a_real_exception(self):
        """Every listed command must have a non-default lifecycle, otherwise
        it has no business taking up space here — the default path handles it."""
        for cmd, cfg in COMMANDS.items():
            non_default = (
                cfg.completed_by != "Stop"
                or cfg.delivered_by != "USP"
                or cfg.changes_session
            )
            assert non_default, (
                f"{cmd} uses the default USP→Stop lifecycle and should not "
                f"be listed in COMMANDS (move its description to "
                f"skills.BUNDLED_SKILLS if the picker needs it)."
            )

    def test_no_overlap_with_known_problematic(self):
        """A command can't be both an exception and forbidden."""
        for cmd in COMMANDS:
            assert cmd not in KNOWN_PROBLEMATIC, (
                f"{cmd} appears in both COMMANDS and KNOWN_PROBLEMATIC"
            )


class TestCompletesOnStopPolarity:
    """Default = True (assume Stop completes it); exceptions = False."""

    def test_unknown_command_defaults_to_true(self):
        """The polarity flip — this is the whole point of the change.

        If this regresses, every Claude-Code-added command (which
        xylocopa doesn't yet know about) gets stuck EXECUTING after
        Stop fires."""
        assert completes_on_stop("/totally-new-cmd") is True
        assert completes_on_stop("/schedule something") is True
        assert completes_on_stop("/rewind") is True

    def test_previously_listed_model_invoking_now_unknown(self):
        """These were in COMMANDS before the shrink; after the shrink
        they fall through to the default-True path.  Behavior is
        identical (they always wanted Stop-completion), but the source
        of truth is now the default, not an explicit entry."""
        for cmd in ("/init", "/review", "/commit", "/security-review",
                    "/insights", "/simplify", "/debug", "/batch",
                    "/claude-api"):
            assert completes_on_stop(cmd) is True, (
                f"{cmd} should default to Stop-completion"
            )

    def test_long_running_returns_false(self):
        """/loop and /goal must NOT be marked complete by Stop —
        Stop fires per iteration/round, but the command is still
        running until SessionEnd or CronDelete."""
        assert completes_on_stop("/loop forever") is False
        assert completes_on_stop("/goal write done") is False

    def test_session_changing_returns_false(self):
        """/compact and /clear have their own completion hooks
        (PostCompact and SessionStart respectively)."""
        assert completes_on_stop("/compact") is False
        assert completes_on_stop("/clear") is False

    def test_command_with_args_still_recognized(self):
        """Arg-bearing forms must still hit the exception table."""
        assert completes_on_stop("/loop every 5 minutes") is False
        assert completes_on_stop("/goal fix the bug then write done") is False
        assert completes_on_stop("/compact summarize the API discussion") is False


class TestIsAllowedAfterShrink:
    """Gate behavior — removed commands default-allow, KNOWN_PROBLEMATIC
    still rejects."""

    def test_lifecycle_exceptions_allowed(self):
        for cmd in COMMANDS:
            assert is_allowed(cmd) is True

    def test_previously_listed_still_allowed(self):
        """Removing from COMMANDS doesn't reject — they pass via the
        default-allow path (not in KNOWN_PROBLEMATIC)."""
        for cmd in ("/init", "/review", "/commit", "/security-review",
                    "/insights", "/simplify", "/debug", "/batch",
                    "/claude-api"):
            assert is_allowed(cmd) is True, f"{cmd} should still be allowed"

    def test_known_problematic_still_rejected(self):
        assert is_allowed("/exit") is False
        assert is_allowed("/quit") is False
        assert is_allowed("/help") is False
        assert is_allowed("/config") is False

    def test_non_slash_always_allowed(self):
        assert is_allowed("regular message") is True
        assert is_allowed("") is True


class TestSkillsPickerNoRegression:
    """Shrinking COMMANDS must not vanish previously-visible commands
    from the picker — they should now come from BUNDLED_SKILLS."""

    def test_previously_listed_still_in_picker(self):
        from skills import list_skills

        names = {s["name"] for s in list_skills(None)}
        for name in ("init", "review", "commit", "security-review",
                     "insights", "simplify", "debug", "batch",
                     "claude-api"):
            assert name in names, (
                f"picker lost {name} after COMMANDS shrink — "
                f"add it to skills.BUNDLED_SKILLS"
            )
