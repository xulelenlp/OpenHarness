"""部署包静态契约测试：compose、脚本、Dockerfile 关键项。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "deploy" / "bundle"
DOCKER = ROOT / "deploy" / "docker"


def test_compose_mounts_config_and_data():
    data = yaml.safe_load((BUNDLE / "docker-compose.yaml").read_text(encoding="utf-8"))
    service = data["services"]["qaapp"]
    volumes = "\n".join(service["volumes"])
    assert "/app/config/backend:ro" in volumes
    assert ":/data" in volumes
    assert service["image"].startswith("${QAAPP_IMAGE")
    assert service["healthcheck"]["test"][0] == "CMD"
    assert service["init"] is True


def test_compose_pins_container_port():
    data = yaml.safe_load((BUNDLE / "docker-compose.yaml").read_text(encoding="utf-8"))
    service = data["services"]["qaapp"]
    assert service["environment"]["QAAPP_PORT"] == "8787"
    assert service["ports"] == ["${QAAPP_BIND:-0.0.0.0}:${QAAPP_HOST_PORT:-8787}:8787"]


def test_bundle_scripts_exist_and_executable():
    for name in (
        "install.sh",
        "upgrade.sh",
        "backup.sh",
        "cleanup.sh",
        "uninstall.sh",
        "smoke.sh",
    ):
        path = BUNDLE / name
        assert path.is_file(), name
        assert os.access(path, os.X_OK), name


def test_dockerfile_runtime_dependencies_are_present():
    text = (DOCKER / "Dockerfile").read_text(encoding="utf-8")
    for token in (
        "fonts-noto-cjk",
        "tzdata",
        "ripgrep",
        "node:20-bookworm-slim",
        "selfcheck.py --runtime --strict",
        "pip install --no-cache-dir",
        "USER 1000:1000",
    ):
        assert token in text, token
    assert (DOCKER / "constraints.txt").is_file()


def test_dockerignore_excludes_secrets_and_heavy_dirs():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for token in (
        ".venv",
        ".conda",
        "**/node_modules",
        "config/backend/.env",
        "config/backend/.env_dev",
    ):
        assert token in text, token
