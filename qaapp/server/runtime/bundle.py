"""Lazy runtime-bundle construction for one conversation."""

from __future__ import annotations

import logging
from pathlib import Path

from openharness.ui.runtime import RuntimeBundle, build_runtime, start_runtime
from qaapp.agents.external import install_external_agent_tool
from qaapp.paths import PLUGINS_DIR, SKILLS_DIR
from qaapp.server.session import ConversationSession, _edit_key
from qaapp.tools.consult import ConsultAgentsTool
from qaapp.tools.db_query import DbQueryTool
from qaapp.tools.report import WriteReportDocxTool
from qaapp.tools.script import CONVERSATION_DIR_KEY, RunScriptTool

logger = logging.getLogger(__name__)



class BundleMixin:
    async def ensure_bundle(self, session: ConversationSession) -> RuntimeBundle:
        """懒构建/恢复一个会话的运行时 bundle。"""
        if session.bundle is not None:
            return session.bundle

        snapshot = self.session_backend.load_by_id(Path(session.cwd), session.conversation_id)
        restore_messages = snapshot.get("messages") if snapshot else None
        restore_tool_metadata = snapshot.get("tool_metadata") if snapshot else None

        async def _permission_prompt(tool_name: str, reason: str) -> bool:
            if tool_name in session.always_approved or tool_name in session.turn_approved:
                return True
            payload = {
                "tool": tool_name,
                "reason": reason,
                "input": session.last_tool_inputs.get(tool_name),
            }
            value = await self._request_approval(session, "permission", payload)
            return bool(value)

        async def _ask_user_prompt(question: str, options: list[dict]) -> str:
            payload = {
                "question": question,
                "options": options,
                "allow_other": True,
            }
            value = await self._request_approval(session, "question", payload)
            return str(value or "")

        async def _edit_approval_prompt(path: str, diff: str, added: int, removed: int) -> str:
            if _edit_key(path) in session.turn_approved:
                return "once"
            value = await self._request_approval(
                session, "edit", {"path": path, "diff": diff, "added": added, "removed": removed}
            )
            return value if value in {"once", "always"} else "reject"

        kwargs: dict = {
            "cwd": session.cwd,
            "permission_prompt": _permission_prompt,
            "ask_user_prompt": _ask_user_prompt,
            "edit_approval_prompt": _edit_approval_prompt,
            "restore_messages": restore_messages,
            "restore_tool_metadata": restore_tool_metadata,
            "session_backend": self.session_backend,
            "extra_skill_dirs": (str(SKILLS_DIR),),
            "extra_plugin_roots": (str(PLUGINS_DIR),),
        }
        if self.webapp.provider_profile:
            kwargs["active_profile"] = self.webapp.provider_profile

        bundle = await build_runtime(**kwargs)
        await start_runtime(bundle)
        bundle.tool_registry.register(RunScriptTool(self.webapp.conversation_workspace))
        install_external_agent_tool(bundle, self.registry, session.conversation_id)
        bundle.tool_registry.register(
            DbQueryTool(self.data_runtime.query if self.data_runtime is not None else None)
        )
        bundle.tool_registry.register(ConsultAgentsTool(session.conversation_id))
        bundle.tool_registry.register(WriteReportDocxTool())
        bundle.engine.tool_metadata[CONVERSATION_DIR_KEY] = str(session.paths.root)
        bundle.engine.tool_metadata["qaapp_conversation_id"] = session.conversation_id
        session.system_prompt_base = bundle.engine.system_prompt
        session.bundle = bundle
        return bundle
