"""多 Agent 路由测试。"""

import pytest

from qaapp.agents.router import (
    AgentNotFoundError,
    NoDefaultAgentError,
    resolve_agent,
)
from qaapp.config import AgentRegistry


def _registry() -> AgentRegistry:
    return AgentRegistry.model_validate(
        {
            "default_agent": "general",
            "routing": {
                "mode": "rule_then_llm",
                "rules": [
                    {"agent": "power-grid-analysis", "keywords": ["售电量", "线损"], "priority": 100},
                    {"agent": "smart-data-query", "keywords": ["明细"], "priority": 50},
                ],
            },
            "agents": [
                {"id": "power-grid-analysis", "kind": "external", "name": "电网", "external_ref": "pga"},
                {"id": "smart-data-query", "kind": "external", "name": "问数", "external_ref": "sdq"},
                {"id": "general", "kind": "harness", "system_prompt": "helpful"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_rule_routing_by_priority():
    result = await resolve_agent("分析福建售电量情况", registry=_registry())
    assert result.agent.id == "power-grid-analysis"
    assert result.route == "rule"


@pytest.mark.asyncio
async def test_explicit_agent_overrides():
    result = await resolve_agent("分析售电量", registry=_registry(), explicit_agent="smart-data-query")
    assert result.agent.id == "smart-data-query"
    assert result.route == "explicit"


@pytest.mark.asyncio
async def test_default_agent():
    result = await resolve_agent("你好", registry=_registry())
    assert result.agent.id == "general"
    assert result.route == "default"


@pytest.mark.asyncio
async def test_agent_not_found():
    with pytest.raises(AgentNotFoundError):
        await resolve_agent("x", registry=_registry(), explicit_agent="nope")


@pytest.mark.asyncio
async def test_llm_classifier_used():
    registry = _registry()
    registry.routing.mode = "llm"

    async def classifier(message: str) -> str | None:
        return "smart-data-query"

    result = await resolve_agent("查一个数值", registry=registry, llm_classifier=classifier)
    assert result.agent.id == "smart-data-query"
    assert result.route == "llm"


@pytest.mark.asyncio
async def test_no_default_raises():
    registry = AgentRegistry.model_validate(
        {"default_agent": "", "agents": [{"id": "a", "kind": "harness"}]}
    )
    with pytest.raises(NoDefaultAgentError):
        await resolve_agent("hello", registry=registry)
