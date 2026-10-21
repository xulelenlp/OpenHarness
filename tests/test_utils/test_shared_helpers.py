"""Tests for helpers extracted during the structure refactor."""

from __future__ import annotations

from pathlib import Path

from openharness.utils.diff import unified_diff_with_counts
from openharness.utils.frontmatter import parse_markdown_frontmatter
from openharness.utils.fs import resolve_path
from openharness.utils.git import find_git_root


def test_unified_diff_counts_added_and_removed_lines():
    diff, added, removed = unified_diff_with_counts("demo.txt", "a\nb\n", "a\nc\n")
    assert "+c" in diff
    assert "-b" in diff
    assert (added, removed) == (1, 1)


def test_resolve_path_handles_relative_absolute_and_none(tmp_path: Path):
    assert resolve_path(tmp_path, "sub/file.txt") == (tmp_path / "sub/file.txt").resolve()
    absolute = tmp_path / "absolute.txt"
    assert resolve_path(tmp_path, str(absolute)) == absolute.resolve()
    assert resolve_path(tmp_path, None) == tmp_path.resolve()


def test_parse_markdown_frontmatter_returns_mapping_and_body():
    frontmatter, body = parse_markdown_frontmatter("---\nname: demo\ntags: [a, b]\n---\n\nHello\n")
    assert frontmatter == {"name": "demo", "tags": ["a", "b"]}
    assert body == "Hello"


def test_parse_markdown_frontmatter_without_block_is_passthrough():
    frontmatter, body = parse_markdown_frontmatter("Hello\n")
    assert frontmatter == {}
    assert body == "Hello\n"


def test_find_git_root_walks_up(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_git_root(nested) == tmp_path
