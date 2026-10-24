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
    apply_env_layers,
    get_agents_config_path,
    get_dangerous_config_path,
    get_datasources_config_path,
    get_webapp_config_path,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
)
from qaapp.data.datasources import (
    DataSourceConfigError,
    create_data_runtime,
    load_datasources_config,
)
from qaapp.server.auth import AuthManager
from qaapp.server.logging import configure_logging
from qaapp.server.runtime import RuntimeMixin
from qaapp.server.runtime.approvals import public_pending_payload
from qaapp.server.session import ConversationSession, PendingRequest
from qaapp.tools.dangerous import DangerousActionGate
from qaapp.workspace.trace import ConversationTraceStore

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
        apply_env_layers(workspace, cwd=self.cwd, config_dir=config_dir)
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
        auth_config = self.webapp.auth
        users_file = Path(auth_config.users_file).expanduser() if auth_config.users_file else root / "users.json"
        if not users_file.is_absolute():
            users_file = Path(self.cwd) / users_file
        self.auth = AuthManager(
            enabled=auth_config.enabled,
            users_file=users_file,
            session_ttl_seconds=auth_config.session_ttl_seconds,
            idle_timeout_seconds=auth_config.idle_timeout_seconds,
            cookie_name=auth_config.cookie_name,
            max_failed_attempts=auth_config.max_failed_attempts,
            lockout_seconds=auth_config.lockout_seconds,
            trust_proxy_headers=auth_config.trust_proxy_headers,
        )
        if auth_config.enabled:
            logger.info(
                "登录已启用：%d 个账户，会话 %d 秒（空闲 %d 秒），用户文件 %s",
                self.auth.account_count,
                auth_config.session_ttl_seconds,
                auth_config.idle_timeout_seconds,
                users_file,
            )
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
    # 审批状态同步（不依赖某一条 SSE 连接是否还活着）
    # ------------------------------------------------------------------

    def approval_snapshot(self) -> dict:
        """返回所有「内存中仍有活动回合」的会话的待审批与运行状态。

        页面刷新会断掉 ``POST /messages`` 的 SSE，但服务端的回合还在跑；
        前端靠这个轻量快照（每几秒一次）恢复审批卡片、角标与断线跟进。
        """
        approvals: list[dict] = []
        running: list[dict] = []
        for conversation_id, session in list(self._sessions.items()):
            current_turn = session.current_turn or {}
            if session.busy:
                running.append(
                    {
                        "conversation_id": conversation_id,
                        "turn_id": current_turn.get("turn_id"),
                        "message_id": current_turn.get("message_id"),
                        "started_at": session.busy_since,
                    }
                )
            for request in session.pending.values():
                approvals.append(
                    {
                        "conversation_id": conversation_id,
                        "request_id": request.request_id,
                        "kind": request.kind,
                        "payload": public_pending_payload(request.payload),
                        "created_at": request.created_at,
                        "turn_id": current_turn.get("turn_id"),
                    }
                )
        approvals.sort(key=lambda item: item.get("created_at") or 0)
        running.sort(key=lambda item: item.get("started_at") or 0)
        return {"approvals": approvals[:50], "running": running[:50]}

    def turn_tail(self, conversation_id: str, turn_id: str, after: int = 0) -> dict:
        """按游标增量读取一轮的轨迹，并给出该轮是否仍在运行。"""
        events, total, reset = self.trace_store.load_trace_tail(
            conversation_id, turn_id, after
        )
        session = self._sessions.get(conversation_id)
        running_turn = (
            (session.current_turn or {}).get("turn_id")
            if session is not None and session.busy
            else None
        )
        return {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "events": events,
            "total": total,
            "after": after,
            "reset": reset,
            "finished": running_turn != turn_id,
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

        # 安全兜底：默认监听所有网卡且既没有 token 也没有登录时，同网段任何人都能
        # 调用对话 API，进而触发文件写入 / shell 执行。这里给出醒目告警而不是静默开放。
        if (
            resolved_host in {"0.0.0.0", "::", ""}
            and not self.webapp.api_tokens
            and not self.webapp.auth.enabled
        ):
            logger.warning(
                "安全告警：服务监听 %s:%s（所有网卡）但既未配置 api_tokens 也未启用账户登录——"
                "同一局域网内任何人都能无需鉴权调用对话 API，并触发文件写入 / shell 执行。"
                "外网部署请开启 webapp.json 的 auth.enabled，或设置 api_tokens。",
                resolved_host,
                resolved_port,
            )

        if self.webapp.auth.enabled:
            if self.auth.default_passwords:
                logger.warning(
                    "安全告警：账户仍在使用默认密码（admin / viewer）。"
                    "公网部署前请登录后在右上角修改密码，或用 QAAPP_DEFAULT_ADMIN_PASSWORD "
                    "等环境变量在首次生成用户文件时覆盖默认密码。"
                )
            if (
                resolved_host in {"0.0.0.0", "::", ""}
                and not self.webapp.auth.trust_proxy_headers
            ):
                logger.info(
                    "登录提示：服务直接监听所有网卡。公网部署请放在 HTTPS 反向代理之后，"
                    "并设置 QAAPP_AUTH_TRUST_PROXY=true，登录 Cookie 才会带 Secure。"
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
