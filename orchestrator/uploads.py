"""Upload helpers — git-exclude management for per-project upload dirs."""

import logging
import os
import subprocess

logger = logging.getLogger("orchestrator")


def ensure_git_exclude(proj_path: str) -> None:
    """Ensure ``.xylocopa/`` is listed in the repo's git exclude file.

    Only acts when ``<proj_path>/.git`` exists (regular dir or worktree
    gitdir file).  Uses ``git rev-parse --git-path info/exclude`` to find
    the correct exclude file (works for both normal repos and worktrees).

    All errors are swallowed — this is a best-effort convenience so
    uploaded attachments don't show up as untracked files.
    """
    try:
        dot_git = os.path.join(proj_path, ".git")
        if not os.path.exists(dot_git):
            return

        result = subprocess.run(
            ["git", "-C", proj_path, "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return

        exclude_path = result.stdout.strip()
        # git may return a relative path — resolve against proj_path
        if not os.path.isabs(exclude_path):
            exclude_path = os.path.join(proj_path, exclude_path)
        exclude_path = os.path.normpath(exclude_path)

        # Read existing lines to check idempotency
        line = ".xylocopa/"
        if os.path.isfile(exclude_path):
            with open(exclude_path, "r") as f:
                existing = f.read()
            if line in existing.splitlines():
                return

        # Create info/ dir if needed, then append
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        with open(exclude_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        logger.debug("ensure_git_exclude failed for %s", proj_path, exc_info=True)
