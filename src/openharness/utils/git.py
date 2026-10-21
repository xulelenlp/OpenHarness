"""Small git-related filesystem helpers shared across packages."""

from __future__ import annotations

from pathlib import Path

__all__ = ["find_git_root"]


def find_git_root(start: str | Path) -> Path | None:
    """Return the nearest ancestor containing a ``.git`` entry, if any.

    Works for both normal repositories (``.git`` directory) and worktrees
    (``.git`` file).
    """
    current = Path(start)
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
