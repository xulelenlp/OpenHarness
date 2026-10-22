"""Repository-relative paths shared by the qaapp application."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"
PLUGINS_DIR = REPO_ROOT / "plugins"
CONFIG_DIR = REPO_ROOT / "config"
BACKEND_CONFIG_DIR = CONFIG_DIR / "backend"

__all__ = [
    "BACKEND_CONFIG_DIR",
    "CONFIG_DIR",
    "PLUGINS_DIR",
    "REPO_ROOT",
    "SKILLS_DIR",
]
