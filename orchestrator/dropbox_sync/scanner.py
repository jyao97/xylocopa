"""Filesystem scanner for Dropbox sync — walks a project tree respecting ignore rules."""

import logging
import os
from dataclasses import dataclass, field
from typing import Callable

from .ignore import IgnoreRules, ROOT_ENTRY

logger = logging.getLogger("orchestrator.dropbox.scanner")


@dataclass(frozen=True)
class ScanEntry:
    rel_path: str
    size: int
    mtime_ns: int


@dataclass
class SkipStats:
    ignored: int = 0
    too_large: int = 0
    symlinks: int = 0
    allowlist: int = 0
    errors: int = 0

    def as_dict(self) -> dict:
        return {
            "ignored": self.ignored,
            "too_large": self.too_large,
            "symlinks": self.symlinks,
            "allowlist": self.allowlist,
            "errors": self.errors,
        }


@dataclass
class TopLevelStats:
    name: str
    type: str
    files: int = 0
    bytes: int = 0
    skipped: SkipStats = field(default_factory=SkipStats)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "files": self.files,
            "bytes": self.bytes,
            "skipped": self.skipped.as_dict(),
        }


def _has_control_char(s: str) -> bool:
    """Return True if *s* contains any character with ordinal < 0x20."""
    return any(ord(c) < 0x20 for c in s)


def _path_components_valid(rel_path: str) -> bool:
    """Return True if no component of *rel_path* is '.' or '..'."""
    for part in rel_path.split("/"):
        if part in (".", ".."):
            return False
    return True


def list_top_level(
    project_path: str, rules: IgnoreRules | None = None
) -> list[dict]:
    """List top-level entries in *project_path* for folder picker display.

    Returns a sorted list of dicts with keys: name, type, default_ignored.
    "." is present only if the root contains at least one regular file.
    Sorted: "." first, then dirs by name (case-insensitive), then symlinks.
    """
    dirs: list[dict] = []
    symlinks: list[dict] = []
    has_root_file = False

    try:
        entries = list(os.scandir(project_path))
    except OSError:
        return []

    for entry in entries:
        try:
            is_symlink = entry.is_symlink()
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue

        if is_symlink:
            symlinks.append({
                "name": entry.name,
                "type": "symlink",
                "default_ignored": True,
            })
        elif is_dir:
            default_ignored = False
            if rules is not None:
                default_ignored = rules.is_default_ignored_dir(entry.name)
            dirs.append({
                "name": entry.name,
                "type": "dir",
                "default_ignored": default_ignored,
            })
        else:
            has_root_file = True

    result: list[dict] = []
    if has_root_file:
        result.append({
            "name": ".",
            "type": "root",
            "default_ignored": False,
        })

    dirs.sort(key=lambda d: d["name"].lower())
    symlinks.sort(key=lambda d: d["name"].lower())
    result.extend(dirs)
    result.extend(symlinks)
    return result


def scan_project(
    project_path: str,
    rules: IgnoreRules,
    *,
    max_file_bytes: int | None = None,
    on_entry_done: Callable[[TopLevelStats], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[list[ScanEntry], dict[str, TopLevelStats], SkipStats]:
    """Walk *project_path* collecting files that pass *rules*.

    Returns (entries, per_top_level_stats, total_skipped).
    """
    all_entries: list[ScanEntry] = []
    per_top: dict[str, TopLevelStats] = {}
    total_skipped = SkipStats()
    file_count = 0
    stopped = False

    try:
        top_items = list(os.scandir(project_path))
    except OSError:
        return all_entries, per_top, total_skipped

    # Separate root files and directories
    root_files: list[os.DirEntry] = []
    top_dirs: list[os.DirEntry] = []

    for item in top_items:
        try:
            is_symlink = item.is_symlink()
            is_dir = item.is_dir(follow_symlinks=False)
        except OSError:
            total_skipped.errors += 1
            continue

        if is_symlink:
            total_skipped.symlinks += 1
            continue
        elif is_dir:
            top_dirs.append(item)
        else:
            root_files.append(item)

    # Process root files as "." entry
    if root_files:
        root_stats = TopLevelStats(name=ROOT_ENTRY, type="root")
        if rules.top_level_selected(ROOT_ENTRY):
            for item in root_files:
                file_count += 1
                if should_stop and file_count % 1000 == 0 and should_stop():
                    stopped = True
                    break

                rel_path = item.name
                if _has_control_char(rel_path):
                    root_stats.skipped.errors += 1
                    total_skipped.errors += 1
                    continue

                ignored, reason = rules.is_file_ignored(rel_path)
                if ignored:
                    if reason == "allowlist":
                        root_stats.skipped.allowlist += 1
                        total_skipped.allowlist += 1
                    else:
                        root_stats.skipped.ignored += 1
                        total_skipped.ignored += 1
                    continue

                try:
                    stat = item.stat(follow_symlinks=False)
                except OSError:
                    root_stats.skipped.errors += 1
                    total_skipped.errors += 1
                    continue

                if max_file_bytes is not None and stat.st_size > max_file_bytes:
                    root_stats.skipped.too_large += 1
                    total_skipped.too_large += 1
                    continue

                root_stats.files += 1
                root_stats.bytes += stat.st_size
                all_entries.append(ScanEntry(
                    rel_path=rel_path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                ))

        per_top[ROOT_ENTRY] = root_stats
        if on_entry_done and not stopped:
            on_entry_done(root_stats)

    if stopped:
        return all_entries, per_top, total_skipped

    # Process top-level directories
    top_dirs.sort(key=lambda d: d.name.lower())
    for top_dir in top_dirs:
        if should_stop and should_stop():
            stopped = True
            break

        dir_name = top_dir.name
        dir_stats = TopLevelStats(name=dir_name, type="dir")

        if not rules.top_level_selected(dir_name):
            per_top[dir_name] = dir_stats
            if on_entry_done:
                on_entry_done(dir_stats)
            continue

        if rules.is_dir_ignored(dir_name):
            per_top[dir_name] = dir_stats
            if on_entry_done:
                on_entry_done(dir_stats)
            continue

        # Walk this subtree
        dir_stopped = False
        stack: list[str] = [os.path.join(project_path, dir_name)]
        while stack:
            if should_stop and should_stop():
                dir_stopped = True
                break

            current = stack.pop()
            try:
                sub_items = list(os.scandir(current))
            except OSError:
                dir_stats.skipped.errors += 1
                total_skipped.errors += 1
                continue

            for sub in sub_items:
                try:
                    is_symlink = sub.is_symlink()
                    is_dir = sub.is_dir(follow_symlinks=False)
                except OSError:
                    dir_stats.skipped.errors += 1
                    total_skipped.errors += 1
                    continue

                # Build relative path with "/" separators
                full = sub.path
                rel = os.path.relpath(full, project_path).replace(os.sep, "/")

                if is_symlink:
                    dir_stats.skipped.symlinks += 1
                    total_skipped.symlinks += 1
                    continue

                if is_dir:
                    if rules.is_dir_ignored(rel):
                        dir_stats.skipped.ignored += 1
                        total_skipped.ignored += 1
                        continue
                    stack.append(full)
                    continue

                # Regular file
                file_count += 1
                if should_stop and file_count % 1000 == 0 and should_stop():
                    dir_stopped = True
                    break

                # Validate path
                if _has_control_char(rel) or not _path_components_valid(rel):
                    dir_stats.skipped.errors += 1
                    total_skipped.errors += 1
                    continue

                ignored, reason = rules.is_file_ignored(rel)
                if ignored:
                    if reason == "allowlist":
                        dir_stats.skipped.allowlist += 1
                        total_skipped.allowlist += 1
                    else:
                        dir_stats.skipped.ignored += 1
                        total_skipped.ignored += 1
                    continue

                try:
                    stat = sub.stat(follow_symlinks=False)
                except OSError:
                    dir_stats.skipped.errors += 1
                    total_skipped.errors += 1
                    continue

                if max_file_bytes is not None and stat.st_size > max_file_bytes:
                    dir_stats.skipped.too_large += 1
                    total_skipped.too_large += 1
                    continue

                dir_stats.files += 1
                dir_stats.bytes += stat.st_size
                all_entries.append(ScanEntry(
                    rel_path=rel,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                ))

        if dir_stopped:
            stopped = True

        per_top[dir_name] = dir_stats
        if on_entry_done and not stopped:
            on_entry_done(dir_stats)

        if stopped:
            break

    return all_entries, per_top, total_skipped


def dry_run(
    project_path: str,
    rules: IgnoreRules,
    *,
    max_file_bytes: int | None = None,
    on_entry_done: Callable[[TopLevelStats], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Run a scan without uploading, returning a summary dict."""
    entries, per_top, total_skipped = scan_project(
        project_path,
        rules,
        max_file_bytes=max_file_bytes,
        on_entry_done=on_entry_done,
        should_stop=should_stop,
    )

    total_files = sum(s.files for s in per_top.values())
    total_bytes = sum(s.bytes for s in per_top.values())

    was_stopped = should_stop is not None and should_stop()

    return {
        "entries": {name: stats.as_dict() for name, stats in per_top.items()},
        "total": {
            "files": total_files,
            "bytes": total_bytes,
            "skipped": total_skipped.as_dict(),
        },
        "stopped": was_stopped,
    }
