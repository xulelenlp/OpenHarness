"""`.env` 的 ``ENC[...]`` 编解码与服务透明解码。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from openharness.config.env_crypt import (
    ENCRYPTED_ENV_KEYS,
    EnvCryptoError,
    decrypt_env,
    decrypt_value,
    encrypt_value,
    is_encrypted,
)

TOOL = Path(__file__).resolve().parents[2] / "scripts" / "env_crypt.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("env_crypt_tool", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_roundtrip_is_deterministic_and_reencodable():
    token = encrypt_value("s3cret-password")
    assert is_encrypted(token)
    # 确定性 XOR：解码后重新编码得到完全相同的 token（兼容既有 ENC[...] 数据）
    assert encrypt_value(decrypt_value(token)) == token
    assert token.startswith("ENC[") and token.endswith("]")
    assert "s3cret-password" not in token
    assert decrypt_value(token) == "s3cret-password"
    # 确定性：同值同密文
    assert encrypt_value("s3cret-password") == token


def test_invalid_token_raises():
    with pytest.raises(EnvCryptoError):
        decrypt_value("ENC[!!!not-base64!!!]")


def test_decrypt_env_only_decodes_configured_keys():
    token = encrypt_value("s3cret")
    values = {
        "DB_PASSWORD": token,
        "QAAPP_HOST": "0.0.0.0",
        "SOME_OTHER": "ENC[bm90LWRlY29kZWQ=]",
    }
    decoded = decrypt_env(values)
    assert decoded["DB_PASSWORD"] == "s3cret"
    assert decoded["QAAPP_HOST"] == "0.0.0.0"
    # 不在名单里的键保持原样
    assert decoded["SOME_OTHER"] == values["SOME_OTHER"]
    assert decoded["DB_PASSWORD"] != token


def test_qaapp_load_dotenv_decodes(tmp_path):
    from qaapp.config.env import load_dotenv

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text(
        f"DB_PASSWORD={encrypt_value('db-pass')}\n"
        f"REDIS_PASSWORD={encrypt_value('redis-pass')}\n"
        "QAAPP_PORT=8787\n",
        encoding="utf-8",
    )

    values = load_dotenv(workspace=workspace, cwd=str(tmp_path))

    assert values["DB_PASSWORD"] == "db-pass"
    assert values["REDIS_PASSWORD"] == "redis-pass"
    assert values["QAAPP_PORT"] == "8787"


def test_apply_project_env_decodes(tmp_path, monkeypatch):
    from openharness.config.settings import apply_project_env

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text(
        f"DB_PASSWORD={encrypt_value('s3cret')}\nOPENHARNESS_MODEL=m\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))

    apply_project_env()

    assert os.environ["DB_PASSWORD"] == "s3cret"
    assert os.environ["OPENHARNESS_MODEL"] == "m"


def test_tool_encrypts_only_listed_non_empty_keys(tmp_path):
    tool = _load_tool()
    env = tmp_path / ".env"
    env.write_text(
        "OPENHARNESS_API_KEY=\n"
        "OPENHARNESS_DASHSCOPE_API_KEY=sk-abc123\n"
        "DB_PASSWORD=At@202311\n"
        "OPENHARNESS_MAX_TOKENS=16384\n"
        "QAAPP_HOST=0.0.0.0\n",
        encoding="utf-8",
    )

    assert tool.main(["encrypt", str(env)]) == 0
    text = env.read_text(encoding="utf-8")
    # 空值不能被编码，也不能污染下一行（历史实现会写出 ENC[] 前缀）
    assert "OPENHARNESS_API_KEY=\n" in text
    assert "ENC[]OPENHARNESS" not in text
    assert f"OPENHARNESS_DASHSCOPE_API_KEY={encrypt_value('sk-abc123')}" in text
    assert f"DB_PASSWORD={encrypt_value('At@202311')}" in text
    # 不在名单/非敏感项保持不变
    assert "OPENHARNESS_MAX_TOKENS=16384" in text
    assert "QAAPP_HOST=0.0.0.0" in text
    # 幂等
    assert tool.main(["encrypt", str(env)]) == 0
    assert env.read_text(encoding="utf-8") == text

    assert tool.main(["decrypt", str(env)]) == 0
    plain = env.read_text(encoding="utf-8")
    assert "DB_PASSWORD=At@202311" in plain
    assert "OPENHARNESS_DASHSCOPE_API_KEY=sk-abc123" in plain


def test_tool_dry_run_does_not_write(tmp_path):
    tool = _load_tool()
    env = tmp_path / ".env"
    env.write_text("DB_PASSWORD=plain\n", encoding="utf-8")
    original = env.read_text(encoding="utf-8")
    assert tool.main(["encrypt", "--dry-run", str(env)]) == 0
    assert env.read_text(encoding="utf-8") == original


def test_encrypted_keys_cover_project_secrets():
    for name in (
        "DB_PASSWORD",
        "REDIS_PASSWORD",
        "OPENHARNESS_DASHSCOPE_API_KEY",
        "OPENHARNESS_API_KEY",
        "QAAPP_API_TOKENS",
    ):
        assert name in ENCRYPTED_ENV_KEYS
