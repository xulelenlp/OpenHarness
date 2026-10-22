"""Agent execution: harness turn and external-agent dispatch."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import (
    AssistantReasoningDelta,
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionProgress,
    ToolExecutionStarted,
)
from openharness.permissions.modes import PermissionMode
from openharness.tools.base import ToolExecutionContext
from qaapp.agents.capabilities import build_capability_catalog
from qaapp.agents.external import external_conversation_id
from qaapp.config import KIND_SUBAGENT, AgentEntry
from qaapp.server.session import ConversationSession, _normalize_permission_mode
from qaapp.tools.dangerous import DangerousPermissionChecker
from qaapp.tools.policy import build_agent_tool_registry

logger = logging.getLogger(__name__)


class ExecutionMixin:
    async def _run_harness(
        self, session: ConversationSession, agent: AgentEntry, message: str
    ) -> None:
        bundle = await self.ensure_bundle(session)
        engine = bundle.engine

        # 1) 先按 agents.json 的 tools / disallowed_tools 裁出本 Agent 的能力边界。
        agent_tools = build_agent_tool_registry(bundle.tool_registry, agent)
        engine.set_tool_registry(agent_tools)

        # 2) 系统提示 = agent 自带提示 + 动态能力清单 + 本会话工作区上下文。
        base_prompt = self._resolve_system_prompt(agent) or session.system_prompt_base or ""
        data_boundary = ""
        if self.data_runtime is not None:
            try:
                data_boundary = await asyncio.wait_for(
                    asyncio.to_thread(self.data_runtime.boundary_summary),
                    timeout=8.0,
                )
            except TimeoutError:
                logger.debug("读取数据边界超时，本轮省略数据边界")
            except Exception:
                logger.debug("读取数据边界失败", exc_info=True)
        catalog = build_capability_catalog(
            self.registry,
            current_agent_id=agent.id,
            available_tools={tool.name for tool in agent_tools.list_tools()},
            now=datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d"),
            data_boundary=data_boundary,
        )
        context_block = self._conversation_context(session)
        prompt_parts = [part for part in (base_prompt, catalog, context_block) if part]
        engine.set_system_prompt("\n\n".join(prompt_parts))

        if agent.model:
            engine.set_model(agent.model)
        if agent.max_turns:
            engine.set_max_turns(agent.max_turns)

        base_permission: PermissionSettings = engine._permission_checker._settings
        mode = PermissionMode(_normalize_permission_mode(agent.permission_mode))
        checker = DangerousPermissionChecker(
            base_permission.model_copy(update={"mode": mode}),
            self.gate,
            conversation_root=session.paths.root,
            auto_allow_tools=self.webapp.conversation_workspace.auto_allow_write_tools,
        )
        engine.set_permission_checker(checker)

        async for event in engine.submit_message(message):
            await self._handle_stream_event(session, event)

    async def _run_external(
        self, session: ConversationSession, agent: AgentEntry, message: str
    ) -> None:
        bundle = await self.ensure_bundle(session)
        tool = bundle.tool_registry.get("external_agent")
        if tool is None:
            self._emit(session, "error", {"text": "external_agent 工具未加载（插件缺失）"})
            return

        async def _stream(text: str) -> None:
            if text:
                self._emit(session, "assistant_delta", {"text": text})

        attachment_block = self._attachment_message_block(session)
        effective_message = (
            f"{attachment_block}\n\n[用户问题]\n{message}" if attachment_block else message
        )

        arguments = tool.input_model(
            agent=agent.external_agent_id,
            message=effective_message,
            conversation_id=external_conversation_id(
                self.registry, agent, session.conversation_id
            ),
        )
        context = ToolExecutionContext(cwd=Path(session.cwd), stream_text=_stream)
        result = await tool.execute(arguments, context)

        if result.is_error:
            self._emit(session, "error", {"text": result.output})
        else:
            self._emit(session, "assistant_complete", {"text": result.output})
            engine = bundle.engine
            engine.load_messages(
                engine.messages
                + [
                    ConversationMessage.from_user_text(message),
                    ConversationMessage(
                        role="assistant", content=[TextBlock(text=result.output)]
                    ),
                ]
            )

    async def _handle_stream_event(
        self, session: ConversationSession, event: StreamEvent
    ) -> None:
        if isinstance(event, AssistantTextDelta):
            self._emit(session, "assistant_delta", {"text": event.text})
        elif isinstance(event, AssistantReasoningDelta):
            self._emit(session, "reasoning_delta", {"text": event.text})
        elif isinstance(event, AssistantTurnComplete):
            self._emit(session, "assistant_complete", {"text": event.message.text})
        elif isinstance(event, ToolExecutionStarted):
            session.last_tool_inputs[event.tool_name] = event.tool_input
            self._emit(
                session,
                "tool_started",
                {"tool_name": event.tool_name, "tool_input": event.tool_input},
            )
        elif isinstance(event, ToolExecutionCompleted):
            self._emit(
                session,
                "tool_completed",
                {
                    "tool_name": event.tool_name,
                    "output": event.output,
                    "is_error": event.is_error,
                },
            )
        elif isinstance(event, ToolExecutionProgress):
            self._emit(
                session,
                "tool_progress",
                {"tool_name": event.tool_name, "text": event.text},
            )
        elif isinstance(event, StatusEvent):
            self._emit(session, "status", {"text": event.message})
        elif isinstance(event, ErrorEvent):
            self._emit(session, "error", {"text": event.message})
        elif isinstance(event, CompactProgressEvent):
            self._emit(session, "status", {"text": event.message or event.phase})

    def _resolve_system_prompt(self, agent: AgentEntry) -> str:
        """解析 agent 的系统提示：harness 用自身配置，subagent 复用 AgentDefinition。"""
        if agent.system_prompt:
            return agent.system_prompt
        if agent.system_prompt_file:
            path = Path(agent.system_prompt_file).expanduser()
            if path.exists():
                return path.read_text(encoding="utf-8")
        if agent.kind == KIND_SUBAGENT and agent.subagent_type:
            try:
                from openharness.coordinator.agent_definitions import get_agent_definition

                definition = get_agent_definition(agent.subagent_type)
                if definition is not None and definition.system_prompt:
                    return definition.system_prompt
            except (ImportError, ValueError, OSError):
                logger.debug(
                    "加载 subagent 定义失败 type=%s", agent.subagent_type, exc_info=True
                )
            return f"You are acting as the '{agent.subagent_type}' subagent."
        return ""
