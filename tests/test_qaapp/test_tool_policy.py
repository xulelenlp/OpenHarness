"""Agent 工具白名单（tools / disallowed_tools）测试。"""

from __future__ import annotations

from pydantic import BaseModel

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from qaapp.config import AgentEntry
from qaapp.tools.policy import build_agent_tool_registry, tool_allowed


class _EmptyInput(BaseModel):
    pass


class _FakeTool(BaseTool):
    description = "fake"
    input_model = _EmptyInput

    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        return ToolResult(output=self.name)


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_FakeTool(name))
    return registry


def test_tool_allowed_semantics():
    assert tool_allowed("bash", None) is True
    assert tool_allowed("bash", ["*"]) is True
    assert tool_allowed("bash", []) is False
    assert tool_allowed("web_search", ["web_*"]) is True
    assert tool_allowed("web_search", ["bash"]) is False
    # disallowed 优先于 allow，且支持通配。
    assert tool_allowed("web_search", ["*"], ["web_*"]) is False
    assert tool_allowed("bash", ["bash"], ["edit_file"]) is True


def test_build_agent_tool_registry_whitelist():
    agent = AgentEntry(id="a", tools=["read_file", "web_*"], disallowed_tools=["web_secret"])
    registry = build_agent_tool_registry(
        _registry("read_file", "web_search", "web_secret", "bash"), agent
    )
    assert [tool.name for tool in registry.list_tools()] == ["read_file", "web_search"]


def test_build_agent_tool_registry_defaults_to_all():
    agent = AgentEntry(id="a")
    registry = build_agent_tool_registry(_registry("read_file", "bash"), agent)
    assert {tool.name for tool in registry.list_tools()} == {"read_file", "bash"}


def test_build_agent_tool_registry_empty_means_none():
    agent = AgentEntry(id="a", tools=[])
    registry = build_agent_tool_registry(_registry("read_file", "bash"), agent)
    assert registry.list_tools() == []


def test_original_registry_is_not_mutated():
    base = _registry("read_file", "bash")
    build_agent_tool_registry(base, AgentEntry(id="a", tools=["read_file"]))
    assert {tool.name for tool in base.list_tools()} == {"read_file", "bash"}
