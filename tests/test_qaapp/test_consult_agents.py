"""多智能体批量调用测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from qaapp.tools.consult import AgentCall, ConsultAgentsInput, ConsultAgentsTool
from qaapp.tools.script import CONVERSATION_DIR_KEY


class FakeExternalInput(BaseModel):
    agent: str
    message: str
    conversation_id: str | None = None


class FakeExternal(BaseTool):
    name = "external_agent"
    description = "fake"
    input_model = FakeExternalInput

    def __init__(self) -> None:
        self.calls: list[FakeExternalInput] = []
        self.fail_first: set[str] = set()
        self.fail_always: set[str] = set()

    def is_read_only(self, arguments: FakeExternalInput) -> bool:
        return True

    async def execute(self, arguments: FakeExternalInput, context: ToolExecutionContext) -> ToolResult:
        self.calls.append(arguments)
        if arguments.agent in self.fail_always:
            return ToolResult(output="external_agent failed: HTTP 502 Bad Gateway", is_error=True)
        if arguments.agent in self.fail_first:
            self.fail_first.remove(arguments.agent)
            return ToolResult(output="external_agent failed: HTTP 502 Bad Gateway", is_error=True)
        return ToolResult(output=f"回答来自 {arguments.agent}：{arguments.message[:80]}")


def _context(tmp_path: Path, tool: FakeExternal) -> ToolExecutionContext:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutionContext(
        cwd=tmp_path,
        metadata={"tool_registry": registry, CONVERSATION_DIR_KEY: str(tmp_path)},
    )


@pytest.mark.asyncio
async def test_parallel_calls_and_evidence(tmp_path: Path):
    external = FakeExternal()
    tool = ConsultAgentsTool(session_id="session-1")
    result = await tool.execute(
        ConsultAgentsInput(
            calls=[
                AgentCall(agent="smart-data-query", message="查售电量", purpose="取数"),
                AgentCall(agent="power-grid-analysis", message="分析原因", purpose="归因"),
                AgentCall(agent="knowledge-qa", message="解读政策", purpose="政策"),
            ],
            shared_context="2026-09 福建售电量 64.9459",
        ),
        _context(tmp_path, external),
    )
    assert result.is_error is False
    assert "回答来自 smart-data-query" in result.output
    assert "回答来自 power-grid-analysis" in result.output
    assert "回答来自 knowledge-qa" in result.output
    assert len(external.calls) == 3
    assert all("2026-09 福建售电量" in call.message for call in external.calls)
    files = list((tmp_path / "outputs" / "agents").rglob("*.md"))
    assert len(files) == 3


@pytest.mark.asyncio
async def test_partial_failure_does_not_block(tmp_path: Path):
    external = FakeExternal()
    external.fail_always = {"power-grid-analysis"}
    tool = ConsultAgentsTool(session_id="session-1")
    result = await tool.execute(
        ConsultAgentsInput(
            calls=[
                AgentCall(agent="smart-data-query", message="查售电量"),
                AgentCall(agent="power-grid-analysis", message="分析原因"),
            ],
        ),
        _context(tmp_path, external),
    )
    assert "❌" in result.output
    assert "✅" in result.output
    assert "HTTP 502" in result.output


@pytest.mark.asyncio
async def test_transient_error_retries_once(tmp_path: Path):
    external = FakeExternal()
    external.fail_first = {"smart-data-query"}
    tool = ConsultAgentsTool(session_id="session-1")
    result = await tool.execute(
        ConsultAgentsInput(calls=[AgentCall(agent="smart-data-query", message="查售电量")], timeout_seconds=10),
        _context(tmp_path, external),
    )
    assert "✅" in result.output
    assert len(external.calls) == 2


@pytest.mark.asyncio
async def test_duplicate_agent_runs_sequentially(tmp_path: Path):
    external = FakeExternal()
    tool = ConsultAgentsTool(session_id="session-1")
    result = await tool.execute(
        ConsultAgentsInput(
            calls=[
                AgentCall(agent="smart-data-query", message="第一次"),
                AgentCall(agent="smart-data-query", message="第二次"),
            ],
            mode="parallel",
        ),
        _context(tmp_path, external),
    )
    assert len(external.calls) == 2
    assert "第一次" in result.output and "第二次" in result.output
