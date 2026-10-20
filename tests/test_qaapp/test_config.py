"""qaapp 配置层测试。"""

import json
from pathlib import Path

from qaapp.config import (
    AgentRegistry,
    DangerousActions,
    config_status,
    get_agents_config_path,
    get_dangerous_config_path,
    get_webapp_config_path,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
    validate_agent_registry,
    validate_dangerous_actions,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_defaults_when_files_missing(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    assert load_webapp_config(ws).port == 8787
    assert load_agent_registry(ws).agents == []
    assert load_dangerous_actions(ws).rules == []


def test_webapp_config_load(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    _write(get_webapp_config_path(ws), {"host": "0.0.0.0", "port": 9999, "api_tokens": ["t"]})
    cfg = load_webapp_config(ws)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9999
    assert cfg.api_tokens == ["t"]
    # 默认允许浏览器免手工 token：服务端用 api_tokens 签发 HttpOnly 会话 Cookie。
    assert cfg.browser_auth.enabled is True
    assert cfg.browser_auth.cookie_name == "qaapp_session"


def test_empty_api_tokens_are_ignored(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    _write(get_webapp_config_path(ws), {"api_tokens": ["", "  ", " real "]})
    assert load_webapp_config(ws).api_tokens == ["real"]


def test_browser_auth_config(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    _write(
        get_webapp_config_path(ws),
        {"browser_auth": {"enabled": False, "ttl_seconds": 60, "cookie_name": "s"}},
    )
    cfg = load_webapp_config(ws)
    assert cfg.browser_auth.enabled is False
    assert cfg.browser_auth.ttl_seconds == 60
    assert cfg.browser_auth.cookie_name == "s"


def test_agent_registry_validation(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    path = get_agents_config_path(ws)
    _write(
        path,
        {
            "default_agent": "general",
            "routing": {
                "mode": "rule_then_llm",
                "rules": [{"agent": "power-grid-analysis", "keywords": ["售电量"], "priority": 100}],
            },
            "agents": [
                {"id": "power-grid-analysis", "kind": "external", "external_ref": "power-grid-analysis"},
                {"id": "general", "kind": "harness", "system_prompt": "helpful"},
            ],
        },
    )
    assert validate_agent_registry(ws) == []

    # 路由指向未知 agent
    _write(path, {"agents": [], "routing": {"rules": [{"agent": "nope", "keywords": ["x"]}]}})
    errors = validate_agent_registry(ws)
    assert any("nope" in e for e in errors)


def test_dangerous_validation(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    path = get_dangerous_config_path(ws)
    _write(path, {"rules": [{"tool": "file_write", "action": "confirm"}]})
    assert validate_dangerous_actions(ws) == []

    _write(path, {"rules": [{"tool": "file_write", "action": "bogus"}]})
    assert validate_dangerous_actions(ws) != []


def test_dangerous_model_defaults():
    cfg = DangerousActions.model_validate({})
    assert cfg.approval_timeout_seconds == 300
    assert cfg.rules == []


def test_agent_registry_enabled_filter():
    reg = AgentRegistry.model_validate(
        {
            "agents": [
                {"id": "a", "kind": "harness", "enabled": True},
                {"id": "b", "kind": "harness", "enabled": False},
            ]
        }
    )
    assert [a.id for a in reg.enabled_agents()] == ["a"]


def _inline_external(endpoint: str = "http://127.0.0.1:9/chat", **extra) -> dict:
    spec = {
        "type": "http",
        "conversation_id": "auto",
        "config": {
            "endpoint": endpoint,
            "method": "POST",
            "request_body": {"message": "{message}", "session": "{conversation_id}"},
        },
        "response": {"mode": "auto"},
    }
    spec.update(extra)
    return spec


def test_external_agent_inline_config_is_valid(tmp_path: Path):
    """kind=external 允许内联访问配置（唯一维护文件 agents.json 的用法）。"""
    ws = tmp_path / ".ohmo"
    path = get_agents_config_path(ws)
    _write(
        path,
        {
            "default_agent": "general",
            "agents": [
                {"id": "general", "kind": "harness"},
                {
                    "id": "power-grid-analysis",
                    "kind": "external",
                    "name": "电网指标数据分析",
                    "description": "售电量/线损分析",
                    "external": _inline_external(),
                },
            ],
        },
    )
    assert validate_agent_registry(ws) == []
    registry = load_agent_registry(ws)
    entry = registry.get("power-grid-analysis")
    assert entry is not None and entry.external is not None
    assert entry.external.config["endpoint"].endswith("/chat")
    # 内联时调用 id 用 agent.id（不再依赖 external_ref）
    assert entry.external_agent_id == "power-grid-analysis"


def test_external_agent_without_any_config_is_invalid(tmp_path: Path):
    ws = tmp_path / ".ohmo"
    path = get_agents_config_path(ws)
    _write(path, {"agents": [{"id": "x", "kind": "external"}]})
    errors = validate_agent_registry(ws)
    assert any("external" in e for e in errors)


def test_external_inline_config_requires_endpoint():
    import pytest
    from pydantic import ValidationError

    from qaapp.config import ExternalAgentSpec

    with pytest.raises(ValidationError):
        ExternalAgentSpec.model_validate({"type": "http", "config": {}})
    with pytest.raises(ValidationError):
        ExternalAgentSpec.model_validate({"type": "stdio", "config": {}})

    assert ExternalAgentSpec.model_validate(
        {"type": "stdio", "config": {"command": "my-agent --json"}}
    ).type == "stdio"


# ---------------------------------------------------------------------------
# 配置解析顺序：--config-dir → <cwd>/config/ → workspace
# ---------------------------------------------------------------------------


def test_project_config_dir_wins_over_workspace(tmp_path: Path):
    """仓库内可见的 config/agents.json 优先于隐藏的 workspace 配置。"""
    cwd = tmp_path / "repo"
    (cwd / "config").mkdir(parents=True)
    ws = tmp_path / ".ohmo"
    _write(cwd / "config" / "agents.json", {"agents": [{"id": "from-project", "kind": "harness"}]})
    _write(ws / "agents.json", {"agents": [{"id": "from-workspace", "kind": "harness"}]})

    assert get_agents_config_path(ws, cwd=cwd) == cwd / "config" / "agents.json"
    assert [a.id for a in load_agent_registry(ws, cwd=cwd).agents] == ["from-project"]
    # 不传 cwd 时保持旧行为（只认 workspace），避免隐式依赖进程当前目录
    assert [a.id for a in load_agent_registry(ws).agents] == ["from-workspace"]


def test_workspace_is_fallback_when_project_config_missing(tmp_path: Path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    ws = tmp_path / ".ohmo"
    _write(ws / "agents.json", {"agents": [{"id": "from-workspace", "kind": "harness"}]})
    assert get_agents_config_path(ws, cwd=cwd) == ws / "agents.json"
    assert [a.id for a in load_agent_registry(ws, cwd=cwd).agents] == ["from-workspace"]

    # config/ 目录存在但没有 agents.json → 仍回退到 workspace
    (cwd / "config").mkdir()
    assert get_agents_config_path(ws, cwd=cwd) == ws / "agents.json"


def test_explicit_config_dir_takes_priority(tmp_path: Path):
    cwd = tmp_path / "repo"
    (cwd / "config").mkdir(parents=True)
    _write(cwd / "config" / "agents.json", {"agents": [{"id": "from-project", "kind": "harness"}]})
    explicit = tmp_path / "shared-config"
    _write(explicit / "agents.json", {"agents": [{"id": "from-explicit", "kind": "harness"}]})

    assert get_agents_config_path(None, cwd=cwd, config_dir=explicit) == explicit / "agents.json"
    registry = load_agent_registry(None, cwd=cwd, config_dir=explicit)
    assert [a.id for a in registry.agents] == ["from-explicit"]


def test_env_config_dir_is_honored(tmp_path: Path, monkeypatch):
    target = tmp_path / "env-config"
    _write(target / "agents.json", {"agents": [{"id": "from-env", "kind": "harness"}]})
    monkeypatch.setenv("QAAPP_CONFIG_DIR", str(target))
    registry = load_agent_registry(None, cwd=tmp_path)
    assert [a.id for a in registry.agents] == ["from-env"]


def test_config_status_reports_source(tmp_path: Path):
    cwd = tmp_path / "repo"
    (cwd / "config").mkdir(parents=True)
    _write(cwd / "config" / "agents.json", {"agents": [{"id": "a", "kind": "harness"}]})
    status = config_status(tmp_path / ".ohmo", cwd=cwd)
    assert status["agents"]["source"] == "project"
    assert status["webapp"]["source"] == "workspace"
    assert status["project_config_dir"] == str(cwd / "config")
