"""审批状态同步：跨会话快照、轨迹游标、断流后的会话状态。

覆盖这次线上事故的三个根因：
1. 审批事件只走 SSE，刷新后没有任何通道能恢复卡片；
2. SSE 断开时把 ``busy`` / ``current_task`` 清掉，后台回合变成“假空闲”；
3. ``approval_required`` 没有日志、没有统一事件通道，排障无据可查。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ohmo.workspace import initialize_workspace
from qaapp.server import QaWebService
from qaapp.server.runtime.events import sse_turn_stream
from qaapp.server.session import ConversationSession


def _set_test_model_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_PROFILE", "test")
    monkeypatch.setenv("OPENHARNESS_PROVIDER", "openai")
    monkeypatch.setenv("OPENHARNESS_API_FORMAT", "openai")
    monkeypatch.setenv("OPENHARNESS_MODEL", "deepseek-flash")
    monkeypatch.setenv("OPENHARNESS_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENHARNESS_AUTH_SOURCE", "dashscope_api_key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))


@pytest.fixture()
def service(tmp_path: Path, monkeypatch):
    _set_test_model_env(monkeypatch, tmp_path)
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)
    (workspace / "webapp.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 0, "provider_profile": ""}),
        encoding="utf-8",
    )
    return QaWebService(workspace=workspace, cwd=str(tmp_path))


def test_pending_snapshot_trims_payload_and_tracks_running(service):
    """跨会话快照要带对话归属/回合，并去掉大字段 input。"""

    async def _run():
        session = ConversationSession("c-snapshot", service)
        service._sessions["c-snapshot"] = session
        session.current_turn = {"turn_id": "t-1", "message_id": "m-1"}
        session.busy = True
        session.busy_since = 1792842686.0

        task = asyncio.create_task(
            service._request_approval(
                session,
                "permission",
                {"tool": "write_report_docx", "reason": "需要确认", "input": {"x": "y" * 200}},
            )
        )
        await asyncio.sleep(0.05)

        snapshot = service.approval_snapshot()
        assert [item["conversation_id"] for item in snapshot["running"]] == ["c-snapshot"]
        assert snapshot["running"][0]["turn_id"] == "t-1"
        assert snapshot["running"][0]["started_at"] == 1792842686.0

        approval = snapshot["approvals"][0]
        assert approval["conversation_id"] == "c-snapshot"
        assert approval["kind"] == "permission"
        assert approval["turn_id"] == "t-1"
        assert approval["payload"]["tool"] == "write_report_docx"
        assert "input" not in approval["payload"]
        assert approval["created_at"] > 0

        service.resolve_approval(session, approval["request_id"], "approve", None)
        assert await task is True
        assert service.approval_snapshot()["approvals"] == []

    asyncio.run(_run())


def test_approval_required_uses_event_channel(service, caplog):
    """审批请求必须写轨迹并留下 sse emit 日志（本次排障缺失的关键证据）。"""

    async def _run():
        with caplog.at_level(logging.INFO, logger="qaapp.server.runtime.events"):
            session = ConversationSession("c-channel", service)
            task = asyncio.create_task(
                service._request_approval(session, "permission", {"tool": "bash"})
            )
            await asyncio.sleep(0.05)
            request_id = next(iter(session.pending))
            service.resolve_approval(session, request_id, "deny", None)
            assert await task is False

        events = service.trace_store.load_trace("c-channel")
        required = [event for event in events if event.get("type") == "approval_required"]
        assert len(required) == 1
        assert required[0]["request_id"] == request_id
        assert required[0]["kind"] == "permission"

        messages = [record.getMessage() for record in caplog.records]
        assert any("type=approval_required" in message for message in messages), messages
        assert any(f"request={request_id}" in message for message in messages), messages

    asyncio.run(_run())


def test_trace_tail_cursor_and_finish_flag(service):
    """按 after 游标增量读轨迹；超出总数时要求整体重载。"""

    for index in range(3):
        service.trace_store.append(
            "c-tail",
            {
                "type": "tool_started",
                "turn_id": "t-1",
                "message_id": "m-1",
                "tool_name": f"tool-{index}",
            },
        )
    # 另一轮的事件不能混进来
    service.trace_store.append("c-tail", {"type": "tool_started", "turn_id": "t-2"})

    head = service.turn_tail("c-tail", "t-1", 0)
    assert [event["tool_name"] for event in head["events"]] == ["tool-0", "tool-1", "tool-2"]
    assert head["total"] == 3
    assert head["reset"] is False
    assert head["finished"] is True  # 没有内存会话 → 视为已结束

    tail = service.turn_tail("c-tail", "t-1", 2)
    assert [event["tool_name"] for event in tail["events"]] == ["tool-2"]
    assert tail["total"] == 3

    stale = service.turn_tail("c-tail", "t-1", 99)
    assert stale["events"] == []
    assert stale["reset"] is True

    session = ConversationSession("c-tail", service)
    service._sessions["c-tail"] = session
    session.current_turn = {"turn_id": "t-1", "message_id": "m-1"}
    session.busy = True
    assert service.turn_tail("c-tail", "t-1", 3)["finished"] is False


def test_sse_stream_close_does_not_reset_turn_state(service):
    """客户端断开（刷新页面）只退订，不能把仍在跑的回合标成空闲。"""

    async def _run():
        session = ConversationSession("c-stream", service)
        session.busy = True
        session.busy_since = 1.0
        session.current_task = asyncio.current_task()
        queue: asyncio.Queue = asyncio.Queue()
        session.subscribers.append(queue)

        stream = sse_turn_stream(session, queue)
        queue.put_nowait({"type": "tool_started", "tool_name": "bash"})
        assert (await stream.__anext__())["type"] == "tool_started"

        await stream.aclose()  # 等价于浏览器断开连接

        assert session.subscribers == []
        assert session.busy is True
        assert session.busy_since == 1.0
        assert session.current_task is asyncio.current_task()

    asyncio.run(_run())


def test_sse_stream_ends_and_unsubscribes_on_turn_complete(service):
    async def _run():
        session = ConversationSession("c-done", service)
        queue: asyncio.Queue = asyncio.Queue()
        session.subscribers.append(queue)
        stream = sse_turn_stream(session, queue)

        queue.put_nowait({"type": "status", "text": "ok"})
        queue.put_nowait({"type": "turn_complete"})
        assert (await stream.__anext__())["type"] == "status"
        assert (await stream.__anext__())["type"] == "turn_complete"
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert session.subscribers == []

    asyncio.run(_run())


def test_http_approval_snapshot_and_detail_busy(service):
    """HTTP 层：快照接口、详情里的 busy/turn_id、游标参数校验。"""

    with TestClient(service.app) as client:
        created = client.post("/api/v1/conversations")
        assert created.status_code == 201
        cid = created.json()["conversation_id"]

        snapshot = client.get("/api/v1/approvals")
        assert snapshot.status_code == 200
        assert snapshot.json() == {"approvals": [], "running": []}

        detail = client.get(f"/api/v1/conversations/{cid}")
        assert detail.json()["busy"] is False
        assert detail.json()["turn_id"] is None

        session = service._sessions[cid]
        session.current_turn = {"turn_id": "t-http", "message_id": "m-http"}
        session.busy = True
        session.busy_since = 10.0
        detail = client.get(f"/api/v1/conversations/{cid}")
        assert detail.json()["busy"] is True
        assert detail.json()["turn_id"] == "t-http"

        running = client.get("/api/v1/approvals").json()["running"]
        assert running == [
            {
                "conversation_id": cid,
                "turn_id": "t-http",
                "message_id": "m-http",
                "started_at": 10.0,
            }
        ]

        bad = client.get(f"/api/v1/conversations/{cid}/turns/t-http?after=abc")
        assert bad.status_code == 422
        missing = client.get(f"/api/v1/conversations/{cid}/turns/t-missing")
        assert missing.status_code == 404
