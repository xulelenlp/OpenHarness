"""qaapp: 面向客户 APP/Web 端的问答产品服务。

基于 OpenHarness/ohmo 能力构建，提供：
- 配置化多 Agent 接入与灵活路由（agents.json）
- 高危操作文件可配置化（dangerous-actions.json）
- 对话 API（REST + SSE/WebSocket）
- 对话历史与执行过程轨迹存储
"""

from __future__ import annotations

from openharness.version import __version__

__all__ = ["__version__"]
