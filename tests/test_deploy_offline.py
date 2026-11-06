"""离线镜像构建脚本的轻量契约测试（不做网络下载）。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = ROOT / "deploy" / "offline" / "build_image.py"
BUILD_SH = ROOT / "deploy" / "build_offline.sh"


def _load_builder():
    spec = importlib.util.spec_from_file_location("deploy_build_image", OFFLINE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_scripts_exist_and_executable():
    assert OFFLINE.is_file()
    assert BUILD_SH.is_file()
    assert os.access(BUILD_SH, os.X_OK)


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
    text = OFFLINE.read_text(encoding="utf-8")
    for token in (
        '"manifest.json"',
        '"config.json"',
        '"layer.tar"',
        "rootfs",
        "diff_ids",
        "Entrypoint",
        "python:3.11-bookworm",
    ):
        assert token in text, token


def test_install_runs_image_runtime_selfcheck():
    text = (ROOT / "deploy" / "bundle" / "install.sh").read_text(encoding="utf-8")
    assert "selfcheck.py --runtime --strict" in text


def test_docs_mention_offline_build():
    text = (ROOT / "docs" / "qaapp" / "IMAGE_DEPLOY.zh-CN.md").read_text(
        encoding="utf-8"
    )
    assert "build_offline.sh" in text
