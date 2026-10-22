"""``.env`` 敏感值编解码（``ENC[...]``，简单可逆混淆）。

算法与既有项目保持一致：

- 密文形如 ``DB_PASSWORD=ENC[ak4XZaMhnZvnJopQ]``；
- 明文与固定种子 ``MindCube.env.md5.crypto`` 生成的 MD5 密钥流做 XOR，
  再做 urlsafe base64。

这是**确定性可逆编码**：不需要额外密钥文件，服务读取 ``.env`` 时自动还原，
适合防止密码/Token 直接暴露在配置、日志或截图里；它不是带私钥的强加密，
拿到代码的人可以还原。真正需要抵抗攻击者时，请用密钥管理服务注入环境变量。
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping

__all__ = [
    "ENCRYPTED_ENV_KEYS",
    "EnvCryptoError",
    "decrypt_env",
    "decrypt_value",
    "encrypt_value",
    "is_encrypted",
]

#: 需要编解码的变量名（在既有清单基础上补充本项目的密钥变量）。
ENCRYPTED_ENV_KEYS: tuple[str, ...] = (
    "DB_PASSWORD",
    "REDIS_PASSWORD",
    "LLM_TOKEN",
    "DOUBAO_SEARCH_APIKEY",
    "NEO4J_PASSWORD",
    "OPENHARNESS_DASHSCOPE_API_KEY",
    "OPENHARNESS_OPENAI_API_KEY",
    "OPENHARNESS_API_KEY",
    "QAAPP_API_TOKENS",
)

_ENV_CRYPTO_PREFIX = "ENC["
_ENV_CRYPTO_SUFFIX = "]"
_ENV_CRYPTO_SEED = "MindCube.env.md5.crypto"


class EnvCryptoError(RuntimeError):
    """密文格式错误、无法解码。"""


def is_encrypted(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip()
    return text.startswith(_ENV_CRYPTO_PREFIX) and text.endswith(_ENV_CRYPTO_SUFFIX)


def _keystream(length: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    counter = 0
    while total < length:
        chunk = hashlib.md5(f"{_ENV_CRYPTO_SEED}:{counter}".encode("utf-8")).digest()
        chunks.append(chunk)
        total += len(chunk)
        counter += 1
    return b"".join(chunks)[:length]


def _xor_bytes(data: bytes) -> bytes:
    return bytes(item ^ key for item, key in zip(data, _keystream(len(data))))


def encrypt_value(value: str) -> str:
    if is_encrypted(value):
        return value
    encoded = base64.urlsafe_b64encode(_xor_bytes(value.encode("utf-8"))).decode("ascii")
    return f"{_ENV_CRYPTO_PREFIX}{encoded}{_ENV_CRYPTO_SUFFIX}"


def decrypt_value(value: str | None) -> str | None:
    if not is_encrypted(value):
        return value
    assert value is not None
    token = value.strip()[len(_ENV_CRYPTO_PREFIX) : -len(_ENV_CRYPTO_SUFFIX)]
    try:
        encrypted = base64.urlsafe_b64decode(token.encode("ascii"))
        return _xor_bytes(encrypted).decode("utf-8")
    except Exception as exc:
        raise EnvCryptoError("encrypted env value cannot be decoded") from exc


def decrypt_env(values: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``values`` with sensitive ``ENC[...]`` entries decoded."""
    result: dict[str, str] = {}
    for key, value in values.items():
        if key in ENCRYPTED_ENV_KEYS and is_encrypted(value):
            decoded = decrypt_value(value)
            result[key] = decoded if decoded is not None else value
        else:
            result[key] = value
    return result
