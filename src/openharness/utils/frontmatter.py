"""YAML frontmatter parsing shared by skills and plugins."""

from __future__ import annotations

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

__all__ = ["parse_markdown_frontmatter"]


def parse_markdown_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown document into ``(frontmatter, body)``.

    Only the conventional leading ``---`` block is recognised. Invalid YAML
    or a non-mapping document yields an empty mapping and leaves the original
    content untouched, matching the historical lenient behaviour of the
    plugin and skill loaders.
    """
    if not content.startswith("---\n"):
        return {}, content
    end_index = content.find("\n---\n", 4)
    if end_index == -1:
        return {}, content
    raw_frontmatter = content[4:end_index]
    body = content[end_index + len("\n---\n") :]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        logger.debug("Failed to parse markdown frontmatter", exc_info=True)
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, body.strip()
