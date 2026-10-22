"""qaapp 分层配置测试：base → dev → local。"""

from __future__ import annotations

import json
from pathlib import Path

from qaapp.config import (
    config_status,
    load_agent_registry,
    load_webapp_config,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_layered_webapp_merges_object_overrides(tmp_path: Path):
    repo = tmp_path / "repo"
    base = repo / "config" / "backend" / "base" / "webapp.json"
    dev = repo / "config" / "backend" / "dev" / "webapp.json"
    _write(base, {"host": "0.0.0.0", "port": 8787, "browser_auth": {"enabled": True}})
    _write(dev, {"host": "127.0.0.1", "browser_auth": {"cookie_name": "dev_session"}})

    cfg = load_webapp_config(tmp_path / ".ohmo", cwd=repo)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8787
    assert cfg.browser_auth.cookie_name == "dev_session"
    assert cfg.browser_auth.enabled is True


def test_local_layer_wins_over_environment(tmp_path: Path):
    repo = tmp_path / "repo"
    base = repo / "config" / "backend" / "base" / "agents.json"
    prod = repo / "config" / "backend" / "prod" / "agents.json"
    local = repo / "config" / "backend" / "local" / "agents.json"
    _write(base, {"agents": [{"id": "base", "kind": "harness"}]})
    _write(prod, {"agents": [{"id": "prod", "kind": "harness"}]})
    _write(local, {"agents": [{"id": "local", "kind": "harness"}]})

    registry = load_agent_registry(tmp_path / ".ohmo", cwd=repo)
    # lists replace wholesale (documented behavior), highest layer wins
    assert [agent.id for agent in registry.agents] == ["local"]


def test_env_selects_environment_layer(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    base = repo / "config" / "backend" / "base" / "webapp.json"
    dev = repo / "config" / "backend" / "dev" / "webapp.json"
    prod = repo / "config" / "backend" / "prod" / "webapp.json"
    _write(base, {"port": 8787})
    _write(dev, {"port": 8801})
    _write(prod, {"port": 8802})

    monkeypatch.setenv("QAAPP_ENV", "prod")
    assert load_webapp_config(tmp_path / ".ohmo", cwd=repo).port == 8802


def test_config_status_reports_layers_and_env(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    _write(repo / "config" / "backend" / "base" / "agents.json", {"agents": []})
    _write(repo / "config" / "backend" / "dev" / "webapp.json", {"port": 8787})
    monkeypatch.setenv("QAAPP_ENV", "dev")

    status = config_status(tmp_path / ".ohmo", cwd=repo)
    assert status["env"] == "dev"
    assert status["agents"]["source"] == "base"
    assert status["webapp"]["source"] == "dev"
    assert [layer["layer"] for layer in status["agents"]["layers"]] == [
        "base",
        "dev",
        "local",
    ]
