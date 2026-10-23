"""qaapp 配置加载、校验与状态汇总（单文件，不做分层合并）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ohmo.workspace import get_workspace_root
from openharness.config.layers import ConfigLayerError, expand_env, load_json_file
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
    get_backend_config_dir,
    get_project_config_dir,
    resolve_config_path,
)

# ---------------------------------------------------------------------------
# 单文件读取
# ---------------------------------------------------------------------------


def config_path(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> Path:
    return resolve_config_path(filename, workspace, cwd=cwd, config_dir=config_dir)


def load_config_dict(
    filename: str,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[str], Path]:
    """Read one JSON file and expand ``${VAR}`` placeholders."""
    path = config_path(filename, workspace, cwd=cwd, config_dir=config_dir)
    errors: list[str] = []
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = load_json_file(path)
        except ConfigLayerError as exc:
            errors.append(str(exc))
    env = merged_env(workspace, cwd=cwd, config_dir=config_dir)
    return expand_env(raw, env), errors, path


def _load_json_model(
    filename: str,
    model: type[BaseModel],
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> tuple[BaseModel, list[str]]:
    raw, errors, path = load_config_dict(
        filename, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors:
        return model(), errors
    if not raw:
        return model(), []
    try:
        return model.model_validate(raw), []
    except ValueError as exc:
        return model(), [f"配置非法 {path}: {exc}"]


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
        WEBAPP_FILENAME, WebappConfig, workspace, cwd=cwd, config_dir=config_dir
    )
    config = model  # type: WebappConfig
    env = merged_env(workspace, cwd=cwd, config_dir=config_dir)
    updates: dict[str, Any] = {}
    if env.get("QAAPP_HOST"):
        updates["host"] = env["QAAPP_HOST"]
    if env.get("QAAPP_PORT"):
        try:
            updates["port"] = int(env["QAAPP_PORT"])
        except ValueError:
            pass
    if env.get("QAAPP_PROVIDER_PROFILE"):
        updates["provider_profile"] = env["QAAPP_PROVIDER_PROFILE"]
    if env.get("QAAPP_STATIC_DIR"):
        updates["static_dir"] = env["QAAPP_STATIC_DIR"]
    if env.get("QAAPP_CORS_ORIGINS"):
        updates["cors_origins"] = [
            item.strip() for item in env["QAAPP_CORS_ORIGINS"].split(",") if item.strip()
        ]
    if env.get("QAAPP_API_TOKENS") is not None:
        updates["api_tokens"] = [
            item.strip() for item in env.get("QAAPP_API_TOKENS", "").split(",") if item.strip()
        ]
    if env.get("QAAPP_APPROVAL_TIMEOUT_SECONDS"):
        try:
            updates["approval_timeout_seconds"] = int(
                env["QAAPP_APPROVAL_TIMEOUT_SECONDS"]
            )
        except ValueError:
            pass
    if env.get("QAAPP_BROWSER_AUTH_ENABLED") is not None:
        browser = config.browser_auth.model_copy(
            update={
                "enabled": env["QAAPP_BROWSER_AUTH_ENABLED"].strip().lower()
                in {"1", "true", "yes", "on"}
            }
        )
        updates["browser_auth"] = browser

    auth_updates: dict[str, Any] = {}
    if env.get("QAAPP_AUTH_ENABLED") is not None:
        auth_updates["enabled"] = env["QAAPP_AUTH_ENABLED"].strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if env.get("QAAPP_USERS_FILE"):
        auth_updates["users_file"] = env["QAAPP_USERS_FILE"].strip()
    if env.get("QAAPP_SESSION_TTL_SECONDS"):
        try:
            auth_updates["session_ttl_seconds"] = int(env["QAAPP_SESSION_TTL_SECONDS"])
        except ValueError:
            pass
    if env.get("QAAPP_AUTH_TRUST_PROXY") is not None:
        auth_updates["trust_proxy_headers"] = env["QAAPP_AUTH_TRUST_PROXY"].strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if auth_updates:
        updates["auth"] = config.auth.model_copy(update=auth_updates)
    return config.model_copy(update=updates) if updates else config


def load_agent_registry(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> AgentRegistry:
    model, _ = _load_json_model(
        AGENTS_FILENAME, AgentRegistry, workspace, cwd=cwd, config_dir=config_dir
    )
    return model  # type: ignore[return-value]


def load_dangerous_actions(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> DangerousActions:
    model, _ = _load_json_model(
        DANGEROUS_FILENAME, DangerousActions, workspace, cwd=cwd, config_dir=config_dir
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
    raw, errors, path = load_config_dict(
        filename, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors:
        return None, errors
    if not raw:
        return model(), []
    try:
        return model.model_validate(raw), []
    except ValueError as exc:
        return None, [f"配置非法 {path}: {exc}"]


def validate_webapp_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
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
    registry, errors = _validate_model(
        AGENTS_FILENAME, AgentRegistry, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors or registry is None:
        return errors
    typed = registry  # type: AgentRegistry
    path = config_path(AGENTS_FILENAME, workspace, cwd=cwd, config_dir=config_dir)
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
    _, errors = _validate_model(
        DANGEROUS_FILENAME, DangerousActions, workspace, cwd=cwd, config_dir=config_dir
    )
    return errors


def _source(
    path: Path,
    workspace: str | Path | None,
    *,
    cwd: str | Path | None,
    config_dir: str | Path | None,
) -> str:
    backend = get_backend_config_dir(cwd, config_dir)
    if backend is not None and path.parent == backend:
        return "project"
    project = get_project_config_dir(cwd, config_dir)
    if project is not None and path.parent == project:
        return "project"
    if config_dir or os.environ.get(ENV_CONFIG_DIR):
        return "explicit"
    return "workspace"


def config_status(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, Any]:
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
        "workspace": str(get_workspace_root(workspace)),
        "project_config_dir": str(project_dir) if project_dir else "",
        "backend_config_dir": str(backend_dir) if backend_dir else "",
    }
    for name, filename in names.items():
        path = config_path(filename, workspace, cwd=cwd, config_dir=config_dir)
        result[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "source": _source(
                path, workspace, cwd=cwd, config_dir=config_dir
            ),
            "errors": validators[name](workspace, cwd=cwd, config_dir=config_dir),
        }
    return result


def _validate_datasources(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    from qaapp.data.datasources import validate_datasources_config

    return validate_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
