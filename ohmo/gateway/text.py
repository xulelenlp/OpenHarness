"""Small text helpers shared by the ohmo gateway bridge and runtime."""

from __future__ import annotations

__all__ = ["content_snippet"]


def content_snippet(text: str, *, limit: int = 160) -> str:
    """Return a single-line preview suitable for logs."""
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
