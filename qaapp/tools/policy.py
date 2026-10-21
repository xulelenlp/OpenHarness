"""按 Agent 生效的工具白名单。

``agents.json`` 里的 ``tools`` / ``disallowed_tools`` 是**能力边界**，必须在运行时
真正生效，否则配置只是装饰。qaapp 在每轮 harness 执行前用本模块从会话的完整
工具表中裁出一个只读视图，再交给 ``QueryEngine``。

语义（与 Claude Code / OpenHarness 的 AgentDefinition 对齐）：

- ``tools`` 为 ``None`` 或包含 ``"*"`` → 允许全部工具；
- ``tools`` 为空列表 ``[]`` → 不允许任何工具；
- 支持 fnmatch 通配（例如 ``"mcp_*"``、``"web_*"``）；
- ``disallowed_tools`` 优先级高于 ``tools``，同样支持通配。
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from openharness.tools.base import ToolRegistry
from qaapp.config import AgentEntry


def tool_allowed(
    name: str,
    allow: list[str] | None,
    deny: list[str] | None = None,
) -> bool:
    """判断一个工具名是否在 Agent 的能力边界内。"""
    if deny and any(fnmatchcase(name, pattern) for pattern in deny):
        return False
    if allow is None:
        return True
    if any(pattern == "*" for pattern in allow):
        return True
    return any(fnmatchcase(name, pattern) for pattern in allow)


def build_agent_tool_registry(base: ToolRegistry, agent: AgentEntry) -> ToolRegistry:
    """从 ``base`` 裁出该 Agent 可用的工具，返回一个新的只读视图。"""
    registry = ToolRegistry()
    for tool in base.list_tools():
        if tool_allowed(tool.name, agent.tools, agent.disallowed_tools):
            registry.register(tool)
    return registry


__all__ = ["build_agent_tool_registry", "tool_allowed"]
