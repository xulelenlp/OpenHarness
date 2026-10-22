"""qaapp 配置文件路径解析。

配置只使用一个后端目录（默认 ``<cwd>/config/backend``）：

- ``config/backend/.env``：当前环境配置（由 ``.env_dev`` / ``.env_prod`` 复制而来）
- ``config/backend/<name>.json``：单份结构化配置，不做分层合并

旧部署仍可回退到 ``<cwd>/config/<name>`` 与 ohmo workspace 下的同名文件。
"""

from __future__ import annotations

import os
from pathlib import Path

from ohmo.workspace import get_workspace_root

# ---------------------------------------------------------------------------

WEBAPP_FILENAME = "webapp.json"
AGENTS_FILENAME = "agents.json"
DANGEROUS_FILENAME = "dangerous-actions.json"
#: 数据源配置：MySQL / Redis 的唯一维护文件（连接信息不写入 webapp.json/代码）。
DATASOURCES_FILENAME = "datasources.json"

#: 项目内可见配置目录（相对于 ``--cwd``），随 git 统一管理。
PROJECT_CONFIG_DIRNAME = "config"
BACKEND_CONFIG_DIRNAME = "backend"
ENV_FILENAME = ".env"

#: 显式指定配置目录的环境变量。
ENV_CONFIG_DIR = "QAAPP_CONFIG_DIR"

KIND_EXTERNAL = "external"
KIND_SUBAGENT = "subagent"
KIND_HARNESS = "harness"
AGENT_KINDS = (KIND_EXTERNAL, KIND_SUBAGENT, KIND_HARNESS)

DANGEROUS_ACTIONS = ("allow", "confirm", "deny")
ROUTING_MODES = ("rule_then_llm", "llm", "rule", "explicit")


def get_project_config_dir(
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path | None:
    """返回项目的可见配置根目录（``<cwd>/config``）。"""
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not cwd:
        return None
    project = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME
    return project if project.is_dir() else None


def get_backend_config_dir(
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path | None:
    """返回后端配置目录（通常是 ``config/backend``）。"""
    root = get_project_config_dir(cwd, config_dir)
    if root is None:
        return None
    if root.name == BACKEND_CONFIG_DIRNAME:
        return root
    candidate = root / BACKEND_CONFIG_DIRNAME
    return candidate if candidate.is_dir() else root


def _candidate_paths(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        candidates.append(root / filename)
        if root.name != BACKEND_CONFIG_DIRNAME:
            candidates.append(root / BACKEND_CONFIG_DIRNAME / filename)
    elif cwd:
        root = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME
        candidates.append(root / BACKEND_CONFIG_DIRNAME / filename)
        # 旧部署的扁平位置，仅作兼容。
        candidates.append(root / filename)
    candidates.append(get_workspace_root(workspace) / filename)
    return candidates


def resolve_config_path(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    """返回该配置实际生效的文件路径（第一个存在的候选）。"""
    candidates = _candidate_paths(
        filename, workspace, cwd=cwd, config_dir=config_dir
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def get_webapp_config_path(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    return resolve_config_path(WEBAPP_FILENAME, workspace, cwd=cwd, config_dir=config_dir)


def get_agents_config_path(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    return resolve_config_path(AGENTS_FILENAME, workspace, cwd=cwd, config_dir=config_dir)


def get_dangerous_config_path(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    return resolve_config_path(
        DANGEROUS_FILENAME, workspace, cwd=cwd, config_dir=config_dir
    )


def get_datasources_config_path(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    """返回数据源配置文件（datasources.json）的实际路径。"""
    return resolve_config_path(
        DATASOURCES_FILENAME, workspace, cwd=cwd, config_dir=config_dir
    )


def get_env_file_path(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    """返回唯一的环境配置文件 ``.env``。"""
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if root.name == BACKEND_CONFIG_DIRNAME:
            return root / ENV_FILENAME
        candidate = root / BACKEND_CONFIG_DIRNAME / ENV_FILENAME
        return candidate if candidate.is_file() or not (root / ENV_FILENAME).is_file() else root / ENV_FILENAME
    if cwd:
        backend = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME / BACKEND_CONFIG_DIRNAME
        if (backend / ENV_FILENAME).is_file():
            return backend / ENV_FILENAME
        legacy = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME / ENV_FILENAME
        if legacy.is_file():
            return legacy
    workspace_env = get_workspace_root(workspace) / ENV_FILENAME
    if workspace_env.is_file():
        return workspace_env
    if cwd:
        cwd_env = Path(cwd).expanduser().resolve() / ENV_FILENAME
        if cwd_env.is_file():
            return cwd_env
    return Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME / BACKEND_CONFIG_DIRNAME / ENV_FILENAME if cwd else workspace_env
