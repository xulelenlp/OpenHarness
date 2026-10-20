"""唯一维护文件：agents.json 内联外部 Agent 配置的注入测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.tools.base import ToolRegistry
from qaapp.config import AgentRegistry
from qaapp.external_agents import (
    build_inline_config,
    external_conversation_id,
    inline_external_entries,
    install_external_agent_tool,
    merge_configs,
)


def _registry(
    *, conversation_id: str = "auto", endpoint: str = "http://127.0.0.1:9/chat"
) -> AgentRegistry:
    return AgentRegistry.model_validate(
        {
            "default_agent": "general",
            "agents": [
                {"id": "general", "kind": "harness", "system_prompt": "hi"},
                {
                    "id": "power-grid-analysis",
                    "kind": "external",
                    "name": "电网指标数据分析",
                    "description": "售电量/线损分析报告",
                    "external": {
                        "type": "http",
                        "conversation_id": conversation_id,
                        "timeout_seconds": 42,
                        "response": {"mode": "auto", "extract_tags": ["answer"]},
                        "config": {
                            "endpoint": endpoint,
                            "method": "POST",
                            "headers": {"content-type": "application/json"},
                            "request_body": {
                                "message": "{message}",
                                "session": "{conversation_id}",
                            },
                        },
                    },
                },
                {"id": "other", "kind": "external"},
            ],
        }
    )


def _plugin_module():
    """按路径加载插件模块（qaapp 的兜底加载路径，保证 pydantic 前向引用可解析）。"""
    import importlib.util
    import sys

    from qaapp.external_agents import _PLUGIN_TOOL_PATH

    name = "test_external_agent_tool_module"
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_inline_external_entries_only_picks_inline_ones():
    registry = _registry()
    assert [a.id for a in inline_external_entries(registry)] == ["power-grid-analysis"]


def test_build_inline_config_maps_fields():
    module = _plugin_module()
    config = build_inline_config(_registry(), module)
    (agent,) = config.agents
    assert agent.id == "power-grid-analysis"
    assert agent.timeout_seconds == 42
    assert agent.response.extract_tags == ["answer"]
    assert agent.config["request_body"]["session"] == "{conversation_id}"
    assert "售电量" in agent.description


def test_merge_configs_inline_wins_but_keeps_others():
    module = _plugin_module()
    base = module.ExternalAgentsConfig.model_validate(
        {
            "agents": [
                {
                    "id": "power-grid-analysis",
                    "type": "http",
                    "config": {"endpoint": "http://old"},
                },
                {"id": "legacy", "type": "http", "config": {"endpoint": "http://legacy"}},
            ]
        }
    )
    merged = merge_configs(base, build_inline_config(_registry(), module))
    by_id = {agent.id: agent for agent in merged.agents}
    assert set(by_id) == {"power-grid-analysis", "legacy"}
    # 内联（agents.json）覆盖文件里的同名 agent，其它保留（兼容旧的 external-agents.json）
    assert by_id["power-grid-analysis"].config["endpoint"] == "http://127.0.0.1:9/chat"
    assert by_id["legacy"].config["endpoint"] == "http://legacy"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("auto", "cid123"), ("", "cid123"), ("fixed-session-001", "fixed-session-001")],
)
def test_external_conversation_id_switch(configured: str, expected: str):
    registry = _registry(conversation_id=configured)
    entry = registry.get("power-grid-analysis")
    assert entry is not None
    assert external_conversation_id(registry, entry, "cid123") == expected


def test_install_external_agent_tool_overrides_instance():
    module = _plugin_module()
    registry = _registry()
    tools = ToolRegistry()
    tools.register(module.ExternalAgentTool())

    class _Bundle:
        def __init__(self) -> None:
            self.tool_registry = tools

    bundle: Any = _Bundle()
    assert install_external_agent_tool(bundle, registry) is True

    tool = bundle.tool_registry.get("external_agent")
    assert tool is not None
    # 描述里必须出现内联 Agent，否则模型不会调用它
    assert "power-grid-analysis" in tool.description
    loaded = tool._load_config(None)
    assert "power-grid-analysis" in [agent.id for agent in loaded.agents]


def test_install_is_noop_without_inline_config():
    module = _plugin_module()
    tools = ToolRegistry()
    original = module.ExternalAgentTool()
    tools.register(original)

    class _Bundle:
        def __init__(self) -> None:
            self.tool_registry = tools

    registry = AgentRegistry.model_validate(
        {"agents": [{"id": "x", "kind": "external", "external_ref": "x"}]}
    )
    assert install_external_agent_tool(_Bundle(), registry) is False
    assert tools.get("external_agent") is original


def test_install_warns_when_plugin_missing(caplog):
    class _Bundle:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()

    with caplog.at_level("WARNING"):
        assert install_external_agent_tool(_Bundle(), _registry()) is False
    assert any("external_agent" in record.message for record in caplog.records)


def test_plugin_bundled_file_is_empty_after_migration():
    """插件目录那份配置已清空为占位，避免和 agents.json 放两份真实地址。"""
    import json

    from qaapp.external_agents import _PLUGIN_TOOL_PATH

    payload = json.loads(
        (_PLUGIN_TOOL_PATH.parent.parent / "external-agents.json").read_text(encoding="utf-8")
    )
    assert payload["agents"] == []
    assert "已迁移" in payload["_comment"]
    assert Path(_PLUGIN_TOOL_PATH).is_file()


def _install_and_capture(monkeypatch, registry, session_id: str, model_conversation_id=None):
    """装上工具并用假 transport 捕获实际传出的 conversation_id。"""
    import asyncio

    from openharness.tools.base import ToolExecutionContext

    module = _plugin_module()
    tools = ToolRegistry()
    tools.register(module.ExternalAgentTool())

    class _Bundle:
        def __init__(self) -> None:
            self.tool_registry = tools

    assert install_external_agent_tool(_Bundle(), registry, session_id=session_id) is True
    tool = tools.get("external_agent")
    assert tool is not None

    captured: dict[str, str] = {}

    class FakeTransport:
        transport_type = "http"

        async def invoke(self, agent, config, message, conversation_id, cwd, stream_text=None):
            captured["conversation_id"] = conversation_id
            captured["agent"] = agent.id
            return "<answer>ok</answer>"

    monkeypatch.setitem(module.TRANSPORT_REGISTRY, "http", FakeTransport())
    arguments = module.ExternalAgentInput(
        agent="power-grid-analysis",
        message="分析售电量",
        conversation_id=model_conversation_id,
    )
    result = asyncio.run(tool.execute(arguments, ToolExecutionContext(cwd=Path.cwd())))
    assert not result.is_error, result.output
    return captured


def test_tool_call_path_uses_qaapp_session_id(monkeypatch):
    """harness 把 external_agent 当工具调用时，也必须用本 qaapp 会话 ID。

    否则插件会退化成「每次调用一个随机 uuid」，对端每次都要新建会话——平台会话存储
    一不稳就是「会话创建失败」。
    """
    captured = _install_and_capture(monkeypatch, _registry(conversation_id="auto"), "cid-abc")
    assert captured["conversation_id"] == "cid-abc"


def test_tool_call_path_ignores_model_supplied_random_id(monkeypatch):
    captured = _install_and_capture(
        monkeypatch, _registry(conversation_id="auto"), "cid-abc", model_conversation_id="deadbeef"
    )
    assert captured["conversation_id"] == "cid-abc"


def test_tool_call_path_pins_configured_conversation_id(monkeypatch):
    captured = _install_and_capture(
        monkeypatch, _registry(conversation_id="fixed-session-001"), "cid-abc"
    )
    assert captured["conversation_id"] == "fixed-session-001"


def test_startup_warns_when_external_ref_cannot_be_resolved(tmp_path, monkeypatch, caplog):
    """服务器上「旧 agents.json（只有 external_ref）+ 插件配置已清空」要能一眼看出原因。"""
    import json
    import logging

    from ohmo.workspace import initialize_workspace
    from qaapp.service import QaWebService

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(repo_root / ".openharness"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))
    cwd = tmp_path / "repo"
    (cwd / "config").mkdir(parents=True)
    (cwd / "config" / "agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "general", "kind": "harness"},
                    {"id": "power-grid-analysis", "kind": "external",
                     "external_ref": "power-grid-analysis"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)

    with caplog.at_level(logging.ERROR, logger="qaapp.service"):
        QaWebService(workspace=workspace, cwd=str(cwd))

    assert any(
        "找不到访问配置" in record.message and "power-grid-analysis" in record.message
        for record in caplog.records
    ), [r.message for r in caplog.records]


def test_service_loads_agents_from_project_config_dir(tmp_path, monkeypatch, caplog):
    """git pull 只更新仓库 → 从 <cwd>/config/agents.json 读到内联外部 Agent。"""
    import json
    import logging

    from ohmo.workspace import initialize_workspace
    from qaapp.service import QaWebService

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(repo_root / ".openharness"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))
    cwd = tmp_path / "repo"
    (cwd / "config").mkdir(parents=True)
    registry_payload = {
        "default_agent": "general",
        "agents": [
            {"id": "general", "kind": "harness"},
            {
                "id": "power-grid-analysis",
                "kind": "external",
                "description": "售电量分析",
                "external": {
                    "type": "http",
                    "config": {
                        "endpoint": "http://127.0.0.1:9/chat",
                        "request_body": {"message": "{message}"},
                    },
                },
            },
        ],
    }
    (cwd / "config" / "agents.json").write_text(
        json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8"
    )
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)  # workspace 里没有 agents.json

    with caplog.at_level(logging.INFO, logger="qaapp.service"):
        service = QaWebService(workspace=workspace, cwd=str(cwd))

    assert [a.id for a in service.registry.agents] == ["general", "power-grid-analysis"]
    # 启动日志里应能看到实际生效的文件，便于排查「改了没生效」
    assert any(
        str(cwd / "config" / "agents.json") in record.getMessage() for record in caplog.records
    )
