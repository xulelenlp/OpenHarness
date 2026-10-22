"""把 ``agents.json`` 里内联的外部 Agent 配置注入 ``external_agent`` 工具。

**唯一维护文件 = ``agents.json``（ohmo workspace）**：外部 Agent 的 endpoint /
method / headers / request_body / response 全部写在那里，不再要求另外维护一份
``external-agents.json``。

做法：会话 bundle 建好后，用**插件自己的模型类**把内联配置构造成
``ExternalAgentsConfig``，再注册一个「内联优先」的工具实例覆盖原来的
``external_agent``（内存注入，不落地第二份配置文件）。插件的文件发现仍然保留，
因此旧配置（``~/.openharness/external-agents.json`` 等）依旧能作为兜底生效。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from qaapp.config import KIND_EXTERNAL, AgentEntry, AgentRegistry
from qaapp.paths import PLUGINS_DIR

logger = logging.getLogger(__name__)

_PLUGIN_TOOL_PATH = (
    PLUGINS_DIR / "external-agents" / "tools" / "external_agent_tool.py"
)
#: 兜底加载插件模块时使用的名字（会登记进 sys.modules）。
_FALLBACK_MODULE_NAME = "qaapp_external_agent_tool"


def inline_external_entries(registry: AgentRegistry) -> list[AgentEntry]:
    """返回配置了内联 ``external`` 的 external Agent（保持声明顺序）。"""
    return [
        agent
        for agent in registry.agents
        if agent.kind == KIND_EXTERNAL and agent.external is not None
    ]


def _plugin_module_for(tool: Any) -> ModuleType | None:
    """拿到插件工具所属的模块（用于复用它的 pydantic 模型）。"""
    module_name = type(tool).__module__
    module = sys.modules.get(module_name)
    if module is not None and hasattr(module, "ExternalAgentsConfig"):
        return module
    if not _PLUGIN_TOOL_PATH.is_file():
        return None
    # 兜底：插件没有被自动加载时（例如单测直接调 qaapp）按路径加载一次。
    # 必须先把模块登记进 sys.modules：pydantic 解析前向引用（AgentResponseConfig）
    # 时按类的 __module__ 去 sys.modules 里查，否则会报 "not fully defined"。
    cached = sys.modules.get(_FALLBACK_MODULE_NAME)
    if cached is not None and hasattr(cached, "ExternalAgentsConfig"):
        return cached
    spec = importlib.util.spec_from_file_location(_FALLBACK_MODULE_NAME, _PLUGIN_TOOL_PATH)
    if spec is None or spec.loader is None:
        return None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    try:
        spec.loader.exec_module(loaded)
    except Exception:
        sys.modules.pop(spec.name, None)
        logger.exception("加载 external-agents 插件模块失败：%s", _PLUGIN_TOOL_PATH)
        return None
    return loaded


def build_inline_config(registry: AgentRegistry, module: ModuleType) -> Any:
    """把 agents.json 的内联配置转换成插件的 ``ExternalAgentsConfig``。"""
    agent_config_cls = module.AgentConfig
    config_cls = module.ExternalAgentsConfig
    agents = []
    for entry in inline_external_entries(registry):
        spec = entry.external
        assert spec is not None  # inline_external_entries 已保证
        agents.append(
            agent_config_cls.model_validate(
                {
                    "id": entry.external_agent_id,
                    "type": spec.type,
                    # name/description 用注册表里的（模型据此路由到正确的 agent）
                    "name": entry.name or entry.id,
                    "description": entry.description,
                    "enabled": bool(entry.enabled and spec.enabled),
                    "conversation_id": spec.conversation_id,
                    "timeout_seconds": spec.timeout_seconds,
                    "max_response_chars": spec.max_response_chars,
                    "progress_max_chars": spec.progress_max_chars,
                    "response": spec.response,
                    "config": spec.config,
                }
            )
        )
    return config_cls(agents=agents)


def merge_configs(base: Any, inline: Any) -> Any:
    """按 agent id 合并：``agents.json`` 的内联配置优先于文件里的同名 agent。"""
    merged = {agent.id: agent for agent in getattr(base, "agents", [])}
    for agent in getattr(inline, "agents", []):
        merged[agent.id] = agent
    return type(inline)(agents=list(merged.values()))


def install_external_agent_tool(
    bundle: Any, registry: AgentRegistry, session_id: str = ""
) -> bool:
    """用「内联优先 + 会话 ID 统一」的工具实例覆盖 ``external_agent``。

    除了叠加内联配置，还解决一个实际问题：harness 总控**把 external_agent 当工具调用**
    时，模型通常不会传 ``conversation_id``，插件于是每次生成一个**随机 uuid**，对端就会
    为每次调用**新建一个会话**（平台侧会话存储一旦不稳，看到的就是「会话创建失败」）。
    qaapp 的语义是「一个 qaapp 会话 = 一个对端会话」，所以这里统一注入：

    - 配置里写了固定 ``conversation_id`` → 用它；
    - 配置是 ``auto`` / 空 → 用 ``session_id``（本 qaapp 会话 ID）。

    返回是否真的完成了注入（没有内联配置 / 插件缺失时返回 False，并给出告警）。
    """
    entries = inline_external_entries(registry)
    if not entries:
        return False
    tool = bundle.tool_registry.get("external_agent")
    if tool is None:
        logger.warning(
            "agents.json 里有 %d 个 external Agent 使用内联配置，但 external_agent "
            "工具未加载（plugins/external-agents 插件缺失），这些 Agent 不可用",
            len(entries),
        )
        return False
    module = _plugin_module_for(tool)
    if module is None:
        logger.warning("无法定位 external-agents 插件模块，内联外部 Agent 配置未生效")
        return False

    inline_config = build_inline_config(registry, module)
    base_cls = type(tool)

    class InlineExternalAgentTool(base_cls):  # type: ignore[misc, valid-type]
        """在插件原有文件发现之上叠加 agents.json 的内联配置。"""

        def __init__(
            self,
            inline: Any,
            config_path: Path | None = None,
            session_id: str = "",
        ) -> None:
            # 必须先赋值：插件 __init__ 会调用 _build_description() → _load_config()，
            # 描述里要包含内联 Agent，否则模型不知道可以调用它们。
            self._qaapp_inline_config = inline
            self._qaapp_session_id = session_id
            self._qaapp_conversation_ids: dict[str, str] = {}
            super().__init__(config_path)

        def _load_config(self, cwd: Path | None = None) -> Any:
            base = super()._load_config(cwd)
            return merge_configs(base, self._qaapp_inline_config)

        def _conversation_ids(self, cwd: Path | None) -> dict[str, str]:
            """每个外部 Agent 该用的 conversation_id（懒构建并按 cwd 缓存）。"""
            cache_key = str(cwd) if cwd else ""
            cached = getattr(self, "_qaapp_conversation_ids_cwd", None)
            if self._qaapp_conversation_ids and cached == cache_key:
                return self._qaapp_conversation_ids
            mapping: dict[str, str] = {}
            for agent in self._load_config(cwd).agents:
                configured = (getattr(agent, "conversation_id", "") or "").strip()
                mapping[agent.id] = (
                    configured if configured and configured != "auto" else self._qaapp_session_id
                )
            self._qaapp_conversation_ids = mapping
            self._qaapp_conversation_ids_cwd = cache_key
            return mapping

        async def execute(self, arguments: Any, context: Any) -> Any:
            agent_id = str(getattr(arguments, "agent", "") or "")
            conversation_id = self._conversation_ids(getattr(context, "cwd", None)).get(agent_id)
            if conversation_id and str(getattr(arguments, "conversation_id", "") or "").strip() != conversation_id:
                # 统一注入，避免模型不传时插件退化成「每次调用一个随机会话」。
                arguments = arguments.model_copy(update={"conversation_id": conversation_id})
            return await super().execute(arguments, context)

    bundle.tool_registry.register(InlineExternalAgentTool(inline_config, session_id=session_id))
    logger.info(
        "已从 agents.json 注入 %d 个外部 Agent（唯一维护文件）：%s",
        len(inline_config.agents),
        "、".join(agent.id for agent in inline_config.agents),
    )
    return True


def external_conversation_id(registry: AgentRegistry, agent: AgentEntry, default: str) -> str:
    """本次调用传给外部 Agent 的 ``conversation_id``。

    - ``external.conversation_id`` 为空 / ``auto``（默认）→ 用 qaapp 的会话 ID，
      即「一个 qaapp 会话对应一个对端会话」；
    - 配了固定值 → 每个 qaapp 会话都复用该值。对端报「会话创建失败」时，填一个
      已知可用的会话 ID 就能复用它、跳过创建过程。
    """
    spec = agent.external
    if spec is not None:
        fixed = (spec.conversation_id or "").strip()
        if fixed and fixed != "auto":
            return fixed
    return default


__all__ = [
    "build_inline_config",
    "external_conversation_id",
    "inline_external_entries",
    "install_external_agent_tool",
    "merge_configs",
]
