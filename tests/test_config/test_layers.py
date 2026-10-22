"""Tests for the shared layered-config primitives."""

from __future__ import annotations

import json
from pathlib import Path

from openharness.config.layers import (
    deep_merge,
    expand_env,
    load_env_file,
    load_json_layers,
)


def test_deep_merge_merges_objects_and_replaces_lists():
    base = {"a": {"b": 1, "c": [1, 2]}, "keep": True}
    override = {"a": {"b": 2, "c": [3]}}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 2, "c": [3]}, "keep": True}
    # inputs are not mutated
    assert base["a"]["b"] == 1
    assert base["a"]["c"] == [1, 2]


def test_expand_env_recurses_and_missing_becomes_empty():
    value = {"host": "${DB_HOST}", "nested": ["${MISSING}", 1], "plain": "x"}
    assert expand_env(value, {"DB_HOST": "127.0.0.1"}) == {
        "host": "127.0.0.1",
        "nested": ["", 1],
        "plain": "x",
    }


def test_load_json_layers_skips_missing_files(tmp_path: Path):
    base = tmp_path / "base.json"
    override = tmp_path / "override.json"
    base.write_text(json.dumps({"a": 1, "b": {"x": 1}}), encoding="utf-8")
    override.write_text(json.dumps({"b": {"y": 2}}), encoding="utf-8")
    merged = load_json_layers([base, tmp_path / "missing.json", override])
    assert merged == {"a": 1, "b": {"x": 1, "y": 2}}


def test_load_env_file(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport A=1\nB='two'\nC=\"three\"\n",
        encoding="utf-8",
    )
    assert load_env_file(env_file) == {"A": "1", "B": "two", "C": "three"}
