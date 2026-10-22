"""qaapp 配置文件路径解析。

配置集中在一个根目录（默认 ``<cwd>/config``）之下，后端配置按
``backend/base → backend/<env> → backend/local`` 分层。为了兼容已有部署，
仍然接受旧扁平位置（``<cwd>/config/<name>``、ohmo workspace 下的同名文件）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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
BASE_LAYER = "base"
LOCAL_LAYER = "local"
DEFAULT_ENV = "dev"
QAAPP_ENVS = ("dev", "prod")

#: 显式指定配置目录的环境变量。
ENV_CONFIG_DIR = "QAAPP_CONFIG_DIR"
#: 选择 dev / prod 环境层。
ENV_QAAPP_ENV = "QAAPP_ENV"

KIND_EXTERNAL = "external"
KIND_SUBAGENT = "subagent"
KIND_HARNESS = "harness"
AGENT_KINDS = (KIND_EXTERNAL, KIND_SUBAGENT, KIND_HARNESS)

DANGEROUS_ACTIONS = ("allow", "confirm", "deny")
ROUTING_MODES = ("rule_then_llm", "llm", "rule", "explicit")


@dataclass(frozen=True)
class ConfigLayer:
    """A single optional configuration layer."""

    name: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def get_qaapp_env() -> str:
    """Return the selected environment layer (``dev`` by default)."""
    value = str(os.environ.get(ENV_QAAPP_ENV) or DEFAULT_ENV).strip().lower()
    return value or DEFAULT_ENV


def get_project_config_dir(
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path | None:
    """返回项目的可见配置根目录。

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


def _layered_backend_dir(root: Path) -> Path | None:
    """Return the backend config root when ``root`` uses the layered layout."""
    if (root / BASE_LAYER).is_dir():
        return root
    nested = root / BACKEND_CONFIG_DIRNAME
    if (nested / BASE_LAYER).is_dir():
        return nested
    return None


def get_backend_config_dir(
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path | None:
    """返回后端的配置目录（分层布局时是 ``.../backend``）。"""
    root = get_project_config_dir(cwd, config_dir)
    if root is None:
        return None
    layered = _layered_backend_dir(root)
    if layered is not None:
        return layered
    if root.name == BACKEND_CONFIG_DIRNAME:
        return root
    candidate = root / BACKEND_CONFIG_DIRNAME
    return candidate if candidate.is_dir() else None


def _layers_for_backend(backend: Path, filename: str, env: str) -> list[ConfigLayer]:
    return [
        ConfigLayer(BASE_LAYER, backend / BASE_LAYER / filename),
        ConfigLayer(env, backend / env / filename),
        ConfigLayer(LOCAL_LAYER, backend / LOCAL_LAYER / filename),
    ]


def resolve_config_layers(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
    env: str | None = None,
) -> list[ConfigLayer]:
    """Return candidate layers for ``filename``, lowest priority first.

    The workspace fallback is only returned when no project-level file exists,
    matching the historical "project config wins, workspace is fallback"
    behavior.
    """
    selected_env = (env or get_qaapp_env()).strip().lower() or DEFAULT_ENV
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    project_layers: list[ConfigLayer] = []

    if explicit:
        root = Path(explicit).expanduser().resolve()
        backend = _layered_backend_dir(root)
        if backend is not None:
            project_layers = _layers_for_backend(backend, filename, selected_env)
        elif root.name == BACKEND_CONFIG_DIRNAME:
            project_layers = _layers_for_backend(root, filename, selected_env)
        else:
            project_layers = [ConfigLayer("explicit", root / filename)]
        # Explicit configuration always wins, even when the file is missing.
        return project_layers

    if cwd:
        root = Path(cwd).expanduser().resolve() / PROJECT_CONFIG_DIRNAME
        backend = _layered_backend_dir(root) or (
            root / BACKEND_CONFIG_DIRNAME
            if (root / BACKEND_CONFIG_DIRNAME / BASE_LAYER).is_dir()
            else None
        )
        if backend is not None:
            project_layers = _layers_for_backend(backend, filename, selected_env)
        else:
            flat = root / filename
            if flat.is_file():
                project_layers = [ConfigLayer("project", flat)]

    if any(layer.exists for layer in project_layers):
        return project_layers
    return [ConfigLayer("workspace", get_workspace_root(workspace) / filename)]


def resolve_config_path(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
    env: str | None = None,
) -> Path:
    """返回该配置实际生效的文件路径（用于展示/报错）。"""
    layers = resolve_config_layers(
        filename, workspace, cwd=cwd, config_dir=config_dir, env=env
    )
    existing = [layer.path for layer in layers if layer.exists]
    if existing:
        return existing[-1]
    return layers[-1].path if layers else get_workspace_root(workspace) / filename


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
