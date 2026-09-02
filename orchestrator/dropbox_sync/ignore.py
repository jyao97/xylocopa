"""Ignore-rule evaluation for Dropbox sync — gitignore semantics via pathspec."""

import logging
import os

import pathspec

logger = logging.getLogger("orchestrator.dropbox.ignore")

DEFAULT_IGNORE_RULES: list[str] = [
    ".git/",
    "*venv*/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".cache/",
    "*.egg-info/",
    "build/",
    "dist/",
    "torch_home/",
    ".thumbcache/",
    "wandb/",
    ".trash/",
    ".xylo-internal/",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
]

SYNCIGNORE_FILENAME = ".xylocopa-syncignore"

ROOT_ENTRY = "."


def parse_rules(text: str | None) -> list[str]:
    """Parse a rules text block, dropping blank lines and '#' comments."""
    if not text:
        return []
    result = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            result.append(stripped)
    return result


class IgnoreRules:
    """Compiled ignore rules for a project, combining defaults, syncignore, and extras."""

    def __init__(
        self,
        spec: pathspec.GitIgnoreSpec,
        default_spec: pathspec.GitIgnoreSpec,
        folders: set[str] | None,
        allowlist_exts: set[str] | None,
        rules_lines: list[str],
    ) -> None:
        self._spec = spec
        self._default_spec = default_spec
        self._folders = folders
        self._allowlist_exts = allowlist_exts
        self._rules_lines = rules_lines

    @classmethod
    def build(
        cls,
        project_path: str,
        *,
        folders: list[str] | None = None,
        extra_rules: str | None = None,
        allowlist_exts: set[str] | None = None,
        include_defaults: bool = True,
        read_syncignore: bool = True,
    ) -> "IgnoreRules":
        """Build compiled ignore rules from defaults, syncignore file, and extras."""
        all_rules: list[str] = []

        if include_defaults:
            all_rules.extend(DEFAULT_IGNORE_RULES)

        if read_syncignore:
            syncignore_path = os.path.join(project_path, SYNCIGNORE_FILENAME)
            if os.path.isfile(syncignore_path):
                try:
                    with open(syncignore_path, "r", encoding="utf-8") as f:
                        all_rules.extend(parse_rules(f.read()))
                except OSError:
                    logger.warning("Failed to read %s", syncignore_path)

        if extra_rules:
            all_rules.extend(parse_rules(extra_rules))

        spec = pathspec.GitIgnoreSpec.from_lines(all_rules)
        default_spec = pathspec.GitIgnoreSpec.from_lines(DEFAULT_IGNORE_RULES)
        folder_set = set(folders) if folders is not None else None

        return cls(
            spec=spec,
            default_spec=default_spec,
            folders=folder_set,
            allowlist_exts=allowlist_exts,
            rules_lines=all_rules,
        )

    def top_level_selected(self, name: str) -> bool:
        """Return True if *name* (a top-level entry or ROOT_ENTRY) is selected."""
        if self._folders is None:
            return True
        return name in self._folders

    def is_dir_ignored(self, rel_dir: str) -> bool:
        """Return True if the directory at *rel_dir* should be ignored.

        *rel_dir* uses "/" separators, no trailing "/".
        """
        return self._spec.match_file(rel_dir + "/")

    def is_file_ignored(self, rel_file: str) -> tuple[bool, str | None]:
        """Return (ignored, reason) for a file at *rel_file*.

        reason is "folder" when the file's top-level dir is not selected,
        "rule" when an ignore rule matches, "allowlist" when the extension
        is not in the allowlist, or None when not ignored.
        """
        # Folder selection check
        parts = rel_file.split("/")
        if len(parts) == 1:
            # Root file
            if self._folders is not None and ROOT_ENTRY not in self._folders:
                return True, "folder"
        else:
            top = parts[0]
            if self._folders is not None and top not in self._folders:
                return True, "folder"

        # Rule check
        if self._spec.match_file(rel_file):
            return True, "rule"

        # Allowlist check
        if self._allowlist_exts is not None:
            _, ext = os.path.splitext(rel_file)
            basename = os.path.basename(rel_file)
            if ext:
                if ext.lower() not in self._allowlist_exts:
                    return True, "allowlist"
            else:
                # Extensionless file — match by full basename
                if basename not in self._allowlist_exts:
                    return True, "allowlist"

        return False, None

    def is_default_ignored_dir(self, name: str) -> bool:
        """Return True if *name* matches the DEFAULT ignore rules (for picker badge)."""
        return self._default_spec.match_file(name + "/")

    @property
    def rules_text(self) -> str:
        """Return the effective rules joined, for status/debug."""
        return "\n".join(self._rules_lines)
