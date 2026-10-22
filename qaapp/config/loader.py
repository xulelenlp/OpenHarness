"""qaapp 配置加载、校验与状态汇总（分层实现）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ohmo.workspace import get_workspace_root
from openharness.config.layers import ConfigLayerError, deep_merge, expand_env, load_json_file
from qaapp.config.env import merged_env
from qaapp.config.models import AgentRegistry, DangerousActions, WebappConfig
from qaapp.config.paths import (
    AGENTS_FILENAME,
    DANGEROUS_FILENAME,
    DATASOURCES_FILENAME,
    ENV_CONFIG_DIR,
    KIND_EXTERNAL,
    KIND_SUBAGENT,
    WEBAPP_FILENAME,
    ConfigLayer,
    get_backend_config_dir,
    get_project_config_dir,
    get_qaapp_env,
    resolve_config_layers,
)

# ---------------------------------------------------------------------------
# 分层读取
# ---------------------------------------------------------------------------


def config_layers(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[ConfigLayer]:
    """Return the candidate layers for ``filename`` (lowest priority first)."""
    return resolve_config_layers(filename, workspace, cwd=cwd, config_dir=config_dir)


def load_config_dict(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[str], list[ConfigLayer]]:
    """Merge existing layers and expand ``${VAR}`` placeholders.

    Invalid layers are skipped and reported, so one broken override does not
    discard a valid base configuration.
    """
    layers = config_layers(filename, workspace, cwd=cwd, config_dir=config_dir)
    merged: dict[str, Any] = {}
    errors: list[str] = []
    for layer in layers:
        if not layer.exists:
            continue
        try:
            raw = load_json_file(layer.path)
        except ConfigLayerError as exc:
            errors.append(str(exc))
            continue
        merged = deep_merge(merged, raw)
    env = merged_env(workspace, cwd=cwd, config_dir=config_dir)
    return expand_env(merged, env), errors, layers


def _effective_path(layers: list[ConfigLayer]) -> Path:
    existing = [layer.path for layer in layers if layer.exists]
    if existing:
        return existing[-1]
    return layers[-1].path if layers else Path("")


def _validation_error(path: Path, exc: Exception) -> str:
    return f"配置非法 {path}: {exc}"


def _load_json_model(
    filename: str,
    model: type[BaseModel],
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> tuple[BaseModel, list[str]]:
    raw, errors, layers = load_config_dict(
        filename, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors:
        return model(), errors
    if not raw:
        return model(), []
    try:
        return model.model_validate(raw), []
    except ValueError as exc:
        return model(), [_validation_error(_effective_path(layers), exc)]


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


def load_webapp_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> WebappConfig:
    model, _ = _load_json_model(
        WEBAPP_FILENAME,
        WebappConfig,
        workspace,
        cwd=cwd,
        config_dir=config_dir,
    )
    return model  # type: ignore[return-value]


def load_agent_registry(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> AgentRegistry:
    model, _ = _load_json_model(
        AGENTS_FILENAME,
        AgentRegistry,
        workspace,
        cwd=cwd,
        config_dir=config_dir,
    )
    return model  # type: ignore[return-value]


def load_dangerous_actions(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> DangerousActions:
    model, _ = _load_json_model(
        DANGEROUS_FILENAME,
        DangerousActions,
        workspace,
        cwd=cwd,
        config_dir=config_dir,
    )
    return model  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _validate_model(
    filename: str,
    model: type[BaseModel],
    workspace: str | Path | None,
    *,
    cwd: str | Path | None,
    config_dir: str | Path | None,
) -> tuple[BaseModel | None, list[str]]:
    raw, errors, layers = load_config_dict(
        filename, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors:
        return None, errors
    if not raw:
        return model(), []
    try:
        return model.model_validate(raw), []
    except ValueError as exc:
        return None, [_validation_error(_effective_path(layers), exc)]


def validate_webapp_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回 webapp.json 的校验错误列表（空 = 合法）。"""
    _, errors = _validate_model(
        WEBAPP_FILENAME, WebappConfig, workspace, cwd=cwd, config_dir=config_dir
    )
    return errors


def validate_agent_registry(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回 agents.json 的校验错误列表（空 = 合法）。"""
    registry, errors = _validate_model(
        AGENTS_FILENAME, AgentRegistry, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors or registry is None:
        return errors
    typed = registry  # type: AgentRegistry
    path = _effective_path(
        config_layers(AGENTS_FILENAME, workspace, cwd=cwd, config_dir=config_dir)
    )
    ids: set[str] = set()
    for agent in typed.agents:
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

    known_ids = {agent.id for agent in typed.agents}
    if typed.default_agent and typed.default_agent not in known_ids:
        errors.append(f"{path}: default_agent '{typed.default_agent}' 未在 agents 中定义")
    for rule in typed.routing.rules:
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
    _, errors = _validate_model(
        DANGEROUS_FILENAME,
        DangerousActions,
        workspace,
        cwd=cwd,
        config_dir=config_dir,
    )
    return errors


def config_status(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, Any]:
    """返回配置文件的实际解析路径、层级、存在性与校验结果摘要。"""
    names = {
        "webapp": WEBAPP_FILENAME,
        "agents": AGENTS_FILENAME,
        "dangerous": DANGEROUS_FILENAME,
        "datasources": DATASOURCES_FILENAME,
    }
    validators = {
        "webapp": validate_webapp_config,
        "agents": validate_agent_registry,
        "dangerous": validate_dangerous_actions,
        "datasources": _validate_datasources,
    }
    project_dir = get_project_config_dir(cwd, config_dir)
    backend_dir = get_backend_config_dir(cwd, config_dir)
    result: dict[str, Any] = {
        "env": get_qaapp_env(),
        "workspace": str(get_workspace_root(workspace)),
        "project_config_dir": str(project_dir) if project_dir else "",
        "backend_config_dir": str(backend_dir) if backend_dir else "",
    }
    for name, filename in names.items():
        layers = config_layers(filename, workspace, cwd=cwd, config_dir=config_dir)
        effective = _effective_path(layers)
        layer_payload = [
            {"layer": layer.name, "path": str(layer.path), "exists": layer.exists}
            for layer in layers
        ]
        if backend_dir is not None:
            source = "+".join(
                layer.name for layer in layers if layer.exists and layer.name != "workspace"
            ) or "defaults"
        elif any(layer.name == "workspace" for layer in layers):
            source = "workspace"
        elif config_dir or os.environ.get(ENV_CONFIG_DIR):
            source = "explicit"
        else:
            source = "project"
        result[name] = {
            "path": str(effective),
            "exists": any(layer.exists for layer in layers),
            "source": source,
            "layers": layer_payload,
            "errors": validators[name](workspace, cwd=cwd, config_dir=config_dir),
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
