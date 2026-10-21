"""数据源配置集中管理测试。"""

from __future__ import annotations

import json
from pathlib import Path

from qaapp.data.datasources import (
    DataSourceConfigError,
    create_data_runtime,
    datasources_status,
    load_datasources_config,
    validate_datasources_config,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_placeholders_and_env_precedence(tmp_path: Path, monkeypatch):
    _write(
        tmp_path / "config" / "datasources.json",
        {
            "mysql": {
                "enabled": True,
                "host": "${DB_HOST}",
                "port": "${DB_PORT}",
                "user": "${DB_USER}",
                "password": "${DB_PASSWORD}",
                "database": "${DB_NAME}",
            },
            "redis": {"enabled": True, "host": "${REDIS_HOST}", "port": "${REDIS_PORT}"},
        },
    )
    (tmp_path / "config" / ".env").write_text(
        "DB_HOST=from-dotenv\nDB_PORT=3307\nDB_USER=u\nDB_PASSWORD=p\nDB_NAME=db\n"
        "REDIS_HOST=redis-dotenv\nREDIS_PORT=6380\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DB_HOST", "from-process")
    config = load_datasources_config(tmp_path / ".ohmo", cwd=tmp_path)
    assert config.mysql.host == "from-process"  # 进程环境优先
    assert config.mysql.port == 3307
    assert config.redis.host == "redis-dotenv"


def test_validation_rejects_redis_db1_and_incomplete_mysql(tmp_path: Path):
    _write(
        tmp_path / "config" / "datasources.json",
        {
            "mysql": {"enabled": True, "host": "", "user": "", "database": ""},
            "redis": {"enabled": True, "host": "localhost", "db": 1},
        },
    )
    errors = validate_datasources_config(tmp_path / ".ohmo", cwd=tmp_path)
    assert any("redis" in item.lower() for item in errors)


def test_validation_reports_incomplete_mysql(tmp_path: Path):
    _write(
        tmp_path / "config" / "datasources.json",
        {"mysql": {"enabled": True, "host": "", "user": "", "database": ""}, "redis": {"enabled": False}},
    )
    errors = validate_datasources_config(tmp_path / ".ohmo", cwd=tmp_path)
    assert any("mysql.enabled" in item for item in errors)


def test_status_masks_password(tmp_path: Path):
    _write(
        tmp_path / "config" / "datasources.json",
        {
            "mysql": {
                "enabled": True,
                "host": "db",
                "port": 3306,
                "user": "u",
                "password": "super-secret",
                "database": "d",
            },
            "redis": {"enabled": False},
        },
    )
    status = datasources_status(tmp_path / ".ohmo", cwd=tmp_path)
    assert status["mysql"]["password"] == "***"
    assert "super-secret" not in json.dumps(status)


def test_runtime_is_none_when_required_fields_missing(tmp_path: Path):
    _write(
        tmp_path / "config" / "datasources.json",
        {"mysql": {"enabled": True, "host": "${MISSING}"}, "redis": {"enabled": True, "host": "${MISSING}"}},
    )
    config = load_datasources_config(tmp_path / ".ohmo", cwd=tmp_path)
    assert create_data_runtime(config, tmp_path) is None


def test_invalid_json_raises(tmp_path: Path):
    path = tmp_path / "config" / "datasources.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")
    try:
        load_datasources_config(tmp_path / ".ohmo", cwd=tmp_path)
    except DataSourceConfigError as exc:
        assert "无法读取" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected DataSourceConfigError")
