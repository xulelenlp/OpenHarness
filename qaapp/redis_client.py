"""Redis 只读缓存客户端。

安全边界（重要）：
- 只允许 ``db=0``；配置成 db1 会在 :class:`~qaapp.datasources.RedisConfig`
  校验阶段被拒绝（db1 含平台配置/密钥）。
- 只读：本模块不提供 set/delete/expire 等写方法。
- 只扫描 ``key_prefixes`` 白名单内的键，禁止 ``KEYS``。
"""

from __future__ import annotations

import json
from typing import Any

from qaapp.datasources import RedisConfig


class RedisClientError(RuntimeError):
    """Redis 连接/读取失败。"""


class RedisClient:
    """同步 Redis 客户端；调用方用 ``asyncio.to_thread`` 包装。"""

    def __init__(self, config: RedisConfig) -> None:
        self.config = config
        self._client: Any = None
        self._closed = False

    # -- 连接 -----------------------------------------------------------

    def _connect(self) -> Any:
        if self._closed:
            raise RedisClientError("Redis 客户端已关闭")
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - 部署依赖缺失
            raise RedisClientError(
                "缺少 redis 依赖；请执行 pip install -e . 或 pip install redis"
            ) from exc
        try:
            client = redis.Redis(
                host=self.config.host,
                port=int(self.config.port),
                password=self.config.password or None,
                db=0,
                socket_connect_timeout=int(self.config.socket_timeout),
                socket_timeout=int(self.config.socket_timeout),
                decode_responses=True,
            )
            client.ping()
        except Exception as exc:
            raise RedisClientError(f"Redis 连接失败: {exc}") from exc
        self._client = client
        return client

    def ping(self) -> bool:
        try:
            return bool(self._connect().ping())
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(f"Redis PING 失败: {exc}") from exc

    def dbsize(self) -> int:
        try:
            return int(self._connect().dbsize())
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(f"Redis DBSIZE 失败: {exc}") from exc

    def close(self) -> None:
        self._closed = True
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001, S110
                pass

    # -- 只读访问 -------------------------------------------------------

    def _check_key(self, key: str) -> str:
        text = str(key or "").strip()
        if not text:
            raise RedisClientError("Redis key 为空")
        if not any(text.startswith(prefix) for prefix in self.config.key_prefixes):
            allowed = "、".join(self.config.key_prefixes)
            raise RedisClientError(f"Redis key 不在白名单前缀内（允许：{allowed}）: {text}")
        return text

    def scan_keys(self, prefix: str = "fj_", limit: int | None = None) -> list[str]:
        """按前缀扫描（内部用 SCAN，禁止 KEYS）。"""
        text = str(prefix or "").strip()
        if not any(text.startswith(item) for item in self.config.key_prefixes):
            allowed = "、".join(self.config.key_prefixes)
            raise RedisClientError(f"Redis 扫描前缀不在白名单内（允许：{allowed}）: {text}")
        cap = int(limit or self.config.max_scan_keys)
        client = self._connect()
        found: list[str] = []
        try:
            for key in client.scan_iter(match=f"{text}*", count=1000):
                found.append(str(key))
                if len(found) >= cap:
                    break
        except Exception as exc:
            raise RedisClientError(f"Redis SCAN 失败: {exc}") from exc
        return found

    def get_text(self, key: str) -> str | None:
        safe = self._check_key(key)
        try:
            value = self._connect().get(safe)
        except RedisClientError:
            raise
        except Exception as exc:
            raise RedisClientError(f"Redis GET 失败: {exc}") from exc
        return str(value) if value is not None else None

    def get_json(self, key: str) -> Any:
        text = self.get_text(key)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def forecast_feature(self, date: str, area: str = "fj") -> dict[str, Any] | None:
        """按日期读取预测/特征缓存（键名格式不完全统一，做启发式匹配）。

        实测键形如 ``fj_daily_electricity_generation_2023-05-16``、
        ``fj_daily_sm_electricity_generation_2023-07-05``。这里只做只读探测，
        找不到就返回 ``None``，不影响主流程。
        """
        day = str(date or "").strip()
        if not day:
            return None
        candidates: list[str] = []
        prefixes = (
            (f"fj_{area}_",)
            if area and area != "fj"
            else ("fj_daily_", "fj_electricity_", "fj_load_", "fj_")
        )
        for prefix in prefixes:
            try:
                candidates.extend(self.scan_keys(prefix, limit=self.config.max_scan_keys))
            except RedisClientError:
                continue
            if candidates:
                break
        for key in candidates:
            if day in key and ("daily" in key or "day" in key) and area in key:
                value = self.get_json(key)
                if isinstance(value, (dict, list, int, float, str)):
                    return {"key": key, "value": value}
        return None


__all__ = ["RedisClient", "RedisClientError"]
