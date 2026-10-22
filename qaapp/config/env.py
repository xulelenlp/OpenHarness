"""qaapp 环境变量分层加载。

集中后的环境文件位于 ``config/backend``：

    .env            # 公共非敏感默认值
    .env.<env>      # dev / prod 环境值（QAAPP_ENV 选择）
    .env.local      # 本机/部署私密值（不提交）

兼容旧位置：``config/.env``、ohmo workspace 下的 ``.env``、``<cwd>/.env``。
进程环境变量始终优先于文件值。
"""

from __future__ import annotations

import os
from pathlib import Path

from openharness.config.layers import expand_env, read_env_file
from qaapp.config.paths import (
    BACKEND_CONFIG_DIRNAME,
    ENV_CONFIG_DIR,
    PROJECT_CONFIG_DIRNAME,
    get_qaapp_env,
)

__all__ = [
    "apply_env_layers",
    "env_layer_paths",
    "load_dotenv",
    "merged_env",
    "_env_with_dotenv",
    "_substitute",
]


def _backend_env_paths(backend: Path, env: str) -> list[Path]:
    return [
        backend / ".env",
        backend / f".env.{env}",
        backend / ".env.local",
    ]


def env_layer_paths(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
    env: str | None = None,
) -> list[Path]:
    """Return env-file candidates, lowest priority first."""
    selected_env = (env or get_qaapp_env()).strip().lower()
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    candidates: list[Path] = []

    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "backend").is_dir():
            candidates.extend(_backend_env_paths(root / "backend", selected_env))
        elif root.name == BACKEND_CONFIG_DIRNAME:
            candidates.extend(_backend_env_paths(root, selected_env))
        else:
            candidates.extend(
                [root / ".env", root / f".env.{selected_env}", root / ".env.local"]
            )
        return candidates

    if cwd:
        root = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME
        backend = root / BACKEND_CONFIG_DIRNAME
        if backend.is_dir():
            candidates.extend(_backend_env_paths(backend, selected_env))
        # Legacy flat location stays readable during migration.
        candidates.append(root / ".env")

    from ohmo.workspace import get_workspace_root

    candidates.append(get_workspace_root(workspace) / ".env")
    if cwd:
        candidates.append(Path(cwd).expanduser().resolve() / ".env")
    return candidates


def load_dotenv(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """Read env files in priority order (later files win over earlier ones)."""
    values: dict[str, str] = {}
    for path in env_layer_paths(workspace, cwd=cwd, config_dir=config_dir):
        if path.is_file():
            values.update(read_env_file(path))
    return values


def merged_env(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """File values plus the process environment (process always wins)."""
    env = load_dotenv(workspace, cwd=cwd, config_dir=config_dir)
    env.update({key: value for key, value in os.environ.items() if value is not None})
    return env


def apply_env_layers(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """Load env files into ``os.environ`` without overriding existing values."""
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
    """Backward-compatible alias for :func:`merged_env`."""
    return merged_env(workspace, cwd=cwd, config_dir=config_dir)


#: Backward-compatible alias used by the old datasources loader.
_substitute = expand_env
