"""qaapp Web 服务：对话 API（REST + SSE + WebSocket）。

基于 Starlette + uvicorn + sse-starlette，复用 OpenHarness 的 ``build_runtime``
运行时与 ohmo 的会话/轨迹存储。单个 gateway 进程内维护“每对话一个
RuntimeBundle”的会话池。
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from ohmo.session_storage import OhmoSessionBackend
from ohmo.workspace import get_workspace_root
from openharness.ui.runtime import close_runtime
from qaapp.config import (
    get_agents_config_path,
    get_dangerous_config_path,
    get_datasources_config_path,
    get_webapp_config_path,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
)
from qaapp.tools.dangerous import DangerousActionGate
from qaapp.data.datasources import (
    DataSourceConfigError,
    create_data_runtime,
    load_datasources_config,
)
from qaapp.workspace.trace import ConversationTraceStore
from qaapp.server.logging import configure_logging
from qaapp.server.runtime import RuntimeMixin
from qaapp.server.session import ConversationSession, PendingRequest

logger = logging.getLogger(__name__)

class QaWebService(RuntimeMixin):
    """对话 API 服务。"""

    def __init__(
        self,
        *,
        workspace: str | Path | None = None,
        cwd: str | Path | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        self._workspace = workspace
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        # 让 openharness 的运行时数据（记忆、日志等）也落在 workspace 下，
        # 便于整体迁移、隔离与多实例部署（若不显式设置则沿用全局默认）。
        root = get_workspace_root(workspace)
        if not os.environ.get("OPENHARNESS_DATA_DIR"):
            os.environ["OPENHARNESS_DATA_DIR"] = str(root / "data")
        if not os.environ.get("OPENHARNESS_LOGS_DIR"):
            os.environ["OPENHARNESS_LOGS_DIR"] = str(root / "logs")
        # 配置解析：--config-dir / QAAPP_CONFIG_DIR → <cwd>/config/（随 git 走）→ workspace。
        # 启动时把实际生效的文件打出来，避免「改了配置没生效」时无从判断。
        self._config_kwargs: dict[str, Any] = {"cwd": self.cwd, "config_dir": config_dir}
        self.webapp = load_webapp_config(workspace, **self._config_kwargs)
        self.registry = load_agent_registry(workspace, **self._config_kwargs)
        self.dangerous = load_dangerous_actions(workspace, **self._config_kwargs)
        # 数据源：唯一入口 datasources.json；未配置时 data_runtime=None，通用问答不受影响。
        self.data_runtime = None
        try:
            datasources = load_datasources_config(workspace, **self._config_kwargs)
            self.data_runtime = create_data_runtime(datasources, workspace, cwd=self.cwd)
            if self.data_runtime is None or not self.data_runtime.enabled:
                logger.info("数据源：未启用（datasources.json 缺失、未启用或必填项未配置）")
            else:
                mysql = datasources.mysql
                redis = datasources.redis
                logger.info(
                    "数据源已启用：mysql=%s:%s/%s（read_only=%s）；redis=%s:%s/db0",
                    mysql.host or "-",
                    mysql.port,
                    mysql.database or "-",
                    mysql.read_only,
                    redis.host or "-",
                    redis.port,
                )
        except DataSourceConfigError as exc:
            logger.error("数据源配置加载失败：%s", exc)
            self.data_runtime = None
        except Exception:  # 数据源故障不能阻断通用问答
            logger.exception("数据源初始化失败")
            self.data_runtime = None
        logger.info(
            "配置来源：agents=%s（%s 个 agent）dangerous=%s webapp=%s datasources=%s",
            get_agents_config_path(workspace, **self._config_kwargs),
            len(self.registry.agents),
            get_dangerous_config_path(workspace, **self._config_kwargs),
            get_webapp_config_path(workspace, **self._config_kwargs),
            get_datasources_config_path(workspace, **self._config_kwargs),
        )
        self.gate = DangerousActionGate(self.dangerous)
        self.trace_store = ConversationTraceStore(workspace)
        self.session_backend = OhmoSessionBackend(workspace)
        self._sessions: dict[str, ConversationSession] = {}
        self._lock = asyncio.Lock()
        #: 浏览器会话 Cookie 的值：进程内随机生成，不是 api_token 本身，也不下发到 JS。
        #: 服务重启后旧 Cookie 失效，前端下次加载会重新 bootstrap。
        self._browser_session_value = secrets.token_urlsafe(32)
        self._warn_unresolved_external_agents()
        from qaapp.server.api.app import build_app

        self.app = build_app(self)


    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_conversations(self, limit: int = 50, offset: int = 0) -> list[dict]:
        snapshots = self.session_backend.list_snapshots(Path(self.cwd), limit=limit + offset)
        return [
            {
                "conversation_id": item["session_id"],
                "summary": item["summary"],
                "message_count": item["message_count"],
                "model": item["model"],
                "created_at": item["created_at"],
            }
            for item in snapshots[offset:]
        ]

    def conversation_detail(self, conversation_id: str) -> dict | None:
        snapshot = self.session_backend.load_by_id(Path(self.cwd), conversation_id)
        if snapshot is None:
            return None
        trace = self.trace_store.load_trace(conversation_id)
        turns: list[dict] = []
        by_turn: dict[str, list[dict]] = {}
        for event in trace:
            turn_id = event.get("turn_id")
            if not turn_id:
                continue
            by_turn.setdefault(turn_id, []).append(event)
        for turn_id, events in by_turn.items():
            message_id = next((e.get("message_id") for e in events if e.get("message_id")), "")
            turns.append({"turn_id": turn_id, "message_id": message_id, "events": events})
        return {
            "conversation_id": conversation_id,
            "summary": snapshot.get("summary", ""),
            "model": snapshot.get("model", ""),
            "created_at": snapshot.get("created_at"),
            "messages": snapshot.get("messages", []),
            "turns": turns,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            if session.bundle is not None:
                try:
                    await close_runtime(session.bundle)
                except Exception:
                    logger.exception("close_runtime failed for %s", session.conversation_id)
        self._sessions.clear()
        if self.data_runtime is not None:
            self.data_runtime.close()

    # ------------------------------------------------------------------
    # Starlette 应用
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # 进程入口
    # ------------------------------------------------------------------

    def run(self, host: str | None = None, port: int | None = None) -> None:
        import uvicorn

        configure_logging()

        resolved_host = host or self.webapp.host
        resolved_port = port if port is not None else self.webapp.port

        # 安全兜底：默认监听所有网卡且未配 token 时，同网段任何人都能调用对话 API，
        # 进而触发文件写入 / shell 执行。这里给出醒目告警而不是静默开放。
        if resolved_host in {"0.0.0.0", "::", ""} and not self.webapp.api_tokens:
            logger.warning(
                "安全告警：服务监听 %s:%s（所有网卡）但 api_tokens 为空——"
                "同一局域网内任何人都能无需鉴权调用对话 API，并触发文件写入 / shell 执行。"
                "请设置 api_tokens（见 README §9.1），或把 webapp.json 的 host 改回 127.0.0.1。",
                resolved_host,
                resolved_port,
            )

        if (
            self.webapp.api_tokens
            and self.webapp.browser_auth.enabled
            and "*" in self.webapp.cors_origins
        ):
            logger.warning(
                "浏览器会话提示：browser_auth 已启用且 cors_origins 含 '*'。"
                "同源部署（static_dir 托管前端）可忽略；前后端分机部署时浏览器会拒绝"
                "带凭据的跨域响应，请把 cors_origins 改成明确的前端来源（见 README §9.1）。"
            )

        uvicorn.run(
            self.app,
            host=resolved_host,
            port=resolved_port,
            log_level="info",
        )


__all__ = [
    "ConversationSession",
    "PendingRequest",
    "QaWebService",
    "configure_logging",
]
