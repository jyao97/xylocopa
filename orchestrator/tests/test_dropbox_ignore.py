"""Tests for dropbox_sync.ignore — ignore rules and folder selection."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dropbox_sync.ignore import (
    DEFAULT_IGNORE_RULES,
    ROOT_ENTRY,
    SYNCIGNORE_FILENAME,
    IgnoreRules,
    parse_rules,
)


class TestParseRules:
    """Test parse_rules helper."""

    def test_none(self):
        assert parse_rules(None) == []

    def test_empty_string(self):
        assert parse_rules("") == []

    def test_strips_comments_and_blanks(self):
        text = "# comment\n\nfoo\n  # indented comment\nbar\n\n"
        assert parse_rules(text) == ["foo", "bar"]

    def test_preserves_negation(self):
        assert parse_rules("!keep.txt") == ["!keep.txt"]


class TestDefaultRules:
    """Test that DEFAULT_IGNORE_RULES produce expected matches."""

    def test_defaults_match_git(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        assert rules.is_dir_ignored(".git")
        assert rules.is_dir_ignored("sub/.git")

    def test_defaults_match_node_modules(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        assert rules.is_dir_ignored("node_modules")
        assert rules.is_dir_ignored("frontend/node_modules")

    def test_defaults_match_pycache(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        assert rules.is_dir_ignored("__pycache__")

    def test_defaults_match_venv_variants(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        assert rules.is_dir_ignored(".venv")
        assert rules.is_dir_ignored("venv")
        assert rules.is_dir_ignored("foo_venv")
        assert rules.is_dir_ignored("ext/foo_venv")

    def test_defaults_match_pyc_files(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        ignored, reason = rules.is_file_ignored("test.pyc")
        assert ignored
        assert reason == "rule"

    def test_defaults_match_ds_store(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        ignored, reason = rules.is_file_ignored(".DS_Store")
        assert ignored
        assert reason == "rule"

    def test_defaults_do_not_match_normal_file(self):
        rules = IgnoreRules.build("/nonexistent", read_syncignore=False)
        ignored, reason = rules.is_file_ignored("src/main.py")
        assert not ignored
        assert reason is None


class TestSyncignoreFile:
    """Test reading .xylocopa-syncignore from project path."""

    def test_reads_syncignore(self, tmp_path):
        (tmp_path / SYNCIGNORE_FILENAME).write_text("*.log\n# comment\ntmp/\n")
        rules = IgnoreRules.build(str(tmp_path))
        ignored, reason = rules.is_file_ignored("server.log")
        assert ignored
        assert reason == "rule"
        assert rules.is_dir_ignored("tmp")

    def test_no_syncignore_ok(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        ignored, _ = rules.is_file_ignored("normal.py")
        assert not ignored

    def test_skip_syncignore(self, tmp_path):
        (tmp_path / SYNCIGNORE_FILENAME).write_text("*.py\n")
        rules = IgnoreRules.build(str(tmp_path), read_syncignore=False)
        ignored, _ = rules.is_file_ignored("main.py")
        assert not ignored


class TestExtraRules:
    """Test extra_rules parameter."""

    def test_extra_rules_applied(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path), extra_rules="*.tmp\nsecrets/")
        ignored, reason = rules.is_file_ignored("data.tmp")
        assert ignored
        assert reason == "rule"
        assert rules.is_dir_ignored("secrets")


class TestNegation:
    """Test negation patterns work correctly."""

    def test_negation_unignores(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            extra_rules="*.log\n!important.log",
            include_defaults=False,
            read_syncignore=False,
        )
        ignored, _ = rules.is_file_ignored("debug.log")
        assert ignored
        ignored, _ = rules.is_file_ignored("important.log")
        assert not ignored


class TestFolderSelection:
    """Test top_level_selected and folder filtering in is_file_ignored."""

    def test_folders_none_selects_all(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path), folders=None)
        assert rules.top_level_selected("src")
        assert rules.top_level_selected(ROOT_ENTRY)

    def test_folders_restrict(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path), folders=["src", "lib"])
        assert rules.top_level_selected("src")
        assert rules.top_level_selected("lib")
        assert not rules.top_level_selected("test")
        assert not rules.top_level_selected(ROOT_ENTRY)

    def test_folder_ignored_file(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            folders=["src"],
            include_defaults=False,
            read_syncignore=False,
        )
        # File in unselected folder
        ignored, reason = rules.is_file_ignored("test/foo.py")
        assert ignored
        assert reason == "folder"
        # File in selected folder
        ignored, reason = rules.is_file_ignored("src/main.py")
        assert not ignored

    def test_root_entry_selection(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            folders=[ROOT_ENTRY],
            include_defaults=False,
            read_syncignore=False,
        )
        # Root file selected
        ignored, reason = rules.is_file_ignored("README.md")
        assert not ignored
        # Subdirectory file not selected
        ignored, reason = rules.is_file_ignored("src/main.py")
        assert ignored
        assert reason == "folder"


class TestAllowlist:
    """Test allowlist extension filtering."""

    def test_allowlist_filters_extensions(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            allowlist_exts={".py", ".md"},
            include_defaults=False,
            read_syncignore=False,
        )
        ignored, reason = rules.is_file_ignored("main.py")
        assert not ignored
        ignored, reason = rules.is_file_ignored("readme.md")
        assert not ignored
        ignored, reason = rules.is_file_ignored("image.png")
        assert ignored
        assert reason == "allowlist"

    def test_allowlist_extensionless_by_name(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            allowlist_exts={".py", "Makefile"},
            include_defaults=False,
            read_syncignore=False,
        )
        ignored, reason = rules.is_file_ignored("Makefile")
        assert not ignored
        ignored, reason = rules.is_file_ignored("Dockerfile")
        assert ignored
        assert reason == "allowlist"

    def test_allowlist_case_insensitive_ext(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            allowlist_exts={".py"},
            include_defaults=False,
            read_syncignore=False,
        )
        ignored, reason = rules.is_file_ignored("test.PY")
        assert not ignored

    def test_allowlist_none_allows_all(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            allowlist_exts=None,
            include_defaults=False,
            read_syncignore=False,
        )
        ignored, _ = rules.is_file_ignored("anything.xyz")
        assert not ignored


class TestIsDefaultIgnoredDir:
    """Test is_default_ignored_dir for picker badge display."""

    def test_git_is_default_ignored(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        assert rules.is_default_ignored_dir(".git")

    def test_node_modules_is_default_ignored(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        assert rules.is_default_ignored_dir("node_modules")

    def test_src_is_not_default_ignored(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        assert not rules.is_default_ignored_dir("src")

    def test_venv_variants_default_ignored(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        assert rules.is_default_ignored_dir(".venv")
        assert rules.is_default_ignored_dir("venv")
        assert rules.is_default_ignored_dir("foo_venv")


class TestRulesText:
    """Test rules_text property."""

    def test_rules_text_includes_defaults(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path))
        text = rules.rules_text
        assert ".git/" in text
        assert "node_modules/" in text

    def test_rules_text_includes_extras(self, tmp_path):
        rules = IgnoreRules.build(str(tmp_path), extra_rules="*.log")
        text = rules.rules_text
        assert "*.log" in text

    def test_no_defaults_rules_text(self, tmp_path):
        rules = IgnoreRules.build(
            str(tmp_path),
            include_defaults=False,
            read_syncignore=False,
            extra_rules="*.tmp",
        )
        assert rules.rules_text == "*.tmp"
