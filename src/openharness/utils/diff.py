"""Unified-diff helpers shared by file-editing tools."""

from __future__ import annotations

import difflib

__all__ = ["unified_diff_with_counts"]


def unified_diff_with_counts(
    filename: str,
    original: str,
    updated: str,
) -> tuple[str, int, int]:
    """Return ``(diff_text, added_lines, removed_lines)`` for a file edit.

    The rendering is intentionally identical to the historical private helper
    in ``file_edit_tool`` / ``file_write_tool`` so approval previews do not
    change.
    """
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=filename,
            tofile=filename,
            lineterm="",
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return "".join(diff_lines), added, removed
