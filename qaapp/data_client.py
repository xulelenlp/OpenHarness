"""MySQL 只读客户端与 SQL 安全守卫。

- 连接池 + ``asyncio.to_thread`` 调用方（本模块保持同步，避免在事件循环里阻塞）；
- 只读会话、执行超时、行数上限；
- 原始 SQL 的默认拒绝策略。
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from qaapp.datasources import MySQLConfig

#: 允许的原始 SQL 首关键字。
_RAW_SQL_PREFIXES = ("select", "show", "describe", "desc", "explain")

#: 写操作/危险关键字（按单词边界匹配，避免误伤字段名）。
_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "replace",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "call",
    "lock",
    "unlock",
    "set",
    "use",
    "into outfile",
    "into dumpfile",
    "load_file",
    "load data",
    "sleep",
    "benchmark",
    "get_lock",
    "release_lock",
    "for update",
    "lock in share mode",
)

_FORBIDDEN_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(word).replace(r"\ ", r"\s+") for word in _FORBIDDEN_KEYWORDS) + r")\b",
    flags=re.IGNORECASE,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class DataClientError(RuntimeError):
    """数据库连接/执行失败。"""


class SqlSafetyError(ValueError):
    """原始 SQL 未通过安全校验。"""


@dataclass
class QueryResult:
    """一次查询的结果（rows 为 dict 列表）。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: int = 0
    sql: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)


class SqlGuard:
    """原始 SQL 白名单/黑名单守卫。"""

    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._denied = {name.lower() for name in config.denied_tables}

    def check(self, sql: str) -> str:
        text = str(sql or "").strip()
        if not text:
            raise SqlSafetyError("SQL 为空")
        if not self._config.allow_raw_sql:
            raise SqlSafetyError(
                "原始 SQL 未启用（datasources.json: mysql.allow_raw_sql=false）；"
                "请使用 query/explore/describe 等结构化操作。"
            )
        # 禁止多语句与注释（注释可用来绕过关键字检查）。
        if ";" in text:
            raise SqlSafetyError("原始 SQL 不允许分号/多语句")
        if "--" in text or "/*" in text or "*/" in text or text.lstrip().startswith("#"):
            raise SqlSafetyError("原始 SQL 不允许注释")
        first = text.split(None, 1)[0].lower()
        if first not in _RAW_SQL_PREFIXES:
            raise SqlSafetyError(f"只允许 SELECT/SHOW/DESCRIBE/EXPLAIN，收到: {first}")
        if first in {"select", "explain"}:
            if _FORBIDDEN_PATTERN.search(text):
                raise SqlSafetyError("原始 SQL 命中写操作/危险关键字")
            lowered = text.lower()
            for table in self._denied:
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])", lowered):
                    raise SqlSafetyError(f"原始 SQL 不允许访问敏感表: {table}")
            for schema in ("information_schema", "mysql.", "sys."):
                if schema in lowered:
                    raise SqlSafetyError(f"原始 SQL 不允许访问系统库: {schema}")
        return text

    def with_limit(self, sql: str) -> str:
        """给没有 LIMIT 的 SELECT 追加行数上限。"""
        text = sql.strip()
        if not text.lower().startswith("select"):
            return text
        if re.search(r"\blimit\s+\d+", text, flags=re.IGNORECASE):
            return text
        return f"{text}\nLIMIT {int(self._config.max_rows)}"


class MySqlClient:
    """同步 MySQL 客户端；调用方用 ``asyncio.to_thread`` 包装。"""

    def __init__(self, config: MySQLConfig) -> None:
        self.config = config
        self._pool: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=max(1, config.pool_size))
        self._pool_lock = threading.Lock()
        self._created = 0
        self._closed = False
        self._table_cache: dict[str, bool] = {}
        self.guard = SqlGuard(config)

    # -- 连接池 ---------------------------------------------------------

    def _connect(self) -> Any:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:  # pragma: no cover - 部署依赖缺失
            raise DataClientError(
                "缺少 pymysql 依赖；请执行 pip install -e . 或 pip install pymysql"
            ) from exc
        try:
            conn = pymysql.connect(
                host=self.config.host,
                port=int(self.config.port),
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset or "utf8mb4",
                connect_timeout=int(self.config.connect_timeout),
                read_timeout=int(self.config.read_timeout),
                write_timeout=int(self.config.read_timeout),
                autocommit=True,
                cursorclass=DictCursor,
                ssl_disabled=None if self.config.ssl else True,
            )
        except Exception as exc:  # pymysql 异常类型较杂，统一转成可读错误
            raise DataClientError(f"MySQL 连接失败: {exc}") from exc
        self._prepare_session(conn)
        return conn

    def _prepare_session(self, conn: Any) -> None:
        """只读 + 执行超时；不同版本不支持时静默降级。"""
        for statement in (
            "SET SESSION TRANSACTION READ ONLY",
            f"SET SESSION MAX_EXECUTION_TIME = {int(self.config.max_execution_ms)}",
        ):
            try:
                with conn.cursor() as cur:
                    cur.execute(statement)
            except Exception:  # noqa: BLE001, S112
                continue

    def _acquire(self) -> Any:
        if self._closed:
            raise DataClientError("MySQL 客户端已关闭")
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._pool_lock:
                if self._created < max(1, self.config.pool_size):
                    conn = self._connect()
                    self._created += 1
                else:
                    conn = None
            if conn is None:
                try:
                    conn = self._pool.get(timeout=int(self.config.connect_timeout) + 5)
                except queue.Empty as exc:
                    raise DataClientError("MySQL 连接池等待超时") from exc
        try:
            conn.ping()
        except Exception:  # noqa: BLE001 - 坏连接就地替换
            # 坏连接就地替换，不改变池内连接数。
            self._safe_close(conn)
            conn = self._connect()
        return conn

    def _release(self, conn: Any) -> None:
        if self._closed:
            self._safe_close(conn)
            return
        try:
            self._pool.put_nowait(conn)
        except queue.Full:
            self._safe_close(conn)
            with self._pool_lock:
                self._created = max(0, self._created - 1)

    def _discard(self, conn: Any) -> None:
        self._safe_close(conn)
        with self._pool_lock:
            self._created = max(0, self._created - 1)

    @staticmethod
    def _safe_close(conn: Any) -> None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                conn = self._pool.get_nowait()
            except queue.Empty:
                break
            self._safe_close(conn)
        with self._pool_lock:
            self._created = 0

    # -- 查询 -----------------------------------------------------------

    def fetch_all(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
        *,
        max_rows: int | None = None,
    ) -> QueryResult:
        limit = int(max_rows or self.config.max_rows)
        started = time.monotonic()
        conn = self._acquire()
        healthy = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params or ()))
                rows = list(cur.fetchmany(limit + 1))
                truncated = len(rows) > limit
                rows = rows[:limit]
                columns = [item[0] for item in (cur.description or [])]
        except Exception as exc:
            healthy = False
            self._discard(conn)
            raise DataClientError(f"SQL 执行失败: {exc}") from exc
        finally:
            if healthy:
                self._release(conn)
        elapsed = int((time.monotonic() - started) * 1000)
        return QueryResult(
            rows=[dict(row) for row in rows],
            columns=columns,
            truncated=truncated,
            elapsed_ms=elapsed,
            sql=sql,
        )

    def fetch_one(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
    ) -> dict[str, Any] | None:
        result = self.fetch_all(sql, params, max_rows=1)
        return result.rows[0] if result.rows else None

    def execute_raw(self, sql: str) -> QueryResult:
        """受控原始 SQL：先过 guard，再执行。"""
        safe = self.guard.with_limit(self.guard.check(sql))
        return self.fetch_all(safe, None)

    # -- 元数据 ---------------------------------------------------------

    def table_exists(self, table: str) -> bool:
        if not _IDENTIFIER.match(str(table or "")):
            return False
        cached = self._table_cache.get(table)
        if cached is not None:
            return cached
        row = self.fetch_one(
            "SELECT 1 AS ok FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1",
            (table,),
        )
        exists = bool(row)
        self._table_cache[table] = exists
        return exists

    def invalidate_table_cache(self) -> None:
        self._table_cache.clear()


__all__ = [
    "DataClientError",
    "MySqlClient",
    "QueryResult",
    "SqlGuard",
    "SqlSafetyError",
]
