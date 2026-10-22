"""Shared human-readable formatting helpers."""

from __future__ import annotations


def format_bytes(size: int) -> str:
    """Format a byte count as ``123B`` / ``1.2KB`` / ``3.45MB``."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.2f}MB"


__all__ = ["format_bytes"]
