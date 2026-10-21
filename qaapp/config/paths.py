"""qaapp 配置文件的路径解析（显式目录 → 项目 config/ → workspace）。"""

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
    """返回项目的可见配置目录。

    - 显式 ``config_dir``（或 ``QAAPP_CONFIG_DIR``）总是生效（即使目录还不存在）；
    - 否则仅当 ``<cwd>/config`` 存在时启用；
    - ``cwd`` 为空（未显式传入）时返回 ``None``，避免隐式依赖进程当前目录。
    """
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not cwd:
        return None
    project = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME
    return project if project.is_dir() else None


def resolve_config_path(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    """按「显式目录 → 项目 config/ → workspace」解析配置文件路径。"""
    project_dir = get_project_config_dir(cwd, config_dir)
    if project_dir is not None:
        candidate = project_dir / filename
        if candidate.is_file():
            return candidate
        # 显式指定的目录即使文件缺失也以它为准（便于报错/写入示例）。
        if config_dir or os.environ.get(ENV_CONFIG_DIR):
            return candidate
    return get_workspace_root(workspace) / filename


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
