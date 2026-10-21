"""qaapp 配置加载、校验与状态汇总。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ohmo.workspace import get_workspace_root
from qaapp.config.models import (
    AgentRegistry,
    DangerousActions,
    WebappConfig,
)
from qaapp.config.paths import (
    ENV_CONFIG_DIR,
    KIND_EXTERNAL,
    KIND_SUBAGENT,
    get_agents_config_path,
    get_dangerous_config_path,
    get_datasources_config_path,
    get_project_config_dir,
    get_webapp_config_path,
)


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


def _load_json_model(path: Path, model: type[BaseModel]) -> tuple[BaseModel, str | None]:
    """读取并校验一个 JSON 配置文件。

    返回 ``(model, error)``；文件缺失返回默认模型与 None；内容非法返回默认模型
    与错误信息（不抛出，便于服务启动时仅告警）。
    """
    if not path.exists():
        return model(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return model(), f"无法读取 {path}: {exc}"
    try:
        return model.model_validate(raw), None
    except (ValueError, OSError) as exc:  # pydantic ValidationError 等
        return model(), f"配置非法 {path}: {exc}"


def _resolve_kwargs(
    cwd: str | Path | None, config_dir: str | Path | None
) -> dict[str, Any]:
    return {"cwd": cwd, "config_dir": config_dir}


def load_webapp_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> WebappConfig:
    config, _ = _load_json_model(
        get_webapp_config_path(workspace, **_resolve_kwargs(cwd, config_dir)), WebappConfig
    )
    return config  # type: ignore[return-value]


def load_agent_registry(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> AgentRegistry:
    registry, _ = _load_json_model(
        get_agents_config_path(workspace, **_resolve_kwargs(cwd, config_dir)), AgentRegistry
    )
    return registry  # type: ignore[return-value]


def load_dangerous_actions(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> DangerousActions:
    config, _ = _load_json_model(
        get_dangerous_config_path(workspace, **_resolve_kwargs(cwd, config_dir)),
        DangerousActions,
    )
    return config  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _validation_error(path: Path, exc: Exception) -> str:
    return f"配置非法 {path}: {exc}"


def validate_webapp_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回 webapp.json 的校验错误列表（空 = 合法）。"""
    path = get_webapp_config_path(workspace, **_resolve_kwargs(cwd, config_dir))
    if not path.exists():
        return []
    try:
        WebappConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]
    return []


def validate_agent_registry(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回 agents.json 的校验错误列表（空 = 合法）。"""
    path = get_agents_config_path(workspace, **_resolve_kwargs(cwd, config_dir))
    if not path.exists():
        return []
    errors: list[str] = []
    try:
        registry = AgentRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]

    ids: set[str] = set()
    for agent in registry.agents:
        if not agent.id:
            errors.append(f"{path}: agent 缺少 id")
            continue
        if agent.id in ids:
            errors.append(f"{path}: agent id 重复: {agent.id}")
        ids.add(agent.id)
        if agent.kind == KIND_EXTERNAL and not agent.external_ref and agent.external is None:
            errors.append(
                f"{path}: agent '{agent.id}' 为 external 但既没有 external 内联配置，"
                "也没有 external_ref"
            )
        if agent.kind == KIND_SUBAGENT and not agent.subagent_type:
            errors.append(f"{path}: agent '{agent.id}' 为 subagent 但缺少 subagent_type")

    known_ids = {agent.id for agent in registry.agents}
    if registry.default_agent and registry.default_agent not in known_ids:
        errors.append(f"{path}: default_agent '{registry.default_agent}' 未在 agents 中定义")
    for rule in registry.routing.rules:
        if rule.agent not in known_ids:
            errors.append(f"{path}: 路由规则指向未知 agent: {rule.agent}")
    return errors


def validate_dangerous_actions(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回 dangerous-actions.json 的校验错误列表（空 = 合法）。"""
    path = get_dangerous_config_path(workspace, **_resolve_kwargs(cwd, config_dir))
    if not path.exists():
        return []
    try:
        DangerousActions.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]
    return []


def config_status(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, Any]:
    """返回配置文件的**实际解析路径**、存在性与校验结果摘要。"""
    kwargs = _resolve_kwargs(cwd, config_dir)
    paths = {
        "webapp": get_webapp_config_path(workspace, **kwargs),
        "agents": get_agents_config_path(workspace, **kwargs),
        "dangerous": get_dangerous_config_path(workspace, **kwargs),
        "datasources": get_datasources_config_path(workspace, **kwargs),
    }
    validators = {
        "webapp": validate_webapp_config,
        "agents": validate_agent_registry,
        "dangerous": validate_dangerous_actions,
        "datasources": _validate_datasources,
    }
    project_dir = get_project_config_dir(cwd, config_dir)
    result: dict[str, Any] = {
        "workspace": str(get_workspace_root(workspace)),
        "project_config_dir": str(project_dir) if project_dir else "",
    }
    for name, path in paths.items():
        result[name] = {
            "path": str(path),
            "exists": path.exists(),
            "source": (
                "project"
                if project_dir is not None and path.parent == project_dir
                else ("explicit" if config_dir or os.environ.get(ENV_CONFIG_DIR) else "workspace")
            ),
            "errors": validators[name](workspace, **kwargs),
        }
    return result


def _validate_datasources(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """延迟导入，避免 qaapp.config 与 qaapp.data.datasources 循环依赖。"""
    from qaapp.data.datasources import validate_datasources_config

    return validate_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
