"""qaapp Web 服务 HTTP 集成测试。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from ohmo.workspace import initialize_workspace
from qaapp.service import ConversationSession, QaWebService, _edit_key


class _MockAgentHandler(BaseHTTPRequestHandler):
    body = "你好，我是外部智能体。\n第二行回复。\n"
    #: 记录外部 Agent 收到的请求体，用来验证「附件内容确实被带过去了」。
    received: ClassVar[list[dict]] = []
    #: 记录请求路径，用来验证内联配置的 endpoint 被正确使用。
    paths: ClassVar[list[str]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        _MockAgentHandler.paths.append(self.path)
        try:
            _MockAgentHandler.received.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _MockAgentHandler.received.append({"raw": raw.decode("utf-8", "replace")})
        payload = self.body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture()
def mock_agent_server():
    server = HTTPServer(("127.0.0.1", 0), _MockAgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=2)


def _write_agents(workspace: Path, endpoint: str) -> None:
    agents = {
        "default_agent": "external-agent",
        "routing": {
            "mode": "rule_then_llm",
            "rules": [{"agent": "external-agent", "keywords": ["分析"], "priority": 100}],
        },
        "agents": [
            {
                "id": "external-agent",
                "kind": "external",
                "name": "外部智能体",
                "description": "调用外部 HTTP 智能体",
                "external_ref": "mock-agent",
            }
        ],
    }
    (workspace / "agents.json").write_text(json.dumps(agents, ensure_ascii=False), encoding="utf-8")

    # external-agents.json 需在配置目录可被 external_agent 工具发现；这里写到
    # 项目级覆盖位置（cwd/.openharness/external-agents.json）。
    project_cfg = workspace.parent / ".openharness"
    project_cfg.mkdir(parents=True, exist_ok=True)
    (project_cfg / "external-agents.json").write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "mock-agent",
                        "type": "http",
                        "name": "mock",
                        "response": {"mode": "text"},
                        "config": {
                            "endpoint": endpoint,
                            "method": "POST",
                            "headers": {"content-type": "application/json"},
                            "request_body": {"message": "{message}"},
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def service(tmp_path: Path, mock_agent_server: str, monkeypatch):
    # 指向仓库自带的 .openharness 配置（含可用的 qwen profile + api key），
    # 使 build_runtime 能成功解析 API client（测试中不会真正发起模型调用）。
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(repo_root / ".openharness"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))

    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)
    _write_agents(workspace, mock_agent_server)
    (workspace / "webapp.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 0, "provider_profile": ""}),
        encoding="utf-8",
    )
    svc = QaWebService(workspace=workspace, cwd=str(tmp_path))
    yield svc
    # 无需显式 close（测试环境不真正起 MCP/沙箱）


def test_healthz(service):
    with TestClient(service.app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_list_agents(service):
    with TestClient(service.app) as client:
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert [a["id"] for a in resp.json()["agents"]] == ["external-agent"]


def test_conversation_lifecycle(service):
    with TestClient(service.app) as client:
        created = client.post("/api/v1/conversations")
        assert created.status_code == 201
        cid = created.json()["conversation_id"]

        listing = client.get("/api/v1/conversations")
        assert listing.status_code == 200

        detail = client.get(f"/api/v1/conversations/{cid}")
        assert detail.status_code == 200
        assert detail.json()["conversation_id"] == cid

        missing = client.get("/api/v1/conversations/nonexistent")
        assert missing.status_code == 404


def test_auth_required(service):
    service.webapp.api_tokens = ["secret"]
    with TestClient(service.app) as client:
        assert client.get("/api/v1/conversations").status_code == 401
        resp = client.get(
            "/api/v1/conversations", headers={"Authorization": "Bearer secret"}
        )
        assert resp.status_code == 200


def test_browser_session_bootstrap_removes_manual_token(service):
    """配置了 api_tokens 后，前端只需 bootstrap 一次即可免手填 token。"""
    service.webapp.api_tokens = ["secret"]
    with TestClient(service.app) as client:
        info = client.get("/api/v1/bootstrap")
        assert info.status_code == 200
        assert info.json()["auth_required"] is True
        assert info.json()["browser_session"] is True
        # TestClient 会保存 Set-Cookie；后续请求自动携带 HttpOnly 会话 Cookie。
        assert client.get("/api/v1/conversations").status_code == 200

        # 会话 Cookie 不是 token 本身，也不应出现在响应体里。
        assert "secret" not in info.text
        cookie = client.cookies.get("qaapp_session")
        assert cookie and cookie != "secret"


def test_browser_session_can_be_disabled(service):
    service.webapp.api_tokens = ["secret"]
    service.webapp.browser_auth.enabled = False
    with TestClient(service.app) as client:
        info = client.get("/api/v1/bootstrap")
        assert info.status_code == 200
        assert info.json()["auth_required"] is True
        assert info.json()["browser_session"] is False
        assert "qaapp_session" not in client.cookies
        assert client.get("/api/v1/conversations").status_code == 401
        # Bearer 仍然可用（API 客户端 / 反向代理注入的场景）。
        resp = client.get(
            "/api/v1/conversations", headers={"Authorization": "Bearer secret"}
        )
        assert resp.status_code == 200


def test_bootstrap_without_tokens_needs_no_auth(service):
    with TestClient(service.app) as client:
        info = client.get("/api/v1/bootstrap")
        assert info.status_code == 200
        assert info.json()["auth_required"] is False
        assert info.json()["browser_session"] is False
        assert client.get("/api/v1/conversations").status_code == 200


def test_explicit_unknown_agent(service):
    with TestClient(service.app) as client:
        created = client.post("/api/v1/conversations")
        cid = created.json()["conversation_id"]
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "你好", "agent": "nope", "stream": False},
        )
        assert resp.status_code == 404


def test_external_agent_stream(service):
    with TestClient(service.app) as client:
        created = client.post("/api/v1/conversations")
        cid = created.json()["conversation_id"]

        with client.stream(
            "POST",
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "分析一下", "stream": True},
        ) as resp:
            assert resp.status_code == 200
            events = []
            data_lines = []
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    events.append(line[len("event:"):].strip())
                elif line.startswith("data:"):
                    data_lines.append(json.loads(line[len("data:"):].strip()))

        assert "agent_routed" in events
        assert "turn_complete" in events
        types = [d["type"] for d in data_lines]
        assert "agent_routed" in types
        assert "assistant_delta" in types
        assert "assistant_complete" in types
        # 会话详情应包含轨迹
        detail = client.get(f"/api/v1/conversations/{cid}").json()
        assert detail["turns"]


def test_approval_mechanism(service):
    import asyncio

    async def _run():
        session = ConversationSession("c1", service)
        request_task = asyncio.create_task(
            service._request_approval(session, "permission", {"tool": "file_write"})
        )
        await asyncio.sleep(0.05)
        assert len(session.pending) == 1
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "approve", None)
        result = await request_task
        assert result is True

    asyncio.run(_run())


def test_resolve_approval_is_idempotent(service):
    """重复提交同一审批应幂等成功，而不是抛 KeyError 让前端弹「已过期」红条。"""
    import asyncio

    async def _run():
        session = ConversationSession("c-dup", service)
        task = asyncio.create_task(
            service._request_approval(session, "permission", {"tool": "bash"})
        )
        await asyncio.sleep(0.05)
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "approve", None)
        assert await task is True

        # 第二次提交同一 id：静默通过，不再抛错
        service.resolve_approval(session, request_id, "approve", None)

        # 从未存在过的 id 仍应报错，避免掩盖真实问题
        with pytest.raises(KeyError):
            service.resolve_approval(session, "never-existed", "approve", None)

    asyncio.run(_run())


def test_single_approve_dedups_same_tool_within_turn(service):
    """同一轮内点一次「允许」后，同工具应被记忆，避免反复弹窗。"""
    import asyncio

    async def _run():
        session = ConversationSession("c-dedup", service)
        task = asyncio.create_task(
            service._request_approval(session, "permission", {"tool": "bash"})
        )
        await asyncio.sleep(0.05)
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "approve", None)
        assert await task is True

        # 本轮内记忆该工具；但单次「允许」不应升级为会话级 always
        assert "bash" in session.turn_approved
        assert "bash" not in session.always_approved

        # 新一轮开始清空本轮记忆，保证新任务仍会确认一次
        session.turn_approved.clear()
        assert "bash" not in session.turn_approved

    asyncio.run(_run())


def test_always_approve_records_session_level(service):
    """「总是允许」应同时写入会话级与本轮记忆。"""
    import asyncio

    async def _run():
        session = ConversationSession("c-always", service)
        task = asyncio.create_task(
            service._request_approval(session, "permission", {"tool": "write_file"})
        )
        await asyncio.sleep(0.05)
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "always", None)
        assert await task is True
        assert "write_file" in session.always_approved
        assert "write_file" in session.turn_approved

    asyncio.run(_run())


def test_deny_does_not_mark_tool_approved(service):
    """拒绝不应被记忆成已批准（否则后续会被误放行）。"""
    import asyncio

    async def _run():
        session = ConversationSession("c-deny", service)
        task = asyncio.create_task(
            service._request_approval(session, "permission", {"tool": "bash"})
        )
        await asyncio.sleep(0.05)
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "deny", None)
        assert await task is False
        assert "bash" not in session.turn_approved
        assert "bash" not in session.always_approved

    asyncio.run(_run())


def test_edit_approval_records_file_key(service):
    """编辑审批批准后按文件路径记忆本轮；拒绝则不记忆。"""
    import asyncio

    async def _run():
        session = ConversationSession("c-edit", service)

        task = asyncio.create_task(
            service._request_approval(
                session, "edit", {"path": "/tmp/a.py", "diff": "", "added": 1, "removed": 0}
            )
        )
        await asyncio.sleep(0.05)
        request_id = next(iter(session.pending))
        service.resolve_approval(session, request_id, "approve", None)
        assert await task == "once"
        assert _edit_key("/tmp/a.py") in session.turn_approved

        task2 = asyncio.create_task(
            service._request_approval(
                session, "edit", {"path": "/tmp/b.py", "diff": "", "added": 1, "removed": 0}
            )
        )
        await asyncio.sleep(0.05)
        rid2 = next(iter(session.pending))
        service.resolve_approval(session, rid2, "deny", None)
        assert await task2 == "reject"
        assert _edit_key("/tmp/b.py") not in session.turn_approved

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 附件上传 / 会话临时目录 / run_script
# ---------------------------------------------------------------------------


def _new_conversation(client: TestClient) -> str:
    resp = client.post("/api/v1/conversations")
    assert resp.status_code == 201
    return resp.json()["conversation_id"]


def _make_docx(path: Path, text: str) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph(text)
    document.save(str(path))
    return path


def test_upload_txt_attachment(service):
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{cid}/attachments",
            files={"files": ("说明.txt", "售电量 256.08 亿千瓦时".encode(), "text/plain")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["conversation_id"] == cid
        (attachment,) = body["attachments"]
        assert attachment["name"] == "说明.txt"
        assert attachment["parse_status"] == "ok"
        assert attachment["text_chars"] > 0
        assert "售电量" in attachment["preview"]
        assert attachment["stored_path"] == "uploads/说明.txt"
        assert attachment["extracted_path"] == "extracted/说明.txt.txt"

        # 会话目录落在 workspace 下，且结构完整
        conv_dir = Path(service.webapp.conversation_workspace.root or "") / cid
        assert not service.webapp.conversation_workspace.root  # 默认走 workspace
        conv_dir = Path(service.session_backend._workspace) / "conversations" / cid
        for name in ("uploads", "extracted", "scripts", "tmp", "outputs"):
            assert (conv_dir / name).is_dir()
        assert (conv_dir / "uploads" / "说明.txt").is_file()
        assert "售电量" in (conv_dir / "extracted" / "说明.txt.txt").read_text(encoding="utf-8")

        # 附件索引可跨会话查询（落盘），列表接口能看到文件
        listing = client.get(f"/api/v1/conversations/{cid}/workspace")
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["dir"] == str(conv_dir)
        paths = {item["path"] for item in payload["files"]}
        assert {"uploads/说明.txt", "extracted/说明.txt.txt"} <= paths

        # 下载 + 目录穿越拦截
        download = client.get(f"/api/v1/conversations/{cid}/files/uploads/说明.txt")
        assert download.status_code == 200
        assert download.content == "售电量 256.08 亿千瓦时".encode()
        traversal = client.get(f"/api/v1/conversations/{cid}/files/../../etc/passwd")
        assert traversal.status_code == 404


def test_upload_docx_is_parsed(service, tmp_path: Path):
    source = _make_docx(tmp_path / "报告.docx", "第二季度售电量同比增长 5.2%")
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        with source.open("rb") as handle:
            resp = client.post(
                f"/api/v1/conversations/{cid}/attachments",
                files={"files": ("报告.docx", handle.read(), "application/octet-stream")},
            )
        assert resp.status_code == 201, resp.text
        (attachment,) = resp.json()["attachments"]
        assert attachment["kind"] == "word"
        assert attachment["parse_status"] == "ok"
        assert "售电量" in attachment["preview"]


def test_upload_enforces_limits(service):
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        # 一次 3 个文件 → 413
        resp = client.post(
            f"/api/v1/conversations/{cid}/attachments",
            files=[
                ("files", ("a.txt", b"a", "text/plain")),
                ("files", ("b.txt", b"b", "text/plain")),
                ("files", ("c.txt", b"c", "text/plain")),
            ],
        )
        assert resp.status_code == 413
        assert resp.json()["code"] == "too_many_files"

        # 不支持的类型 → 415
        resp = client.post(
            f"/api/v1/conversations/{cid}/attachments",
            files={"files": ("evil.exe", b"MZ", "application/octet-stream")},
        )
        assert resp.status_code == 415
        assert resp.json()["code"] == "unsupported_file_type"

        # 超过单文件大小上限 → 413，且不留半成品
        service.webapp.attachments.max_file_mb = 0.001
        try:
            resp = client.post(
                f"/api/v1/conversations/{cid}/attachments",
                files={"files": ("big.txt", b"x" * 4096, "text/plain")},
            )
            assert resp.status_code == 413
            assert resp.json()["code"] == "file_too_large"
            conv_dir = Path(service.session_backend._workspace) / "conversations" / cid
            assert not (conv_dir / "uploads" / "big.txt").exists()
        finally:
            service.webapp.attachments.max_file_mb = 20.0

        # 空表单 → 400
        resp = client.post(f"/api/v1/conversations/{cid}/attachments", data={"x": "1"})
        assert resp.status_code == 400


def test_send_message_with_attachment_reaches_external_agent(service):
    _MockAgentHandler.received.clear()
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        upload = client.post(
            f"/api/v1/conversations/{cid}/attachments",
            files={"files": ("数据.txt", "线损率 3.21%".encode(), "text/plain")},
        )
        attachment_id = upload.json()["attachments"][0]["attachment_id"]

        # 只上传文件、不输入文字也应能发送
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "", "attachment_ids": [attachment_id], "stream": False},
        )
        assert resp.status_code == 200, resp.text

        assert _MockAgentHandler.received, "外部 Agent 未收到请求"
        forwarded = json.dumps(_MockAgentHandler.received[-1], ensure_ascii=False)
        assert "线损率 3.21%" in forwarded

        # 轨迹里保留 attachments_ready，供前端历史回放
        detail = client.get(f"/api/v1/conversations/{cid}").json()
        events = [event for turn in detail["turns"] for event in turn["events"]]
        ready = [event for event in events if event["type"] == "attachments_ready"]
        assert ready
        assert ready[-1]["attachments"][0]["attachment_id"] == attachment_id


def test_send_message_attachment_validation(service):
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        # 不存在的附件 → 404
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "看看", "attachment_ids": ["att_missing"], "stream": False},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "attachment_not_found"

        # 超过单消息上限 → 422
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "看看", "attachment_ids": ["a", "b", "c"], "stream": False},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "too_many_files"

        # 既无文字又无附件 → 422
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "  ", "stream": False},
        )
        assert resp.status_code == 422


def test_conversation_id_traversal_rejected(service):
    with TestClient(service.app) as client:
        assert client.get("/api/v1/conversations/..%2f..%2fetc").status_code == 404
        assert (
            client.post(
                "/api/v1/conversations/not-a-conversation-id/messages",
                json={"content": "hi", "stream": False},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/conversations/not-a-conversation-id/attachments",
                files={"files": ("a.txt", b"a", "text/plain")},
            ).status_code
            == 404
        )


def test_workspace_and_download_expose_generated_scripts(service):
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        conv_dir = Path(service.session_backend._workspace) / "conversations" / cid
        (conv_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (conv_dir / "scripts" / "analyze.py").write_text("print('hi')\n", encoding="utf-8")
        (conv_dir / "outputs").mkdir(parents=True, exist_ok=True)
        (conv_dir / "outputs" / "result.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        listing = client.get(f"/api/v1/conversations/{cid}/workspace").json()
        paths = {item["path"] for item in listing["files"]}
        assert {"scripts/analyze.py", "outputs/result.csv"} <= paths
        assert listing["total_bytes"] > 0

        download = client.get(f"/api/v1/conversations/{cid}/files/scripts/analyze.py")
        assert download.status_code == 200
        assert b"print('hi')" in download.content


def test_run_script_tool_is_registered_with_conversation_dir(service):
    """bundle 建好后应注册 run_script，并把会话目录写进 tool_metadata。"""
    import asyncio

    from qaapp.script_tool import CONVERSATION_DIR_KEY

    async def _run():
        session = await service.get_or_create_session("abc123abc123", create=True)
        bundle = await service.ensure_bundle(session)
        assert bundle.tool_registry.get("run_script") is not None
        assert bundle.engine.tool_metadata[CONVERSATION_DIR_KEY] == str(session.paths.root)
        # 系统提示基线只保存一次，后续每轮在此基础上追加会话上下文
        assert session.system_prompt_base is not None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 唯一维护文件：agents.json 内联外部 Agent 配置
# ---------------------------------------------------------------------------


def _write_inline_agents(workspace: Path, endpoint: str, *, conversation_id: str = "auto") -> None:
    """把外部 Agent 的访问配置**内联**写进 agents.json（不再需要 external-agents.json）。"""
    agents = {
        "default_agent": "inline-agent",
        "routing": {"mode": "explicit", "llm_model": "", "rules": []},
        "agents": [
            {
                "id": "inline-agent",
                "kind": "external",
                "name": "内联外部智能体",
                "description": "内联配置的外部 Agent",
                "external": {
                    "type": "http",
                    "conversation_id": conversation_id,
                    "timeout_seconds": 30,
                    "response": {"mode": "text"},
                    "config": {
                        "endpoint": f"{endpoint}/inline/chat",
                        "method": "POST",
                        "headers": {"content-type": "application/json"},
                        "request_body": {
                            "message": "{message}",
                            "session": "{conversation_id}",
                        },
                    },
                },
            }
        ],
    }
    (workspace / "agents.json").write_text(
        json.dumps(agents, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture()
def inline_service(tmp_path: Path, mock_agent_server: str, monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(repo_root / ".openharness"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)
    _write_inline_agents(workspace, mock_agent_server)
    (workspace / "webapp.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 0, "provider_profile": ""}), encoding="utf-8"
    )
    return QaWebService(workspace=workspace, cwd=str(tmp_path))


def test_inline_external_agent_uses_configured_endpoint(inline_service):
    """agents.json 内联的 endpoint / request_body 真正生效，且会话 ID 用 qaapp 的。"""
    _MockAgentHandler.received.clear()
    _MockAgentHandler.paths.clear()
    with TestClient(inline_service.app) as client:
        cid = _new_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "分析一下售电量", "stream": False},
        )
        assert resp.status_code == 200, resp.text

    assert _MockAgentHandler.paths[-1] == "/inline/chat"
    payload = _MockAgentHandler.received[-1]
    assert payload["message"] == "分析一下售电量"
    # conversation_id=auto → 用 qaapp 的会话 ID（一个 qaapp 会话对应一个对端会话）
    assert payload["session"] == cid


def test_inline_external_agent_can_pin_conversation_id(tmp_path, mock_agent_server, monkeypatch):
    """对端「会话创建失败」时，可用固定 conversation_id 复用已有会话。"""
    _MockAgentHandler.received.clear()
    _MockAgentHandler.paths.clear()
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(repo_root / ".openharness"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(tmp_path / "logs"))
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)
    _write_inline_agents(workspace, mock_agent_server, conversation_id="fixed-session-001")
    (workspace / "webapp.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 0, "provider_profile": ""}), encoding="utf-8"
    )
    service = QaWebService(workspace=workspace, cwd=str(tmp_path))
    with TestClient(service.app) as client:
        cid = _new_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{cid}/messages",
            json={"content": "查一下售电量", "stream": False},
        )
        assert resp.status_code == 200, resp.text
    assert _MockAgentHandler.received[-1]["session"] == "fixed-session-001"


def test_agents_list_reports_inline_external_kind(inline_service):
    with TestClient(inline_service.app) as client:
        data = client.get("/api/v1/agents").json()
    assert [a["id"] for a in data["agents"]] == ["inline-agent"]
    assert data["agents"][0]["kind"] == "external"


@pytest.mark.asyncio
async def test_harness_injects_capability_catalog_and_filters_tools(service, monkeypatch):
    """harness 每轮注入动态能力清单，且 tools 白名单真正裁掉工具。"""
    from types import SimpleNamespace

    from pydantic import BaseModel

    from openharness.config.settings import PermissionSettings
    from openharness.tools.base import BaseTool, ToolRegistry, ToolResult
    from qaapp.config import AgentEntry

    class _Empty(BaseModel):
        pass

    class _FakeTool(BaseTool):
        description = "fake"
        input_model = _Empty

        def __init__(self, name: str) -> None:
            self.name = name

        async def execute(self, arguments, context):
            return ToolResult(output=self.name)

    class _FakeEngine:
        def __init__(self) -> None:
            # _run_harness 从权限检查器读取基础 settings。
            self._permission_checker = SimpleNamespace(_settings=PermissionSettings())
            self.system_prompt = ""
            self.tools: ToolRegistry | None = None
            self.model = ""
            self.max_turns = None
            self.checker = None

        def set_tool_registry(self, registry: ToolRegistry) -> None:
            self.tools = registry

        def set_system_prompt(self, prompt: str) -> None:
            self.system_prompt = prompt

        def set_model(self, model: str) -> None:
            self.model = model

        def set_max_turns(self, turns) -> None:
            self.max_turns = turns

        def set_permission_checker(self, checker) -> None:
            self.checker = checker

        async def submit_message(self, message):
            del message
            if False:  # 让函数成为 async generator，但不产生任何事件
                yield None

    base = ToolRegistry()
    base.register(_FakeTool("external_agent"))
    base.register(_FakeTool("run_script"))
    engine = _FakeEngine()
    bundle = SimpleNamespace(engine=engine, tool_registry=base)

    async def _ensure_bundle(session):
        del session
        return bundle

    monkeypatch.setattr(service, "ensure_bundle", _ensure_bundle)
    monkeypatch.setattr(service, "_conversation_context", lambda session: "CTX")

    session = ConversationSession("abcdef123456", service)
    agent = AgentEntry(
        id="general",
        kind="harness",
        system_prompt="BASE",
        tools=["external_agent"],
    )
    await service._run_harness(session, agent, "你好")

    # tools 白名单生效：run_script 被裁掉，模型工具表里只剩 external_agent。
    assert engine.tools is not None
    assert [tool.name for tool in engine.tools.list_tools()] == ["external_agent"]
    # 系统提示 = 自身提示 + 动态能力清单 + 会话上下文，且清单只列可用能力。
    assert "BASE" in engine.system_prompt
    assert "CTX" in engine.system_prompt
    assert "external-agent" in engine.system_prompt
    assert "本机执行能力" not in engine.system_prompt
