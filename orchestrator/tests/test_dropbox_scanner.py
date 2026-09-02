"""Tests for dropbox_sync.scanner — filesystem scanning for Dropbox sync."""

import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dropbox_sync.ignore import IgnoreRules, ROOT_ENTRY
from dropbox_sync.scanner import (
    ScanEntry,
    SkipStats,
    TopLevelStats,
    dry_run,
    list_top_level,
    scan_project,
)


def _make_file(path, content=b"x"):
    """Create a file at *path* with *content*, creating parent dirs as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def _rules(tmp_path, **kwargs):
    """Build IgnoreRules for a tmp_path project."""
    return IgnoreRules.build(str(tmp_path), **kwargs)


class TestListTopLevel:
    """Test list_top_level ordering, types, and the '.' entry."""

    def test_empty_dir(self, tmp_path):
        result = list_top_level(str(tmp_path))
        assert result == []

    def test_only_dirs(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "lib").mkdir()
        result = list_top_level(str(tmp_path))
        assert len(result) == 2
        assert result[0]["name"] == "lib"
        assert result[1]["name"] == "src"
        assert all(r["type"] == "dir" for r in result)

    def test_root_files_create_dot_entry(self, tmp_path):
        _make_file(str(tmp_path / "README.md"), b"hi")
        (tmp_path / "src").mkdir()
        result = list_top_level(str(tmp_path))
        assert result[0] == {"name": ".", "type": "root", "default_ignored": False}
        assert result[1]["name"] == "src"

    def test_dot_only_when_root_files_exist(self, tmp_path):
        (tmp_path / "src").mkdir()
        result = list_top_level(str(tmp_path))
        names = [r["name"] for r in result]
        assert "." not in names

    def test_case_insensitive_sort(self, tmp_path):
        (tmp_path / "Zlib").mkdir()
        (tmp_path / "alpha").mkdir()
        result = list_top_level(str(tmp_path))
        assert result[0]["name"] == "alpha"
        assert result[1]["name"] == "Zlib"

    def test_symlinks_listed_last(self, tmp_path):
        (tmp_path / "adir").mkdir()
        _make_file(str(tmp_path / "afile.txt"), b"x")
        os.symlink(str(tmp_path / "adir"), str(tmp_path / "linkdir"))
        result = list_top_level(str(tmp_path))
        types = [r["type"] for r in result]
        assert types == ["root", "dir", "symlink"]
        link_entry = [r for r in result if r["type"] == "symlink"][0]
        assert link_entry["default_ignored"] is True

    def test_default_ignored_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "src").mkdir()
        rules = _rules(tmp_path)
        result = list_top_level(str(tmp_path), rules=rules)
        git_entry = [r for r in result if r["name"] == ".git"][0]
        assert git_entry["default_ignored"] is True
        src_entry = [r for r in result if r["name"] == "src"][0]
        assert src_entry["default_ignored"] is False

    def test_hidden_dirs_listed(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        result = list_top_level(str(tmp_path))
        names = [r["name"] for r in result]
        assert ".hidden" in names


class TestSymlinks:
    """Test that symlinks are never followed or counted as files."""

    def test_symlinked_file_skipped(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_bytes(b"data")
        link = tmp_path / "src" / "link.txt"
        (tmp_path / "src").mkdir()
        os.symlink(str(real), str(link))

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(str(tmp_path), rules)

        # The symlink should not appear in entries
        rel_paths = [e.rel_path for e in entries]
        assert "src/link.txt" not in rel_paths
        assert total.symlinks >= 1

    def test_symlinked_dir_skipped(self, tmp_path):
        target = tmp_path / "real_dir"
        target.mkdir()
        _make_file(str(target / "inner.txt"), b"inner")
        os.symlink(str(target), str(tmp_path / "link_dir"))

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(str(tmp_path), rules)

        rel_paths = [e.rel_path for e in entries]
        assert not any(p.startswith("link_dir/") for p in rel_paths)
        assert total.symlinks >= 1

    def test_symlink_loop_safe(self, tmp_path):
        # Create a symlink loop: a -> b -> a
        a = tmp_path / "src" / "a"
        b = tmp_path / "src" / "b"
        (tmp_path / "src").mkdir()
        os.symlink(str(b), str(a))
        os.symlink(str(a), str(b))

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(str(tmp_path), rules)
        # Should not hang or raise; symlinks counted
        assert total.symlinks >= 2


class TestIgnoredDirNotDescended:
    """Test that ignored directories are not entered."""

    def test_ignored_dir_not_descended_permission(self, tmp_path):
        # Create an ignored directory with a permission-denied subdir
        ignored = tmp_path / "src" / "node_modules"
        ignored.mkdir(parents=True)
        denied = ignored / "forbidden"
        denied.mkdir()
        _make_file(str(denied / "secret.txt"), b"secret")
        os.chmod(str(denied), 0o000)

        try:
            rules = _rules(tmp_path)
            entries, per_top, total = scan_project(str(tmp_path), rules)
            # Since node_modules is ignored, we should not get permission errors
            # from trying to read the forbidden subdir
            rel_paths = [e.rel_path for e in entries]
            assert not any("node_modules" in p for p in rel_paths)
            # No errors from descending into the forbidden dir
            src_stats = per_top.get("src")
            if src_stats:
                assert src_stats.skipped.errors == 0
        finally:
            os.chmod(str(denied), 0o755)

    def test_ignored_dir_count(self, tmp_path):
        (tmp_path / "src").mkdir()
        _make_file(str(tmp_path / "src" / "main.py"), b"print()")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        _make_file(str(nm / "index.js"), b"//")

        rules = _rules(tmp_path)
        entries, per_top, total = scan_project(str(tmp_path), rules)
        rel_paths = [e.rel_path for e in entries]
        assert "src/main.py" in rel_paths
        assert not any("node_modules" in p for p in rel_paths)


class TestMaxFileBytes:
    """Test max_file_bytes → too_large skip."""

    def test_too_large_skipped(self, tmp_path):
        _make_file(str(tmp_path / "src" / "small.txt"), b"x")
        _make_file(str(tmp_path / "src" / "big.txt"), b"x" * 1000)

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(
            str(tmp_path), rules, max_file_bytes=500
        )
        rel_paths = [e.rel_path for e in entries]
        assert "src/small.txt" in rel_paths
        assert "src/big.txt" not in rel_paths
        assert total.too_large >= 1


class TestOnEntryDone:
    """Test on_entry_done callback."""

    def test_called_once_per_top_level(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        _make_file(str(tmp_path / "alpha" / "a.txt"), b"a")
        (tmp_path / "beta").mkdir()
        _make_file(str(tmp_path / "beta" / "b.txt"), b"b")
        _make_file(str(tmp_path / "root.txt"), b"r")

        done_entries: list[str] = []

        def on_done(stats: TopLevelStats):
            done_entries.append(stats.name)

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        scan_project(str(tmp_path), rules, on_entry_done=on_done)

        # "." for root files, plus each directory
        assert ROOT_ENTRY in done_entries
        assert "alpha" in done_entries
        assert "beta" in done_entries
        assert len(done_entries) == 3


class TestShouldStop:
    """Test should_stop is honoured."""

    def test_stop_limits_scan(self, tmp_path):
        # Create files spread across many top-level directories
        for d in range(10):
            for i in range(5):
                _make_file(str(tmp_path / f"dir{d:02d}" / f"file{i:03d}.txt"), b"x")

        stop_flag = False
        dirs_started = 0

        def stop_after_few_dirs():
            return stop_flag

        def on_done(stats):
            nonlocal stop_flag, dirs_started
            dirs_started += 1
            if dirs_started >= 3:
                stop_flag = True

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(
            str(tmp_path), rules, should_stop=stop_after_few_dirs,
            on_entry_done=on_done,
        )
        # Should have stopped before processing all directories
        assert len(per_top) < 10


class TestRelPaths:
    """Test relative path formatting and control character handling."""

    def test_rel_paths_use_forward_slash(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        _make_file(str(nested / "file.txt"), b"x")

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, _, _ = scan_project(str(tmp_path), rules)
        paths = [e.rel_path for e in entries]
        assert "a/b/c/file.txt" in paths
        assert all("/" in p or "/" not in p for p in paths)  # no backslash
        assert all("\\" not in p for p in paths)

    def test_control_char_names_skipped_as_errors(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        # Create a file with a control character in name
        bad_name = "bad\x01file.txt"
        try:
            _make_file(str(src / bad_name), b"x")
        except OSError:
            pytest.skip("OS does not support control chars in filenames")

        _make_file(str(src / "good.txt"), b"y")

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        entries, per_top, total = scan_project(str(tmp_path), rules)
        rel_paths = [e.rel_path for e in entries]
        assert "src/good.txt" in rel_paths
        assert not any("\x01" in p for p in rel_paths)
        assert total.errors >= 1


class TestDryRun:
    """Test dry_run returns the expected summary dict."""

    def test_dry_run_shape(self, tmp_path):
        (tmp_path / "src").mkdir()
        _make_file(str(tmp_path / "src" / "main.py"), b"print('hi')")
        _make_file(str(tmp_path / "README.md"), b"# readme")

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        result = dry_run(str(tmp_path), rules)

        assert "entries" in result
        assert "total" in result
        assert "stopped" in result
        assert result["stopped"] is False
        assert result["total"]["files"] == 2
        assert "skipped" in result["total"]

    def test_dry_run_stopped(self, tmp_path):
        for i in range(10):
            _make_file(str(tmp_path / "src" / f"f{i}.txt"), b"x")

        rules = _rules(tmp_path, include_defaults=False, read_syncignore=False)
        result = dry_run(str(tmp_path), rules, should_stop=lambda: True)
        assert result["stopped"] is True


class TestTopLevelStatsDict:
    """Test TopLevelStats.as_dict and SkipStats.as_dict."""

    def test_skip_stats_as_dict(self):
        s = SkipStats(ignored=1, too_large=2, symlinks=3, allowlist=4, errors=5)
        d = s.as_dict()
        assert d == {
            "ignored": 1,
            "too_large": 2,
            "symlinks": 3,
            "allowlist": 4,
            "errors": 5,
        }

    def test_top_level_stats_as_dict(self):
        s = TopLevelStats(name="src", type="dir", files=10, bytes=5000)
        d = s.as_dict()
        assert d["name"] == "src"
        assert d["type"] == "dir"
        assert d["files"] == 10
        assert d["bytes"] == 5000
        assert "skipped" in d
