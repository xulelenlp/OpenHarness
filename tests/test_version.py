"""The runtime version must match the packaging metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from openharness.version import __version__


def test_version_matches_pyproject():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert __version__ == project["version"]
