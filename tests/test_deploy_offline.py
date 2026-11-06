"""deploy/ 精简构建与部署脚本的轻量契约测试（不做网络下载）。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
BUILD_IMAGE = DEPLOY / "build_image.py"
BUILD_SH = DEPLOY / "build.sh"
RUN_SH = DEPLOY / "run.sh"


def _load_builder():
    spec = importlib.util.spec_from_file_location("deploy_build_image", BUILD_IMAGE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_layout_is_simple():
    expected = {
        "build.sh",
        "build_image.py",
        "constraints.txt",
        "entrypoint.sh",
        "run.sh",
        "README.md",
    }
    assert {path.name for path in DEPLOY.iterdir() if path.is_file()} == expected


def test_scripts_exist_and_executable():
    assert BUILD_SH.is_file()
    assert RUN_SH.is_file()
    assert BUILD_IMAGE.is_file()
    assert os.access(BUILD_SH, os.X_OK)
    assert os.access(RUN_SH, os.X_OK)


def test_split_image_variants():
    builder = _load_builder()
    assert builder._split_image("python:3.11-bookworm") == (
        "library/python",
        "3.11-bookworm",
    )
    assert builder._split_image("library/python") == ("library/python", "latest")
    assert builder._split_image("registry.local:5000/ns/app:tag") == (
        "registry.local:5000/ns/app",
        "tag",
    )


def test_normalize_repo():
    builder = _load_builder()
    assert builder._normalize_repo("python") == "library/python"
    assert builder._normalize_repo("library/python") == "library/python"
    assert builder._normalize_repo("ghcr.io/ns/app") == "ghcr.io/ns/app"


def test_builder_writes_docker_save_layout():
    text = BUILD_IMAGE.read_text(encoding="utf-8")
    for token in (
        '"manifest.json"',
        '"config.json"',
        '"layer.tar"',
        "rootfs",
        "diff_ids",
        "Entrypoint",
        "python:3.11-bookworm",
        "/app/deploy/entrypoint.sh",
    ):
        assert token in text, token


def test_builder_supports_offline_rebuild():
    text = BUILD_IMAGE.read_text(encoding="utf-8")
    assert "--from-image" in text
    assert "update_app" in text
    assert "app/vendor" in text


def test_docs_mention_build_and_deploy():
    text = (ROOT / "docs" / "qaapp" / "BUILD_AND_DEPLOY.zh-CN.md").read_text(
        encoding="utf-8"
    )
    assert "deploy/build.sh" in text
    assert "docker load" in text
    assert "config/backend/.env" in text
