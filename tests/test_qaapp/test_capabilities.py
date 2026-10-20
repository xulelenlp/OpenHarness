"""动态能力清单测试：新增 Agent 不需要改通用助手的提示词。"""

from __future__ import annotations

from qaapp.capabilities import build_capability_catalog
from qaapp.config import AgentRegistry


def _registry() -> AgentRegistry:
    return AgentRegistry.model_validate(
        {
            "default_agent": "general",
            "agents": [
                {"id": "general", "kind": "harness", "name": "通用助手"},
                {
                    "id": "power-grid-analysis",
                    "kind": "external",
                    "name": "电网指标数据分析",
                    "description": "售电量/线损分析\n（趋势、同比、影响因素）",
                    "external": {
                        "type": "http",
                        "config": {"endpoint": "http://example.invalid/chat"},
                    },
                },
                {
                    "id": "disabled-agent",
                    "kind": "external",
                    "enabled": False,
                    "name": "停用智能体",
                    "description": "不应该出现",
                    "external": {
                        "type": "http",
                        "config": {"endpoint": "http://example.invalid/disabled"},
                    },
                },
                {"id": "sub", "kind": "subagent", "subagent_type": "Explore"},
            ],
        }
    )


def test_catalog_is_generated_from_registry():
    catalog = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={"external_agent", "run_script"},
    )
    assert "power-grid-analysis" in catalog
    assert "售电量/线损分析" in catalog
    # description 里的换行被压平，避免撑坏提示词结构。
    assert "趋势、同比、影响因素" in catalog
    assert "disabled-agent" not in catalog
    assert "不应该出现" not in catalog
    # 当前 harness 自身不作为可调用智能体列出。
    assert "`general`" not in catalog
    # 本机执行能力与决策顺序。
    assert "run_script" in catalog
    assert "决策顺序" in catalog


def test_catalog_respects_tool_boundary():
    only_external = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={"external_agent"},
    )
    assert "power-grid-analysis" in only_external
    assert "本机执行能力" not in only_external

    only_script = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={"run_script"},
    )
    assert "power-grid-analysis" not in only_script
    assert "本机执行能力" in only_script


def test_catalog_empty_when_no_capabilities_available():
    assert (
        build_capability_catalog(
            _registry(), current_agent_id="general", available_tools=set()
        )
        == ""
    )


def test_catalog_none_tools_means_unfiltered():
    catalog = build_capability_catalog(_registry(), current_agent_id="general")
    assert "power-grid-analysis" in catalog
    assert "run_script" in catalog
