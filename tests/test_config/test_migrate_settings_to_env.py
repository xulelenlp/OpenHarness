"""脚本：把旧 settings.json 迁移到 .env。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_settings_to_env.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_settings_to_env", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_settings(path: Path, auth_source: str) -> None:
    path.write_text(
        json.dumps(
            {
                "active_profile": "dev",
                "profiles": {
                    "dev": {
                        "label": "dev",
                        "provider": "openai",
                        "api_format": "openai",
                        "auth_source": auth_source,
                        "default_model": "deepseek-flash",
                        "base_url": "http://gateway.example/v1",
                    }
                },
                "model": "deepseek-flash",
                "provider": "openai",
                "api_format": "openai",
                "base_url": "http://gateway.example/v1",
                "branding": {"product_name": "ABC Tech", "slogan": "企业级AI助手"},
                "max_tokens": 8192,
            }
        ),
        encoding="utf-8",
    )


def test_migrates_and_moves_misplaced_key(tmp_path):
    module = _load_module()
    settings = tmp_path / "settings.json"
    env = tmp_path / ".env"
    _write_settings(settings, auth_source="sk-" + "a" * 40)
    env.write_text("OPENHARNESS_DASHSCOPE_API_KEY=keep-me\nQAAPP_HOST=0.0.0.0\n", encoding="utf-8")

    assert module.main(["--settings", str(settings), "--env", str(env)]) == 0

    text = env.read_text(encoding="utf-8")
    assert "OPENHARNESS_PROVIDER=openai" in text
    assert "OPENHARNESS_MODEL=deepseek-flash" in text
    assert "OPENHARNESS_BASE_URL=http://gateway.example/v1" in text
    # 误填进 auth_source 的密钥被搬到 OPENHARNESS_API_KEY，auth_source 恢复为来源名
    assert "OPENHARNESS_AUTH_SOURCE=openai_api_key" in text
    assert "OPENHARNESS_API_KEY=sk-" + "a" * 40 in text
    assert "OPENHARNESS_MAX_TOKENS=8192" in text
    assert "OPENHARNESS_PRODUCT_NAME=ABC Tech" in text
    # 已有变量保持不变
    assert "OPENHARNESS_DASHSCOPE_API_KEY=keep-me" in text
    assert "QAAPP_HOST=0.0.0.0" in text


def test_is_idempotent_and_dry_run_does_not_write(tmp_path):
    module = _load_module()
    settings = tmp_path / "settings.json"
    env = tmp_path / ".env"
    _write_settings(settings, auth_source="openai_api_key")
    env.write_text("QAAPP_HOST=0.0.0.0\n", encoding="utf-8")

    original = env.read_text(encoding="utf-8")
    assert module.main(["--settings", str(settings), "--env", str(env), "--dry-run"]) == 0
    assert env.read_text(encoding="utf-8") == original

    assert module.main(["--settings", str(settings), "--env", str(env)]) == 0
    migrated = env.read_text(encoding="utf-8")
    assert module.main(["--settings", str(settings), "--env", str(env)]) == 0
    assert env.read_text(encoding="utf-8") == migrated
