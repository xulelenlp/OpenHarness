"""Startup validation for external Agent configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from qaapp.config import KIND_EXTERNAL

logger = logging.getLogger(__name__)


def _external_id_exists(cwd: Path, agent_id: str) -> bool:
    """外部 Agent 的 id 是否能被 external_agent 工具解析到（插件文件发现）。"""
    from qaapp.agents.external import _plugin_module_for

    module = _plugin_module_for(object())
    if module is None:
        return False
    try:
        config = module.load_config(cwd)
    except Exception:
        logger.debug("读取 external-agents 配置失败", exc_info=True)
        return False
    return any(agent.id == agent_id for agent in getattr(config, "agents", []))


class StartupMixin:
    def _warn_unresolved_external_agents(self) -> None:
        """启动时检查 external Agent 是否有可用配置，避免运行时才暴露。"""
        missing: list[str] = []
        for agent in self.registry.agents:
            if agent.kind != KIND_EXTERNAL or agent.external is not None:
                continue
            if not agent.external_ref:
                missing.append(agent.id)
                continue
            ref = agent.external_ref
            if not _external_id_exists(Path(self.cwd), ref):
                missing.append(f"{agent.id}(external_ref={ref})")
        if missing:
            logger.error(
                "以下 external Agent 找不到访问配置：%s。"
                "请在 agents.json 里改用 external 内联配置（推荐，随 git 走），"
                "或补上 external-agents.json 里对应的 agent id。",
                "、".join(missing),
            )
