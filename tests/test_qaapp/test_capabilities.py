"""动态能力清单测试：新增 Agent 不需要改通用助手的提示词。"""

from __future__ import annotations

from qaapp.agents.capabilities import build_capability_catalog
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
                    "best_for": ["趋势分析", "同比环比"],
                    "not_for": ["单值取数"],
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


def test_catalog_includes_option_clarification_when_tool_available():
    with_tool = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={"ask_user_question", "run_script"},
    )
    assert "选项式澄清" in with_tool
    assert "2–4 个互斥" in with_tool
    assert "其他" in with_tool

    without_tool = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={"run_script"},
    )
    assert "选项式澄清" not in without_tool
    assert "选项式澄清（ask_user_question）" not in without_tool


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


def test_catalog_preserves_general_and_adds_analysis_as_optional():
    catalog = build_capability_catalog(
        _registry(),
        current_agent_id="general",
        available_tools={
            "external_agent",
            "consult_agents",
            "db_query",
            "run_script",
            "write_report_docx",
            "skill",
        },
        now="2026-10-21",
        data_boundary="售电量 最新 2026-09-30",
    )
    # 通用能力仍然是第一原则，不是强制业务分析。
    assert "能直接回答就直接回答" in catalog
    assert "通用任务仍按你原有" in catalog
    assert "深度业务分析/报告才加载" in catalog
    # 多智能体组合而不是固定一个。
    assert "组合编排" in catalog
    assert "consult_agents" in catalog
    assert "db_query" in catalog
    assert "write_report_docx" in catalog
    assert "当前日期：2026-10-21" in catalog
    assert "售电量 最新 2026-09-30" in catalog
    assert "不要只选一个" in catalog


def test_actual_general_config_keeps_tools_and_analysis_optional():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    data = json.loads((root / "config" / "backend" / "base" / "agents.json").read_text(encoding="utf-8"))
    general = next(agent for agent in data["agents"] if agent["id"] == "general")
    assert general["tools"] == ["*"]
    assert "能直接回答就直接回答" in general["system_prompt"]
    assert "业务分析（按需" in general["system_prompt"]
    assert "不要强行套流程" in general["system_prompt"]
    assert general["max_turns"] >= 60


def test_catalog_lists_agent_metadata():
    catalog = build_capability_catalog(
        _registry(), current_agent_id="general", available_tools={"external_agent"}
    )
    assert "擅长" in catalog
