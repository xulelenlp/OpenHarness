"""临时登录（TEMP-AUTH）：登录、会话、角色与限流测试。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ohmo.workspace import initialize_workspace
from qaapp.server import QaWebService

ADMIN = {"username": "admin", "password": "Admin@Qa2026"}
VIEWER = {"username": "viewer", "password": "Viewer@Qa2026"}


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
def auth_service(tmp_path: Path, monkeypatch):
    _set_test_model_env(monkeypatch, tmp_path)
    workspace = tmp_path / ".ohmo"
    initialize_workspace(workspace)
    (workspace / "webapp.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 0,
                "provider_profile": "",
                "auth": {"enabled": True, "max_failed_attempts": 5, "lockout_seconds": 60},
            }
        ),
        encoding="utf-8",
    )
    svc = QaWebService(workspace=workspace, cwd=str(tmp_path))
    svc.workspace_path = workspace  # 测试断言用户文件位置
    return svc


def _login(client: TestClient, credentials: dict[str, str]):
    return client.post("/api/v1/auth/login", json=credentials)


def test_login_required_and_session_cookie(auth_service):
    with TestClient(auth_service.app) as client:
        assert client.get("/api/v1/conversations").status_code == 401

        info = client.get("/api/v1/bootstrap").json()
        assert info["login_required"] is True
        assert info["browser_session"] is False
        assert info["default_credentials"] is True

        wrong = _login(client, {"username": "admin", "password": "wrong-password"})
        assert wrong.status_code == 401
        assert wrong.json()["code"] == "invalid_credentials"

        ok = _login(client, ADMIN)
        assert ok.status_code == 200
        assert ok.json()["user"]["role"] == "admin"
        cookie_header = ok.headers["set-cookie"].lower()
        assert "qaapp_auth=" in cookie_header
        assert "httponly" in cookie_header
        assert "samesite=lax" in cookie_header

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["username"] == "admin"
        assert client.get("/api/v1/conversations").status_code == 200

        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/conversations").status_code == 401


def test_login_rate_limit(auth_service):
    with TestClient(auth_service.app) as client:
        for _ in range(5):
            assert _login(client, {"username": "admin", "password": "bad"}).status_code == 401
        locked = _login(client, {"username": "admin", "password": "bad"})
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0
        # 锁定期间即使密码正确也拒绝，避免被用来做在线爆破。
        assert _login(client, ADMIN).status_code == 429


def test_viewer_is_read_only(auth_service):
    with TestClient(auth_service.app) as client:
        assert _login(client, VIEWER).status_code == 200
        assert client.get("/api/v1/conversations").status_code == 200
        denied = client.post("/api/v1/conversations")
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"
        # 只读账户仍可退出登录。
        assert client.post("/api/v1/auth/logout").status_code == 200


def test_cross_site_write_is_rejected(auth_service):
    with TestClient(auth_service.app) as client:
        assert _login(client, ADMIN).status_code == 200
        resp = client.post(
            "/api/v1/conversations",
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "cross_site"
        # 同源写请求正常。
        same_origin = client.post(
            "/api/v1/conversations", headers={"Origin": "http://testserver"}
        )
        assert same_origin.status_code == 201


def test_change_password_and_revoke_sessions(auth_service):
    with TestClient(auth_service.app) as client:
        assert _login(client, ADMIN).status_code == 200
        changed = client.post(
            "/api/v1/auth/password",
            json={"old_password": ADMIN["password"], "new_password": "NewAdmin2026"},
        )
        assert changed.status_code == 200
        assert changed.json()["relogin"] is True
        # 改密后旧会话立即失效。
        assert client.get("/api/v1/conversations").status_code == 401

        assert _login(client, ADMIN).status_code == 401
        assert _login(
            client, {"username": "admin", "password": "NewAdmin2026"}
        ).status_code == 200


def test_default_users_file_is_hashed(auth_service):
    users_file = Path(auth_service.workspace_path) / "users.json"
    raw = users_file.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert [item["username"] for item in payload["users"]] == ["admin", "viewer"]
    assert all(item["role"] in {"admin", "viewer"} for item in payload["users"])
    assert "scrypt$" in raw
    assert ADMIN["password"] not in raw
    assert VIEWER["password"] not in raw
    assert stat.S_IMODE(users_file.stat().st_mode) == 0o600


def test_seed_password_from_env(tmp_path: Path, monkeypatch):
    _set_test_model_env(monkeypatch, tmp_path)
    monkeypatch.setenv("QAAPP_DEFAULT_ADMIN_PASSWORD", "EnvAdmin2026")
    workspace = tmp_path / "ws"
    initialize_workspace(workspace)
    (workspace / "webapp.json").write_text(
        json.dumps({"host": "127.0.0.1", "port": 0, "auth": {"enabled": True}}),
        encoding="utf-8",
    )
    svc = QaWebService(workspace=workspace, cwd=str(tmp_path))
    with TestClient(svc.app) as client:
        assert _login(
            client, {"username": "admin", "password": "EnvAdmin2026"}
        ).status_code == 200
        info = client.get("/api/v1/bootstrap").json()
        # 管理员已用环境变量覆盖，但 viewer 仍是默认密码。
        assert info["default_credentials"] is True


def test_api_token_still_works_with_login_enabled(auth_service):
    auth_service.webapp.api_tokens = ["secret"]
    with TestClient(auth_service.app) as client:
        assert client.get("/api/v1/conversations").status_code == 401
        resp = client.get(
            "/api/v1/conversations", headers={"Authorization": "Bearer secret"}
        )
        assert resp.status_code == 200
