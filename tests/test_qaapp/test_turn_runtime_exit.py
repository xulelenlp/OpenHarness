"""单轮执行遇到 ``SystemExit`` 时必须降级成错误事件，而不是终止 Web 服务。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from qaapp.config import KIND_HARNESS
from qaapp.server.runtime import turns
from qaapp.server.runtime.turns import TurnsMixin


class _FakePaths:
    def __init__(self, root):
        self.root = root

    def touch_turn(self) -> None:
        pass


class _FakeSession:
    def __init__(self, root):
        self.conversation_id = "c1"
        self.current_turn = None
        self.turn_approved: set[str] = set()
        self.current_attachments: list = []
        self.last_error: str | None = None
        self.paths = _FakePaths(root)


class _StubService(TurnsMixin):
    """只保留 run_turn 需要的协作者，避免真的启动模型运行时。"""

    def __init__(self) -> None:
        self.registry = None
        self.events: list[tuple[str, dict]] = []

    async def _snapshot_turn_files(self, session):
        return None

    def resolve_attachments(self, session, ids):
        return []

    def _emit(self, session, event, payload):
        self.events.append((event, payload))

    async def _save_snapshot(self, session):
        return None

    async def _run_harness(self, session, agent, content):
        raise AssertionError("子类必须覆盖 _run_harness")


def _patch_resolve_agent(monkeypatch):
    async def fake_resolve_agent(content, *, registry=None, explicit_agent=None):
        return SimpleNamespace(
            agent=SimpleNamespace(id="harness-agent", kind=KIND_HARNESS),
            route="test",
        )

    monkeypatch.setattr(turns, "resolve_agent", fake_resolve_agent)


def test_system_exit_is_reported_as_error_event(tmp_path, monkeypatch):
    _patch_resolve_agent(monkeypatch)
    service = _StubService()

    async def run_harness(session, agent, content):
        try:
            raise ValueError("No credentials found for auth source 'openai_api_key'")
        except ValueError:
            raise SystemExit(1)

    service._run_harness = run_harness  # type: ignore[method-assign]

    session = _FakeSession(tmp_path)
    asyncio.run(service.run_turn(session, "你好", None))

    events = [event for event, _ in service.events]
    assert "error" in events
    assert events[-1] == "turn_complete"
    error_payload = next(payload for event, payload in service.events if event == "error")
    assert "No credentials found" in error_payload["text"]
    assert error_payload["code"] == "runtime_exit"
    assert session.last_error and "No credentials found" in session.last_error


def test_system_exit_without_cause_still_completes_turn(tmp_path, monkeypatch):
    _patch_resolve_agent(monkeypatch)
    service = _StubService()

    async def run_harness(session, agent, content):
        raise SystemExit(1)

    service._run_harness = run_harness  # type: ignore[method-assign]

    session = _FakeSession(tmp_path)
    asyncio.run(service.run_turn(session, "你好", None))

    events = [event for event, _ in service.events]
    assert "error" in events
    assert events[-1] == "turn_complete"
    assert session.last_error
