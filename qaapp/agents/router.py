"""多 Agent 路由。

根据用户问题，从 ``agents.json`` 注册表中选出一个 Agent 并返回派发信息。

解析顺序（可用 ``routing.mode`` 裁剪）：
1. ``explicit``  —— 调用方显式指定 agent（API 参数 / 前端选择器）
2. ``rule``      —— 关键词规则匹配（``routing.rules``，按 priority 降序）
3. ``llm``       —— LLM 意图分类（可选，由调用方注入 classifier）
4. ``default``   —— 默认 Agent

> 注意：``qaapp/server/service.py`` **目前没有注入** ``llm_classifier``，因此
> ``routing.mode=llm`` 实际会直接落到 ``default_agent``。生产推荐「默认 harness +
> 动态能力清单」的自主编排，而不是每轮先做一次分类调用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from qaapp.config import AgentEntry, AgentRegistry

ROUTE_EXPLICIT = "explicit"
ROUTE_RULE = "rule"
ROUTE_LLM = "llm"
ROUTE_DEFAULT = "default"


class AgentNotFoundError(Exception):
    """显式指定的 agent 不存在或未启用。"""

    def __init__(self, agent_id: str, available: list[str]) -> None:
        self.agent_id = agent_id
        self.available = available
        super().__init__(f"Unknown or disabled agent '{agent_id}'. Available: {', '.join(available) or '(none)'}")


class NoDefaultAgentError(Exception):
    """无法路由且未配置默认 Agent。"""


@dataclass(frozen=True)
class ResolvedAgent:
    """路由结果。"""

    agent: AgentEntry
    route: str  # explicit | rule | llm | default


LlmClassifier = Callable[[str], Awaitable[str | None]]


def keyword_match_agent(message: str, registry: AgentRegistry) -> AgentEntry | None:
    """按关键词规则匹配（priority 降序，首个命中即返回）。"""
    normalized = message.lower()
    rules = sorted(
        registry.routing.rules,
        key=lambda rule: -rule.priority,
    )
    for rule in rules:
        if not rule.keywords:
            continue
        if any(keyword.lower() in normalized for keyword in rule.keywords):
            agent = registry.get(rule.agent)
            if agent is not None and agent.enabled:
                return agent
    return None


async def resolve_agent(
    message: str,
    *,
    registry: AgentRegistry,
    explicit_agent: str | None = None,
    llm_classifier: LlmClassifier | None = None,
) -> ResolvedAgent:
    """解析用户消息应路由到哪个 Agent。"""
    # 1. 显式指定
    if explicit_agent:
        agent = registry.get(explicit_agent)
        if agent is None or not agent.enabled:
            raise AgentNotFoundError(
                explicit_agent, [a.id for a in registry.enabled_agents()]
            )
        return ResolvedAgent(agent=agent, route=ROUTE_EXPLICIT)

    mode = registry.routing.mode

    # 2. 关键词规则
    if mode in {"rule", "rule_then_llm", "explicit"}:
        agent = keyword_match_agent(message, registry)
        if agent is not None:
            return ResolvedAgent(agent=agent, route=ROUTE_RULE)

    # 3. LLM 意图分类（可选）
    if mode in {"llm", "rule_then_llm"} and llm_classifier is not None:
        agent_id = await llm_classifier(message)
        if agent_id:
            agent = registry.get(agent_id)
            if agent is not None and agent.enabled:
                return ResolvedAgent(agent=agent, route=ROUTE_LLM)

    # 4. 默认
    if registry.default_agent:
        agent = registry.get(registry.default_agent)
        if agent is not None and agent.enabled:
            return ResolvedAgent(agent=agent, route=ROUTE_DEFAULT)

    raise NoDefaultAgentError("No default agent configured and no rule matched.")


__all__ = [
    "AgentNotFoundError",
    "LlmClassifier",
    "NoDefaultAgentError",
    "ResolvedAgent",
    "keyword_match_agent",
    "resolve_agent",
]
