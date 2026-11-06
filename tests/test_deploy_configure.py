"""deploy/configure.py 的单元测试（不依赖真实 .env / 数据库）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "deploy_configure", ROOT / "deploy" / "configure.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure = _load_module()


def _write_config(tmp_path: Path, *, redis_host: str = "127.0.0.1") -> Path:
    config_dir = tmp_path / "backend"
    config_dir.mkdir()
    redis_line = f"REDIS_HOST={redis_host}\n" if redis_host else ""
    (config_dir / ".env").write_text(
        "\n".join(
            [
                "# 注释保留",
                "OPENHARNESS_PROVIDER=openai",
                "OPENHARNESS_API_FORMAT=openai",
                "OPENHARNESS_MODEL=test-model",
                "OPENHARNESS_BASE_URL=http://example.test/v1",
                "OPENHARNESS_AUTH_SOURCE=openai_api_key",
                "OPENHARNESS_OPENAI_API_KEY=sk-test",
                "DB_HOST=127.0.0.1",
                "DB_PORT=3306",
                "DB_USER=readonly",
                "DB_PASSWORD=old",
                "DB_NAME=testdb",
                redis_line.rstrip("\n"),
                "REDIS_PORT=6379",
                "QAAPP_HOST=0.0.0.0",
                "QAAPP_PORT=8787",
                "QAAPP_STATIC_DIR=/app/frontend/webapp/dist",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "datasources.json").write_text(
        json.dumps(
            {"mysql": {"enabled": True}, "redis": {"enabled": True}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_dir


def test_set_env_value_keeps_comments_and_order():
    text = "# header\nA=1\nB=2\n"
    result = configure.set_env_value(text, "A", "9")
    assert result == "# header\nA=9\nB=2\n"


def test_set_env_value_drops_duplicates():
    text = "A=1\n# keep\nA=2\nB=3\n"
    result = configure.set_env_value(text, "A", "9")
    assert result == "A=9\n# keep\nB=3\n"


def test_set_env_value_appends_missing_key():
    result = configure.set_env_value("A=1\n", "NEW_KEY", "v")
    assert result.endswith("NEW_KEY=v\n")
    assert "A=1" in result


@pytest.mark.parametrize("value", ["line\nbreak", "  padded", '"quoted"'])
def test_set_env_value_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        configure.set_env_value("A=1\n", "A", value)


def test_set_env_value_rejects_invalid_key():
    with pytest.raises(ValueError):
        configure.set_env_value("A=1\n", "BAD KEY", "v")


def test_parse_env_matches_runtime_rules():
    values = configure.parse_env("export A=1\nB='x'\n# C=2\nD=has=equals\n")
    assert values == {"A": "1", "B": "x", "D": "has=equals"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "*"),
        ("*", "*"),
        ("http://10.0.0.1:8787/path", "http://10.0.0.1:8787"),
        ("10.0.0.1:8787", "http://10.0.0.1:8787"),
        ("https://qa.example.com/", "https://qa.example.com"),
    ],
)
def test_normalize_origin(raw, expected):
    assert configure.normalize_origin(raw) == expected


def test_validate_values_reports_missing_db_host():
    values = configure.parse_env(
        "OPENHARNESS_PROVIDER=openai\n"
        "OPENHARNESS_API_FORMAT=openai\n"
        "OPENHARNESS_MODEL=m\n"
        "OPENHARNESS_BASE_URL=http://x/v1\n"
        "OPENHARNESS_AUTH_SOURCE=openai_api_key\n"
        "OPENHARNESS_OPENAI_API_KEY=k\n"
        "DB_PORT=3306\nDB_USER=u\nDB_NAME=d\n"
        "REDIS_HOST=r\nREDIS_PORT=6379\n"
    )
    errors, _ = configure.validate_values(values, redis_enabled=True)
    assert any("DB_HOST" in error for error in errors)


def test_validate_values_skips_redis_when_disabled():
    values = configure.parse_env(
        "OPENHARNESS_PROVIDER=openai\n"
        "OPENHARNESS_API_FORMAT=openai\n"
        "OPENHARNESS_MODEL=m\n"
        "OPENHARNESS_BASE_URL=http://x/v1\n"
        "OPENHARNESS_AUTH_SOURCE=openai_api_key\n"
        "OPENHARNESS_OPENAI_API_KEY=k\n"
        "DB_HOST=db\nDB_PORT=3306\nDB_USER=u\nDB_NAME=d\n"
    )
    errors, _ = configure.validate_values(values, redis_enabled=False)
    assert errors == []


def test_update_datasources_writes_flag_and_backup(tmp_path):
    path = tmp_path / "datasources.json"
    path.write_text('{"mysql": {"enabled": true}, "redis": {"enabled": true}}\n', encoding="utf-8")
    changes = configure.update_datasources(path, redis_enabled=False, dry_run=False)
    assert changes
    assert json.loads(path.read_text(encoding="utf-8"))["redis"]["enabled"] is False
    assert list(tmp_path.glob("datasources.json.bak-*"))


def test_main_updates_env_and_is_atomic(tmp_path):
    config_dir = _write_config(tmp_path)
    answers = tmp_path / "answers.json"
    answers.write_text(
        json.dumps(
            {
                "db_host": "10.1.2.3",
                "db_password": "p@ss#word",
                "redis_host": "10.1.2.4",
                "public_url": "http://10.1.2.5:8787/",
                "trust_proxy": False,
            }
        ),
        encoding="utf-8",
    )
    code = configure.main(
        ["--config-dir", str(config_dir), "--answers-file", str(answers)]
    )
    assert code == 0
    values = configure.parse_env((config_dir / ".env").read_text(encoding="utf-8"))
    assert values["DB_HOST"] == "10.1.2.3"
    assert values["DB_PASSWORD"] == "p@ss#word"
    assert values["REDIS_HOST"] == "10.1.2.4"
    assert values["QAAPP_CORS_ORIGINS"] == "http://10.1.2.5:8787"
    assert values["QAAPP_AUTH_TRUST_PROXY"] == "false"
    assert (config_dir / ".env").stat().st_mode & 0o777 == 0o600


def test_main_dry_run_does_not_modify(tmp_path):
    config_dir = _write_config(tmp_path)
    before = (config_dir / ".env").read_text(encoding="utf-8")
    code = configure.main(
        ["--config-dir", str(config_dir), "--db-host", "10.9.9.9", "--dry-run"]
    )
    assert code == 0
    assert (config_dir / ".env").read_text(encoding="utf-8") == before


def test_main_check_fails_on_missing_required_key(tmp_path):
    config_dir = _write_config(tmp_path)
    env_path = config_dir / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace("DB_HOST=127.0.0.1\n", "DB_HOST=\n"),
        encoding="utf-8",
    )
    code = configure.main(["--config-dir", str(config_dir), "--check"])
    assert code == 2
