"""数据源配置与运行时：MySQL / Redis 的唯一入口。

设计目标（对应改造要求）：

- **集中管理**：连接信息只出现在 ``datasources.json``（可配 ``${ENV}`` 占位符）
  与就近的 ``.env`` / 进程环境变量中；其他模块不读取 ``DB_*`` / ``REDIS_*``，
  也不自行创建 pymysql / redis 连接。
- **单一运行时**：``QaWebService`` 启动时构造一次 :class:`DataRuntime`，把
  ``MySqlClient`` / ``RedisClient`` / ``BusinessCatalog`` / ``BusinessQueryService``
  注入工具，避免配置和连接散落。
- **可降级**：配置缺失或未启用时返回 ``None``，通用问答不受影响；
  ``abcqa datasource check`` 可单独做连通性检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qaapp.config import (
    DATASOURCES_FILENAME,
    config_layers,
    get_datasources_config_path,
    load_config_dict,
    load_dotenv,
)

#: 默认拒绝访问的平台配置/密钥/会话表（原始 SQL 也不允许）。
DEFAULT_DENIED_TABLES = [
    "ai_llm_agent_api",
    "ai_llm_agent_api_bak0617",
    "ai_llm_agent_bk",
    "ai_llm_api",
    "ai_llm_api_local",
    "ai_llm_api_pc",
    "ai_llm_conf",
    "ai_runtime_config",
    "mc_users",
    "mc_ip_whitelist",
    "mc_sessions",
    "mc_messages",
    "ai_api_session",
    "ai_agent_session_online",
]


class DataSourceConfigError(ValueError):
    """数据源配置缺失/非法。"""


class MySQLConfig(BaseModel):
    """MySQL 连接与查询安全配置。"""

    enabled: bool = False
    host: str = ""
    port: int = 3306
    user: str = ""
    password: str = Field(default="", repr=False)
    database: str = ""
    charset: str = "utf8mb4"
    #: 本方案只允许只读；配置成 false 会被校验拒绝。
    read_only: bool = True
    #: 是否启用 TLS；内网 MySQL 不支持 SSL 时保持 False（pymysql 会显式 ssl_disabled）。
    ssl: bool = False
    connect_timeout: int = 5
    read_timeout: int = 30
    max_rows: int = 1000
    max_execution_ms: int = 15_000
    pool_size: int = 4
    allow_raw_sql: bool = False
    denied_tables: list[str] = Field(default_factory=lambda: list(DEFAULT_DENIED_TABLES))
    cache_ttl_seconds: int = 300

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value: Any) -> int:
        if value in (None, ""):
            return 3306
        if not 1 <= int(value) <= 65535:
            raise ValueError("mysql.port must be in 1..65535")
        return int(value)

    @field_validator("pool_size", "max_rows", "max_execution_ms", "connect_timeout", "read_timeout")
    @classmethod
    def _positive(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("mysql numeric limits must be > 0")
        return int(value)


class RedisConfig(BaseModel):
    """Redis 只读缓存配置（强制 db=0）。"""

    enabled: bool = False
    host: str = ""
    port: int = 6379
    password: str = Field(default="", repr=False)
    db: int = 0
    socket_timeout: int = 3
    key_prefixes: list[str] = Field(default_factory=lambda: ["fj_", "daily_"])
    max_scan_keys: int = 2_000

    @field_validator("port", mode="before")
    @classmethod
    def _port(cls, value: Any) -> int:
        if value in (None, ""):
            return 6379
        if not 1 <= int(value) <= 65535:
            raise ValueError("redis.port must be in 1..65535")
        return int(value)

    @field_validator("db")
    @classmethod
    def _db(cls, value: int) -> int:
        if int(value) != 0:
            raise ValueError("redis.db must be 0（db1 含平台密钥，禁止访问）")
        return 0

    @field_validator("key_prefixes")
    @classmethod
    def _prefixes(cls, values: list[str]) -> list[str]:
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            raise ValueError("redis.key_prefixes must not be empty")
        return cleaned


class DataSourcesConfig(BaseModel):
    """``datasources.json`` 根模型。"""

    mysql: MySQLConfig = Field(default_factory=MySQLConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)

    @property
    def enabled(self) -> bool:
        return bool(self.mysql.enabled or self.redis.enabled)


def load_datasources_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> DataSourcesConfig:
    """加载数据源配置；文件缺失返回默认（全 disabled）。"""
    path = get_datasources_config_path(workspace, cwd=cwd, config_dir=config_dir)
    raw, errors, _ = load_config_dict(
        DATASOURCES_FILENAME, workspace, cwd=cwd, config_dir=config_dir
    )
    if errors:
        raise DataSourceConfigError("; ".join(errors))
    if not raw:
        return DataSourcesConfig()
    try:
        return DataSourcesConfig.model_validate(raw)
    except ValueError as exc:
        raise DataSourceConfigError(f"数据源配置非法 {path}: {exc}") from exc


def validate_datasources_config(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> list[str]:
    """返回数据源配置错误列表（空 = 合法/未配置）。"""
    path = get_datasources_config_path(workspace, cwd=cwd, config_dir=config_dir)
    if not path.is_file():
        return []
    try:
        config = load_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
    except DataSourceConfigError as exc:
        return [str(exc)]

    errors: list[str] = []
    if config.mysql.enabled:
        if not config.mysql.host:
            errors.append(f"{path}: mysql.enabled=true 但缺少 host（检查 ${{DB_HOST}} 是否已设置）")
        if not config.mysql.user:
            errors.append(f"{path}: mysql.enabled=true 但缺少 user")
        if not config.mysql.database:
            errors.append(f"{path}: mysql.enabled=true 但缺少 database")
        if not config.mysql.read_only:
            errors.append(f"{path}: mysql.read_only 必须为 true（本方案只允许只读）")
    if config.redis.enabled and not config.redis.host:
        errors.append(f"{path}: redis.enabled=true 但缺少 host（检查 ${{REDIS_HOST}} 是否已设置）")
    return errors


def datasources_status(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, Any]:
    """返回脱敏后的数据源配置状态，供 ``abcqa datasource show`` / ``config`` 使用。"""
    path = get_datasources_config_path(workspace, cwd=cwd, config_dir=config_dir)
    errors = validate_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
    try:
        config = load_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
    except DataSourceConfigError:
        config = DataSourcesConfig()

    project_dir = path
    layers = config_layers(
        DATASOURCES_FILENAME, workspace, cwd=cwd, config_dir=config_dir
    )
    existing_layers = [layer.name for layer in layers if layer.exists]
    if existing_layers:
        source = "+".join(existing_layers)
    elif any(layer.name == "workspace" for layer in layers):
        source = "workspace"
    else:
        source = "defaults"
    return {
        "path": str(project_dir),
        "exists": any(layer.exists for layer in layers),
        "source": source,
        "layers": [
            {"layer": layer.name, "path": str(layer.path), "exists": layer.exists}
            for layer in layers
        ],
        "errors": errors,
        "mysql": {
            "enabled": config.mysql.enabled,
            "host": config.mysql.host,
            "port": config.mysql.port,
            "user": config.mysql.user,
            "database": config.mysql.database,
            "password": "***" if config.mysql.password else "",
            "read_only": config.mysql.read_only,
            "allow_raw_sql": config.mysql.allow_raw_sql,
        },
        "redis": {
            "enabled": config.redis.enabled,
            "host": config.redis.host,
            "port": config.redis.port,
            "db": config.redis.db,
            "password": "***" if config.redis.password else "",
            "key_prefixes": list(config.redis.key_prefixes),
        },
    }


@dataclass
class DataRuntime:
    """一次进程内共享的数据运行时（客户端 + 元数据 + 查询服务）。"""

    config: DataSourcesConfig
    mysql: Any = None
    redis: Any = None
    catalog: Any = None
    query: Any = None
    _closed: bool = field(default=False, init=False)

    @property
    def enabled(self) -> bool:
        return bool(self.mysql is not None or self.redis is not None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in (self.mysql, self.redis):
            close = getattr(client, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception:  # noqa: BLE001, S110 - 关闭失败不影响退出
                pass

    def boundary_summary(self) -> str:
        query = self.query
        if query is None or not hasattr(query, "boundary_summary"):
            return ""
        try:
            return str(query.boundary_summary() or "")
        except Exception:  # noqa: BLE001 - 边界信息失败不影响本轮
            return ""


def create_data_runtime(
    config: DataSourcesConfig | None,
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
) -> DataRuntime | None:
    """按配置构造运行时；未启用时返回 None（通用问答不受影响）。"""
    if config is None or not config.enabled:
        return None

    mysql_client = None
    redis_client = None
    catalog = None
    query_service = None

    mysql_ready = bool(
        config.mysql.enabled
        and str(config.mysql.host or "").strip()
        and str(config.mysql.user or "").strip()
        and str(config.mysql.database or "").strip()
    )
    redis_ready = bool(config.redis.enabled and str(config.redis.host or "").strip())

    if mysql_ready:
        from qaapp.data.catalog import BusinessCatalog
        from qaapp.data.mysql import MySqlClient
        from qaapp.data.query import BusinessQueryService

        mysql_client = MySqlClient(config.mysql)
        catalog = BusinessCatalog(mysql_client, config.mysql)
        query_service = BusinessQueryService(
            mysql_client,
            catalog,
            config.mysql,
            redis=None,
        )
    if redis_ready:
        from qaapp.data.redis import RedisClient

        redis_client = RedisClient(config.redis)
        if query_service is not None:
            query_service.redis = redis_client

    if mysql_client is None and redis_client is None:
        return None
    return DataRuntime(
        config=config,
        mysql=mysql_client,
        redis=redis_client,
        catalog=catalog,
        query=query_service,
    )


__all__ = [
    "DATASOURCES_FILENAME",
    "DEFAULT_DENIED_TABLES",
    "DataRuntime",
    "DataSourceConfigError",
    "DataSourcesConfig",
    "MySQLConfig",
    "RedisConfig",
    "create_data_runtime",
    "datasources_status",
    "load_datasources_config",
    "load_dotenv",
    "validate_datasources_config",
]
