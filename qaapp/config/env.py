"""qaapp 环境变量加载。

后端只读取一个文件：``config/backend/.env``。开发/生产切换由用户复制模板完成：

    cp config/backend/.env_dev  config/backend/.env
    cp config/backend/.env_prod config/backend/.env

进程环境变量始终优先于文件值。
"""

from __future__ import annotations

import os
from pathlib import Path

from openharness.config.layers import expand_env, read_env_file
from qaapp.config.paths import get_env_file_path

__all__ = [
    "apply_env_layers",
    "env_file",
    "load_dotenv",
    "merged_env",
    "_env_with_dotenv",
    "_substitute",
]


def env_file(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    return get_env_file_path(workspace, cwd=cwd, config_dir=config_dir)


def load_dotenv(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """Read the single effective ``.env`` file."""
    return read_env_file(env_file(workspace, cwd=cwd, config_dir=config_dir))


def merged_env(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """File values plus the process environment (process always wins)."""
    values = load_dotenv(workspace, cwd=cwd, config_dir=config_dir)
    values.update({key: value for key, value in os.environ.items() if value is not None})
    return values


def apply_env_layers(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """Load ``.env`` into ``os.environ`` without overriding existing values."""
    values = load_dotenv(workspace, cwd=cwd, config_dir=config_dir)
    applied: dict[str, str] = {}
    for key, value in values.items():
        if key in os.environ:
            continue
        os.environ[key] = value
        applied[key] = value
    return applied


def _env_with_dotenv(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    return merged_env(workspace, cwd=cwd, config_dir=config_dir)


_substitute = expand_env
