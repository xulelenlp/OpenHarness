"""deploy/ 精简构建与部署契约测试（不执行 Docker）。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
BUILD_SH = DEPLOY / "build.sh"
DOCKERFILE = DEPLOY / "Dockerfile"
RUN_SH = DEPLOY / "run.sh"


def test_deploy_layout_is_simple():
    expected = {
        "build.sh",
        "Dockerfile",
        "entrypoint.sh",
        "run.sh",
        "README.md",
    }
    assert {path.name for path in DEPLOY.iterdir() if path.is_file()} == expected


def test_scripts_exist_and_executable():
    for path in (BUILD_SH, RUN_SH, DEPLOY / "entrypoint.sh"):
        assert path.is_file(), path
        assert os.access(path, os.X_OK), path


def test_dockerfile_is_incremental_from_local_base():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for token in (
        "ARG BASE_IMAGE=openharness-qaapp:0.1.9-offline",
        "FROM ${BASE_IMAGE}",
        "COPY --chown=1000:1000 src /app/src",
        "COPY --chown=1000:1000 qaapp /app/qaapp",
        "COPY --chown=1000:1000 frontend/webapp/dist",
        "COPY --chown=1000:1000 config/backend/.env_prod",
        "USER 1000:1000",
        'ENTRYPOINT ["/app/deploy/entrypoint.sh"]',
    ):
        assert token in text, token
    assert "config/backend/.env " not in text


def test_build_uses_docker_and_local_base_only():
    text = BUILD_SH.read_text(encoding="utf-8")
    for token in (
        "docker image inspect",
        "--env",
        "env_files/env_",
        "docker build",
        "--network none",
        "--build-arg \"BASE_IMAGE=$BASE\"",
        "docker save",
        "docker run --rm",
        "dist/qaapp-simple",
    ):
        assert token in text, token
    assert "pip install" not in text
    assert "curl " not in text


def test_run_script_mounts_project_config():
    text = RUN_SH.read_text(encoding="utf-8")
    for token in (
        "SCRIPT_DIR",
        "CONFIG=",
        "/app/config/backend:ro",
        "/root/qaapp-data",
        "--user 0:0",
        "/healthz",
    ):
        assert token in text, token


def test_dockerignore_excludes_secrets_and_build_output():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for token in (".git", "dist", "config/backend/.env", "**/node_modules"):
        assert token in text, token


def test_docs_mention_build_and_deploy():
    text = (ROOT / "docs" / "qaapp" / "BUILD_AND_DEPLOY.zh-CN.md").read_text(
        encoding="utf-8"
    )
    assert "./deploy/build.sh --env pro" in text
    assert "docker load" in text
    assert "config/backend/.env" in text
