"""指标取数：表路由、SQL 执行与最新日期探测。"""

from __future__ import annotations

from datetime import date
from typing import Any

from qaapp.data.catalog import Dimension, Indicator, Org
from qaapp.data.mysql import DataClientError
from qaapp.data.schema import (
    DATA_COLUMNS,
    FOUR_RATE_DIM_DISTRIBUTION,
    FOUR_RATE_DIM_MAIN,
    T_DIM,
    T_FOUR_RATE,
    T_WEATHER,
)


class FetcherMixin:
    """Fetch indicator rows from candidate tables."""

    def _fetch_indicator(
        self,
        indicator: Indicator,
        org: Org,
        period: str,
        start: date | None,
        end: date | None,
        dims: list[Dimension],
        limit: int,
    ) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], list[str]]:
        rows: list[dict[str, Any]] = []
        sqls: list[dict[str, Any]] = []
        notes: list[str] = []
        tried: list[str] = []
        used_table = ""
        tables = self._candidate_tables(indicator, org, period, dims)
        if not tables:
            return [], "", [], ["no_table_found"]
        for table in tables[:5]:
            tried.append(table)
            query_rows, sql = self._run_table_query(
                indicator, org, period, start, end, dims, limit, table
            )
            if sql:
                sqls.append(sql)
                if sql.get("error"):
                    notes.append("query_failed")
            if query_rows:
                rows = query_rows
                used_table = table
                break
        if not rows and dims:
            # 维度无数据 → 回退总量
            for table in tables[:3]:
                query_rows, sql = self._run_table_query(
                    indicator, org, period, start, end, [], limit, table
                )
                if sql:
                    sqls.append(sql)
                    if sql.get("error"):
                        notes.append("query_failed")
                if query_rows:
                    rows = query_rows
                    used_table = table
                    notes.append("dim_fallback")
                    break
        if not rows:
            # 总量也没有 → 放宽到任意维度，至少给出明细
            for table in tables[:3]:
                query_rows, sql = self._run_table_query(
                    indicator, org, period, start, end, [], limit, table, ignore_dim=True
                )
                if sql:
                    sqls.append(sql)
                    if sql.get("error"):
                        notes.append("query_failed")
                if query_rows:
                    rows = query_rows
                    used_table = table
                    notes.append("no_total_dim")
                    break
        return rows, used_table, sqls, notes

    def _run_table_query(
        self,
        indicator: Indicator,
        org: Org,
        period: str,
        start: date | None,
        end: date | None,
        dims: list[Dimension],
        limit: int,
        table: str,
        *,
        ignore_dim: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if table == T_WEATHER:
            return self._query_weather(indicator, org, period, start, end, limit)
        if table == T_FOUR_RATE:
            return self._query_four_rate(indicator, org, period, start, end, dims, limit)

        columns = ", ".join(f"a.{column}" for column in DATA_COLUMNS)
        params: list[Any] = [indicator.index_id, org.org_code, period]
        where = ["a.index_id = %s", "a.org_id = %s", "a.period = %s"]
        if start and end:
            where.append("a.stat_date BETWEEN %s AND %s")
            params.extend([start.isoformat(), end.isoformat()])
        if dims and not ignore_dim:
            wd_ids = [str(dim.wd_id) for dim in dims if dim.wd_id]
            if wd_ids:
                placeholders = ", ".join(["%s"] * len(wd_ids))
                where.append(f"a.dim_val IN ({placeholders})")
                params.extend(wd_ids)
                sql = (
                    f"SELECT {columns}, a.dim_val, b.wd_name AS dim_name "
                    f"FROM {table} a LEFT JOIN {T_DIM} b ON b.wd_id = a.dim_val "
                    f"WHERE {' AND '.join(where)} ORDER BY a.stat_date, a.org_id LIMIT %s"
                )
                params.append(limit)
                return self._execute(sql, params)
        if not ignore_dim:
            where.append("(a.dim_val IS NULL OR a.dim_val = '')")
            sql = (
                f"SELECT {columns} FROM {table} a "
                f"WHERE {' AND '.join(where)} ORDER BY a.stat_date, a.org_id LIMIT %s"
            )
        else:
            sql = (
                f"SELECT {columns}, b.wd_name AS dim_name FROM {table} a "
                f"LEFT JOIN {T_DIM} b ON b.wd_id = a.dim_val "
                f"WHERE {' AND '.join(where)} ORDER BY a.stat_date, a.org_id LIMIT %s"
            )
        params.append(limit)
        return self._execute(sql, params)

    def _execute(
        self, sql: str, params: list[Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        try:
            result = self.client.fetch_all(sql, params)
        except DataClientError:
            return [], {"sql": sql, "params": len(params), "error": "query_failed"}
        return result.rows, {
            "sql": sql,
            "params": len(params),
            "elapsed_ms": result.elapsed_ms,
            "row_count": result.row_count,
            "truncated": result.truncated,
        }

    def _query_weather(
        self,
        indicator: Indicator,
        org: Org,
        period: str,
        start: date | None,
        end: date | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        name = indicator.name
        if "最高" in name:
            expr = "dim_value_b"
        elif "最低" in name:
            expr = "dim_value_c"
        else:
            expr = "(dim_value_b + dim_value_c) / 2"
        sql = (
            f"SELECT stat_date, period, org_id, name, {expr} AS val, "
            f"'℃' AS unit, dim_value_a AS weather, dim_value_b AS max_temp, "
            f"dim_value_c AS min_temp FROM {T_WEATHER} "
            "WHERE org_id = %s AND period = %s"
        )
        params: list[Any] = [org.org_code, period]
        if start and end:
            sql += " AND stat_date BETWEEN %s AND %s"
            params.extend([start.isoformat(), end.isoformat()])
        sql += " ORDER BY stat_date LIMIT %s"
        params.append(limit)
        return self._execute(sql, params)

    def _query_four_rate(
        self,
        indicator: Indicator,
        org: Org,
        period: str,
        start: date | None,
        end: date | None,
        dims: list[Dimension],
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        dim_values = [FOUR_RATE_DIM_MAIN, FOUR_RATE_DIM_DISTRIBUTION]
        if dims:
            dim_values = []
            for dim in dims:
                dim_values.append(
                    FOUR_RATE_DIM_DISTRIBUTION if "配网" in dim.name else FOUR_RATE_DIM_MAIN
                )
            dim_values = list(dict.fromkeys(dim_values))
        placeholders = ", ".join(["%s"] * len(dim_values))
        sql = (
            f"SELECT {', '.join(DATA_COLUMNS)} FROM {T_FOUR_RATE} "
            f"WHERE index_id = %s AND org_id = %s AND period = %s AND dim_val IN ({placeholders})"
        )
        params: list[Any] = [indicator.index_id, org.org_code, period, *dim_values]
        if start and end:
            sql += " AND stat_date BETWEEN %s AND %s"
            params.extend([start.isoformat(), end.isoformat()])
        sql += " ORDER BY stat_date LIMIT %s"
        params.append(limit)
        return self._execute(sql, params)

    def _latest_available(self, indicator: Indicator, org: Org | None, period: str | None = None) -> str:
        if org is None:
            return ""
        latest = ""
        for table in self._candidate_tables(indicator, org, period or "月", []):
            try:
                sql = f"SELECT MAX(stat_date) AS latest FROM {table} WHERE index_id = %s AND org_id = %s"
                params: list[Any] = [indicator.index_id, org.org_code]
                if period:
                    sql += " AND period = %s"
                    params.append(period)
                result = self.client.fetch_all(sql, params, max_rows=1)
                value = result.rows[0].get("latest") if result.rows else None
                if value:
                    return str(value)
            except DataClientError:
                continue
        return latest
