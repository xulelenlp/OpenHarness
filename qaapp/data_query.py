"""业务查询服务：解析 → 路由 → 查询 → 质量诊断 → 整形。

本模块只依赖 :class:`~qaapp.data_client.MySqlClient` 与
:class:`~qaapp.data_catalog.BusinessCatalog`，不读取环境变量、不创建连接。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from qaapp.analysis_scenarios import AnalysisScenario, get_scenario, match_scenario
from qaapp.data_catalog import BusinessCatalog, Dimension, Indicator, Org, normalize_text
from qaapp.data_client import DataClientError, MySqlClient
from qaapp.data_quality import QualityIssue, check_quality, summarize_issues
from qaapp.data_schema import (
    DATA_COLUMNS,
    FOUR_RATE_DIM_DISTRIBUTION,
    FOUR_RATE_DIM_MAIN,
    FOUR_RATE_NAMES,
    LEVEL_TABLE_BY_ORG_TYPE,
    LEVEL_TABLES,
    T_DIM,
    T_EVENTS,
    T_FOUR_RATE,
    T_NEWS,
    T_WEATHER,
    WEATHER_KEYWORDS,
)
from qaapp.datasources import MySQLConfig

_PERIOD_ORDER = ("月", "年", "季", "周", "日")


class QueryError(ValueError):
    """查询参数/解析错误。"""


@dataclass
class ResolvedRequest:
    indicators: list[Indicator] = field(default_factory=list)
    orgs: list[Org] = field(default_factory=list)
    dimensions: dict[str, list[Dimension]] = field(default_factory=dict)
    period: str = "月"
    start: date | None = None
    end: date | None = None
    fallbacks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    needs_confirmation: bool = False


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%Y/%m"):
        try:
            parsed = datetime.strptime(text, fmt)  # noqa: DTZ007 - 业务日期无时区
            return parsed.date()
        except ValueError:
            continue
    return None


def _month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _today() -> date:
    """统一用业务时区（Asia/Shanghai）的“今天”。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _previous_month_end(today: date | None = None) -> date:
    today = today or _today()
    first = date(today.year, today.month, 1)
    return first - timedelta(days=1)


def _quarter_start(value: date) -> date:
    month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, month, 1)


def _quarter_end(value: date) -> date:
    start = _quarter_start(value)
    month = start.month + 3
    year = start.year + (1 if month > 12 else 0)
    month = month - 12 if month > 12 else month
    return date(year, month, 1) - timedelta(days=1)


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _week_end(value: date) -> date:
    return _week_start(value) + timedelta(days=6)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _rows_jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _periods_from_indicator(indicator: Indicator) -> list[str]:
    periods = [item for item in indicator.periods if item in _PERIOD_ORDER]
    return periods or list(_PERIOD_ORDER)


def _is_weather(indicator: Indicator) -> bool:
    return any(word in indicator.name for word in WEATHER_KEYWORDS)


def _is_four_rate(indicator: Indicator) -> bool:
    return any(word in indicator.name for word in FOUR_RATE_NAMES)


def _is_total_dim_fallback(row: dict[str, Any]) -> bool:
    dim = str(row.get("dim_val") or "").strip()
    return dim in {"", "NULL", "null", "None"}


class BusinessQueryService:
    """面向工具层的高层查询服务（同步实现，异步包装在工具中调用）。"""

    def __init__(
        self,
        client: MySqlClient,
        catalog: BusinessCatalog,
        config: MySQLConfig,
        redis: Any = None,
    ) -> None:
        self.client = client
        self.catalog = catalog
        self.config = config
        self.redis = redis

    # -- 入口 -----------------------------------------------------------

    def resolve(
        self,
        *,
        indicators: list[str] | None = None,
        index_ids: list[str] | None = None,
        org: str = "福建",
        dimensions: list[str] | None = None,
        period: str = "月",
    ) -> dict[str, Any]:
        try:
            resolved = self._resolve_request(
                indicators=indicators or [],
                index_ids=index_ids or [],
                org=org,
                dimensions=dimensions or [],
                period=period,
                start=None,
                end=None,
                allow_multi_org=False,
            )
        except QueryError as exc:
            return {"status": "error", "summary": str(exc), "candidates": []}
        return {
            "status": "ok" if resolved.indicators else "no_data",
            "summary": self._resolved_summary(resolved),
            "resolved": self._resolved_dict(resolved),
            "candidates": resolved.candidates,
            "needs_confirmation": resolved.needs_confirmation,
        }

    def describe(
        self,
        *,
        indicators: list[str] | None = None,
        index_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            resolved = self._resolve_request(
                indicators=indicators or [],
                index_ids=index_ids or [],
                org="福建",
                dimensions=[],
                period="月",
                start=None,
                end=None,
                allow_multi_org=False,
            )
        except QueryError as exc:
            return {"status": "error", "summary": str(exc)}
        items = []
        for indicator in resolved.indicators:
            availability = [
                {
                    "table": item.table_name,
                    "periods": sorted(item.periods),
                    "dims_available": item.dims_available,
                    "organizations": len(item.orgs),
                }
                for item in self.catalog.availability(indicator.index_id)
            ]
            latest = self._latest_available(indicator, resolved.orgs[0] if resolved.orgs else None, resolved.period)
            items.append(
                {
                    "index_id": indicator.index_id,
                    "name": indicator.name,
                    "unit": indicator.unit,
                    "one_table": indicator.one_table,
                    "index_table_name": indicator.index_table_name,
                    "periods": list(indicator.periods),
                    "city_types": list(indicator.city_types),
                    "predict_flag": indicator.predict_flag,
                    "index_type": indicator.index_type,
                    "add_type": indicator.add_type,
                    "accuracy": indicator.accuracy,
                    "definition": self.catalog.index_config(indicator.name),
                    "availability": availability,
                    "latest_available": latest,
                    "dimensions": [
                        {"wd_id": item.wd_id, "name": item.name, "type": item.dim_type or item.wd_type}
                        for item in self.catalog.indicator_dimensions(indicator)[:30]
                    ],
                }
            )
        return {"status": "ok" if items else "no_data", "summary": self._resolved_summary(resolved), "indicators": items}

    def query(
        self,
        *,
        indicators: list[str] | None = None,
        index_ids: list[str] | None = None,
        org: str = "福建",
        org_level: str = "auto",
        period: str = "月",
        start: str = "",
        end: str = "",
        dimensions: list[str] | None = None,
        group_by: str = "none",
        aggregate: str = "auto",
        metrics: list[str] | None = None,
        limit: int = 200,
        artifact_dir: Path | None = None,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 200), self.config.max_rows))
        try:
            resolved = self._resolve_request(
                indicators=indicators or [],
                index_ids=index_ids or [],
                org=org,
                dimensions=dimensions or [],
                period=period,
                start=start,
                end=end,
                org_level=org_level,
            )
        except QueryError as exc:
            return {"status": "error", "summary": str(exc), "candidates": []}

        if not resolved.indicators:
            return self._no_data_response(resolved, "没有匹配到指标", [])
        if not resolved.orgs:
            return self._no_data_response(resolved, "没有匹配到机构", [])

        all_rows: list[dict[str, Any]] = []
        sql_log: list[dict[str, Any]] = []
        tables: list[str] = []
        latest_dates: list[str] = []
        for indicator in resolved.indicators:
            dims = resolved.dimensions.get(indicator.index_id, [])
            for target_org in resolved.orgs:
                rows, table, sqls, notes = self._fetch_indicator(
                    indicator,
                    target_org,
                    resolved.period,
                    resolved.start,
                    resolved.end,
                    dims,
                    limit,
                )
                all_rows.extend(rows)
                sql_log.extend(sqls)
                if table:
                    tables.append(table)
                resolved.notes.extend(notes)
                latest = self._latest_available(indicator, target_org, resolved.period)
                if latest:
                    latest_dates.append(f"{indicator.name}/{target_org.label}: {latest}")

        if not all_rows:
            return self._no_data_response(resolved, "请求范围内没有数据", sql_log, tables=tables)

        shaped = self._shape_rows(
            all_rows,
            resolved,
            group_by=group_by,
            aggregate=aggregate,
            metrics=metrics or ["val", "val_tb", "val_hb"],
            limit=limit,
        )
        quality = check_quality(
            all_rows,
            requested_period=resolved.period,
            requested_end=(resolved.end or _today()).isoformat(),
            actual_period=str(all_rows[0].get("period") or resolved.period),
            fallbacks=resolved.fallbacks,
            requested_dimensions=[dim.name for dim in resolved.dimensions.get(resolved.indicators[0].index_id, [])],
        )
        artifact = self._write_artifact(shaped["rows"], shaped["columns"], artifact_dir, "data")
        summary = self._query_summary(resolved, shaped, quality, latest_dates)
        status = "ok"
        if any(item.severity == "high" for item in quality):
            status = "partial"
        return {
            "status": status,
            "summary": summary,
            "resolved": self._resolved_dict(resolved),
            "data": shaped,
            "diagnostics": {
                "latest_available": latest_dates,
                "available_periods": sorted({str(row.get("period")) for row in all_rows if row.get("period")}),
                "tables": sorted(set(tables)),
                "fallback_used": resolved.fallbacks,
                "notes": resolved.notes,
            },
            "data_quality": [item.to_dict() for item in quality],
            "sql": sql_log,
            "artifact": artifact,
            "candidates": resolved.candidates,
            "needs_confirmation": resolved.needs_confirmation,
        }

    def explore(
        self,
        *,
        indicators: list[str] | None = None,
        index_ids: list[str] | None = None,
        org: str = "福建",
        period: str = "月",
        start: str = "",
        end: str = "",
        dimensions: list[str] | None = None,
        limit: int = 200,
        artifact_dir: Path | None = None,
    ) -> dict[str, Any]:
        """EDA 包：趋势 + 机构对比 + 维度拆分 + 问题候选。"""
        base = self.query(
            indicators=indicators,
            index_ids=index_ids,
            org=org,
            org_level="auto",
            period=period,
            start=start,
            end=end,
            dimensions=dimensions,
            group_by="date",
            metrics=["val", "val_tb", "val_hb", "val_tq", "val_lj", "val_wcl"],
            limit=limit,
            artifact_dir=artifact_dir,
        )
        if base.get("status") in {"error", "no_data"}:
            return base
        rows = base.get("data", {}).get("rows", [])
        columns = base.get("data", {}).get("columns", [])
        problem_candidates = self._problem_candidates(rows, columns)
        # 机构对比（只在福建/地市场景下补充，避免过多查询）
        org_compare: list[dict[str, Any]] = []
        first = (indicators or [""])[0] if indicators else ""
        if first:
            compare = self.query(
                indicators=[first],
                org="福建",
                org_level="all_cities",
                period=period,
                start=start,
                end=end,
                dimensions=dimensions,
                group_by="org",
                aggregate="latest",
                metrics=["val", "val_tb", "val_hb"],
                limit=100,
                artifact_dir=None,
            )
            if compare.get("status") not in {"error", "no_data"}:
                org_compare = compare.get("data", {}).get("rows", [])
                yoy = [float(row["val_tb"]) for row in org_compare if row.get("val_tb") is not None]
                if len(yoy) >= 2 and max(yoy) - min(yoy) >= 10:
                    problem_candidates.append(
                        {
                            "code": "region_divergence",
                            "severity": "warn",
                            "title": "区域分化明显",
                            "evidence": {"max_yoy": max(yoy), "min_yoy": min(yoy), "spread_pp": round(max(yoy) - min(yoy), 2)},
                        }
                    )
        # 维度拆分
        dim_breakdown: list[dict[str, Any]] = []
        if first and not dimensions:
            dim_breakdown = self._dimension_breakdown(first, org, period, start, end, limit=50)
        return {
            "status": "ok",
            "summary": base.get("summary", ""),
            "resolved": base.get("resolved", {}),
            "trend": {"columns": columns, "rows": rows},
            "org_compare": org_compare,
            "dimension_breakdown": dim_breakdown,
            "problem_candidates": problem_candidates,
            "data_quality": base.get("data_quality", []),
            "sql": base.get("sql", []),
            "artifact": base.get("artifact", {}),
            "diagnostics": base.get("diagnostics", {}),
        }

    def outline(
        self,
        *,
        theme: str,
        scenario: str = "business",
        org: str = "福建",
        period: str = "月",
        start: str = "",
        end: str = "",
        indicators: list[str] | None = None,
    ) -> dict[str, Any]:
        scenario_obj = get_scenario(scenario)
        if scenario == "business" and theme:
            scenario_obj = match_scenario(theme)
        names = list(indicators or [])
        if not names:
            names = self._theme_indicator_names(theme, scenario_obj)
        # 主题里点名了地市（如“厦门售电量分析”）而 org 仍是默认值时，自动落到该地市。
        if normalize_text(org) in {"", "福建", "福建省", "全省"} and theme:
            for city in self.catalog.cities():
                if city.label and city.label in theme:
                    org = city.label
                    break
        resolved = self._resolve_request(
            indicators=names,
            index_ids=[],
            org=org,
            dimensions=[],
            period=period,
            start=start,
            end=end,
            allow_multi_org=False,
        )
        selected: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for indicator in resolved.indicators[:8]:
            latest = self._latest_available(indicator, resolved.orgs[0] if resolved.orgs else None, resolved.period)
            item = {
                "index_id": indicator.index_id,
                "name": indicator.name,
                "unit": indicator.unit,
                "periods": list(indicator.periods),
                "dimensions": [
                    {"wd_id": dim.wd_id, "name": dim.name, "type": dim.dim_type or dim.wd_type}
                    for dim in self.catalog.indicator_dimensions(indicator)[:12]
                ],
                "latest_available": latest,
                "available": bool(latest),
            }
            selected.append(item)
            if not latest:
                gaps.append({"indicator": indicator.name, "reason": "无可用数据/未探明"})
        sections = []
        for index, title in enumerate(scenario_obj.report_sections, start=1):
            sections.append(
                {
                    "heading": f"{index}. {title}",
                    "level": 1,
                    "objective": self._section_objective(title, scenario_obj),
                    "indicators": [item["name"] for item in selected[:5]],
                    "dimensions": list(scenario_obj.dimensions),
                    "required_analyses": list(scenario_obj.required_analyses),
                    "suggested_agents": self._suggested_agents(title),
                }
            )
        return {
            "status": "ok" if selected else "no_data",
            "summary": f"{scenario_obj.name}大纲：{len(sections)} 个章节，{len(selected)} 个指标，{len(gaps)} 个数据缺口。",
            "scenario": scenario_obj.to_dict(),
            "theme": theme,
            "org": (resolved.orgs[0].label if resolved.orgs else org),
            "period": resolved.period,
            "indicators": selected,
            "sections": sections,
            "gaps": gaps,
            "requirements": {
                "minimum_indicators": 3,
                "minimum_dimensions": 3,
                "minimum_comparisons": 2,
                "minimum_problem_points": 2,
                "minimum_causes": 2,
                "minimum_recommendations": 3,
                "data_quality_required": True,
                "outline_confirmation_required": True,
            },
            "recommended_agents": ["smart-data-query", "power-grid-analysis", "knowledge-qa"],
        }

    def news(
        self,
        *,
        keywords: list[str] | None = None,
        start: str = "",
        end: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        keywords = [str(item).strip() for item in (keywords or []) if str(item).strip()]
        start_date = _as_date(start) or (_today() - timedelta(days=180))
        end_date = _as_date(end) or _today()
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        limit = max(1, min(int(limit or 10), 50))
        rows: list[dict[str, Any]] = []
        sqls: list[dict[str, Any]] = []
        # 1) 人工整理的月度事件
        try:
            event_rows = self.client.fetch_all(
                f"SELECT period, event_text, source FROM {T_EVENTS} "
                "WHERE period BETWEEN %s AND %s ORDER BY period DESC LIMIT %s",
                (start_date.strftime("%Y-%m"), end_date.strftime("%Y-%m"), limit * 2),
            ).rows
            for row in event_rows:
                rows.append(
                    {
                        "date": str(row.get("period") or ""),
                        "title": str(row.get("event_text") or "")[:120],
                        "summary": str(row.get("event_text") or ""),
                        "source": str(row.get("source") or "curated"),
                        "kind": "curated_event",
                    }
                )
        except DataClientError:
            pass
        # 2) 新闻库（去重、过滤未来/日历噪声）
        for keyword in keywords[:5] or [""]:
            try:
                result = self.client.fetch_all(
                    f"SELECT news_time, keyword, news_summary, source FROM {T_NEWS} "
                    "WHERE news_time BETWEEN %s AND %s "
                    "AND (%s = '' OR news_summary LIKE %s OR news_detail LIKE %s) "
                    "ORDER BY news_time DESC LIMIT 80",
                    (
                        start_date.isoformat(),
                        end_date.isoformat(),
                        keyword,
                        f"%{keyword}%" if keyword else "%",
                        f"%{keyword}%" if keyword else "%",
                    ),
                )
                sqls.append({"purpose": "news", "sql": result.sql, "elapsed_ms": result.elapsed_ms, "row_count": result.row_count})
                for row in result.rows:
                    text = str(row.get("news_summary") or "").strip()
                    if len(text) < 30:
                        continue
                    if any(word in text[:80] for word in ("农历", "星座", "天气预报", "节气")):
                        continue
                    rows.append(
                        {
                            "date": str(row.get("news_time") or ""),
                            "title": text.splitlines()[0][:120],
                            "summary": text[:500],
                            "source": str(row.get("source") or ""),
                            "kind": "news",
                        }
                    )
            except DataClientError as exc:
                sqls.append({"purpose": "news", "sql": "", "error": str(exc)})
        # 去重 + 排序
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = normalize_text(str(row.get("title") or ""))[:80] or normalize_text(str(row.get("summary") or ""))[:80]
            if key and key not in unique:
                unique[key] = row
        ordered = sorted(unique.values(), key=lambda item: str(item.get("date") or ""), reverse=True)[:limit]
        return {
            "status": "ok" if ordered else "no_data",
            "summary": f"检索到 {len(ordered)} 条政策/事件（已去重、限时）。",
            "items": ordered,
            "sql": sqls,
        }

    def sql(self, sql: str, *, limit: int = 200) -> dict[str, Any]:
        try:
            result = self.client.execute_raw(sql)
        except Exception as exc:  # noqa: BLE001 - 工具层兜底
            return {"status": "error", "summary": f"SQL 查询失败: {exc}"}
        rows = _rows_jsonable(result.rows)
        return {
            "status": "ok" if rows else "no_data",
            "summary": f"SQL 返回 {len(rows)} 行" + ("（已截断）" if result.truncated else ""),
            "data": {"columns": result.columns, "rows": rows, "row_count": len(rows), "truncated": result.truncated},
            "sql": [{"purpose": "raw", "sql": result.sql, "elapsed_ms": result.elapsed_ms, "row_count": result.row_count}],
        }

    def forecast(self, *, date: str = "", area: str = "fj") -> dict[str, Any]:
        """读取 Redis 中的预测/特征缓存（只读、可选）。"""
        if self.redis is None:
            return {
                "status": "unavailable",
                "summary": "Redis 未启用，无法读取预测/特征缓存；可继续用业务库实际值或预测指标。",
            }
        day = str(date or "").strip()
        if not day:
            day = _today().isoformat()
        try:
            found = self.redis.forecast_feature(day, area)
        except Exception as exc:  # noqa: BLE001 - Redis 辅助数据失败不影响主流程
            return {"status": "error", "summary": f"Redis 读取失败: {exc}"}
        if not found:
            return {
                "status": "no_data",
                "summary": f"Redis 未找到 {day} / {area} 的预测缓存；可改用预测指标或业务库实际值。",
            }
        value = found.get("value") if isinstance(found, dict) else None
        rows: list[dict[str, Any]] = []
        if isinstance(value, dict):
            rows = [{"field": key, "value": item} for key, item in list(value.items())[:200]]
        else:
            rows = [{"field": "value", "value": value}]
        return {
            "status": "ok",
            "summary": f"Redis 预测/特征缓存：{found.get('key')}（辅助数据，非业务库权威值）。",
            "data": {"columns": ["field", "value"], "rows": rows, "row_count": len(rows), "truncated": False},
            "diagnostics": {"source": "redis", "key": found.get("key"), "date": day, "area": area},
        }

    def boundary_summary(self) -> str:
        return self.catalog.boundary_summary()

    # -- 解析 -----------------------------------------------------------

    def _resolve_request(
        self,
        *,
        indicators: list[str],
        index_ids: list[str],
        org: str,
        dimensions: list[str],
        period: str,
        start: str | date | None,
        end: str | date | None,
        org_level: str = "auto",
        allow_multi_org: bool = True,
    ) -> ResolvedRequest:
        resolved = ResolvedRequest(period=period if period in _PERIOD_ORDER else "月")
        # 指标
        seen: set[str] = set()
        hint_dimensions: dict[str, str] = {}
        for index_id in index_ids:
            indicator = self.catalog.get_indicator(index_id)
            if indicator and indicator.index_id not in seen:
                resolved.indicators.append(indicator)
                seen.add(indicator.index_id)
        for text in indicators:
            hits = self.catalog.resolve_indicators(text)
            if not hits:
                resolved.notes.append(f"未匹配到指标：{text}")
                continue
            best = hits[0]
            resolved.candidates.extend(
                [
                    {
                        "index_id": hit.indicator.index_id,
                        "name": hit.indicator.name,
                        "score": round(hit.score, 3),
                        "match_type": hit.match_type,
                    }
                    for hit in hits[:5]
                ]
            )
            if len(hits) > 1 and hits[0].score - hits[1].score < 0.05:
                resolved.needs_confirmation = True
            if best.indicator.index_id not in seen:
                resolved.indicators.append(best.indicator)
                seen.add(best.indicator.index_id)
            if best.org_hint and normalize_text(org) in {"", "福建", "福建省", "全省"}:
                org = best.org_hint
            if best.dimension_hint:
                hint_dimensions[best.indicator.index_id] = best.dimension_hint
        if not resolved.indicators:
            if getattr(self.catalog, "last_error", ""):
                raise QueryError(f"业务库读取失败：{self.catalog.last_error}")
            if resolved.candidates:
                raise QueryError("指标存在多个候选，请确认：" + "、".join(item["name"] for item in resolved.candidates[:5]))
            raise QueryError("未匹配到有效指标，请检查指标名称")

        # 机构
        if org_level == "all_cities":
            resolved.orgs = self.catalog.cities()
        elif org_level == "all_counties":
            hits = self.catalog.resolve_orgs(org)
            city = hits[0].org if hits else None
            all_counties = self.catalog.counties(city.label if city and city.type != 3001 else "")
            resolved.orgs = all_counties[:50]
            if len(all_counties) > 50:
                resolved.notes.append(f"区县数量较多（{len(all_counties)}），已限制前 50 个；可指定地市缩小范围")
        else:
            if org_level == "province":
                org_hits = [hit for hit in self.catalog.resolve_orgs("福建") if hit.org.type == 3001]
            elif org_level == "city":
                org_hits = [hit for hit in self.catalog.resolve_orgs(org) if hit.org.type == 3002] or self.catalog.resolve_orgs(org)
            elif org_level == "county":
                org_hits = [hit for hit in self.catalog.resolve_orgs(org) if hit.org.type == 3003] or self.catalog.resolve_orgs(org)
            else:
                org_hits = self.catalog.resolve_orgs(org)
            if not org_hits:
                org_hits = [hit for hit in self.catalog.resolve_orgs("福建") if hit.org.type == 3001]
                resolved.fallbacks.append("org_fallback")
                resolved.notes.append(f"未匹配到机构「{org}」，已回退到福建")
            resolved.orgs = [org_hits[0].org]
        if not resolved.orgs:
            raise QueryError("未匹配到有效机构")

        # 周期回退（按指标交集）
        allowed = set(_PERIOD_ORDER)
        for indicator in resolved.indicators:
            periods = set(_periods_from_indicator(indicator))
            allowed &= periods
        if resolved.period not in allowed:
            fallback = next((item for item in _PERIOD_ORDER if item in allowed), resolved.period)
            resolved.fallbacks.append("period_fallback")
            resolved.notes.append(f"周期「{resolved.period}」不可用，已回退到「{fallback}」")
            resolved.period = fallback

        # 维度
        for indicator in resolved.indicators:
            if not dimensions:
                continue
            hits: list[Dimension] = []
            for text in dimensions:
                dim_hits = self.catalog.resolve_dimensions(indicator, text)
                if dim_hits:
                    hits.append(dim_hits[0].dimension)
                else:
                    resolved.notes.append(f"指标「{indicator.name}」未匹配到维度「{text}」，将回退总量")
                    resolved.fallbacks.append("dim_fallback")
            # 去重
            unique: dict[str, Dimension] = {item.wd_id: item for item in hits if item.wd_id}
            resolved.dimensions[indicator.index_id] = list(unique.values())

        # 自然语言里的维度提示（如“工业售电量”）：仅在未显式传 dimensions 时使用
        if not dimensions:
            for indicator in resolved.indicators:
                hint = hint_dimensions.get(indicator.index_id)
                if not hint:
                    continue
                dim_hits = self.catalog.resolve_dimensions(indicator, hint)
                if dim_hits:
                    resolved.dimensions[indicator.index_id] = [dim_hits[0].dimension]
                    resolved.notes.append(f"从问题中识别维度：{hint}")

        # 时间范围
        resolved.start, resolved.end = self._resolve_range(start, end, resolved.period)
        return resolved

    def _resolve_range(
        self,
        start: str | date | None,
        end: str | date | None,
        period: str,
    ) -> tuple[date, date]:
        start_date = _as_date(start)
        end_date = _as_date(end)
        if end_date is None:
            end_date = _previous_month_end()
        if start_date is None:
            if period == "年":
                start_date = date(end_date.year - 4, 1, 1)
            elif period == "月":
                start_date = date(end_date.year - 1, end_date.month, 1)
            else:
                start_date = end_date - timedelta(days=90)
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        if period == "月":
            return _month_start(start_date), _month_end(end_date)
        if period == "年":
            return date(start_date.year, 1, 1), date(end_date.year, 12, 31)
        if period == "季":
            return _quarter_start(start_date), _quarter_end(end_date)
        if period == "周":
            return _week_start(start_date), _week_end(end_date)
        return start_date, end_date

    # -- 路由与查询 -----------------------------------------------------

    def _candidate_tables(
        self,
        indicator: Indicator,
        org: Org,
        period: str,
        dims: list[Dimension],
    ) -> list[str]:
        if _is_weather(indicator):
            return [T_WEATHER]
        if _is_four_rate(indicator):
            return [T_FOUR_RATE]

        level_table = LEVEL_TABLE_BY_ORG_TYPE.get(org.type, LEVEL_TABLE_BY_ORG_TYPE.get(3001))
        candidates: list[str] = []

        def add(table: str) -> None:
            table = str(table or "").strip()
            if table and table not in candidates:
                candidates.append(table)

        # 1) ai_data_struct 优先
        availability = self.catalog.availability(indicator.index_id)
        scored: list[tuple[int, str]] = []
        for item in availability:
            if item.periods and period not in item.periods:
                continue
            if item.orgs and org.org_code not in item.orgs:
                continue
            score = 0
            if item.table_name in LEVEL_TABLES:
                score += 3 if item.table_name == level_table else 1
            if dims and item.table_name.endswith("_ssx"):
                score += 3
            if not dims and not item.table_name.endswith("_ssx"):
                score += 2
            if dims and not item.dims_available:
                score -= 2
            scored.append((score, item.table_name))
        for _, table in sorted(scored, key=lambda item: item[0], reverse=True):
            add(table)

        # 2) 文档规则
        if indicator.one_table == 1:
            add(indicator.index_table_name)
            if dims:
                add(indicator.index_table_name + "_ssx")
            add(level_table)
        else:
            if dims:
                add(indicator.index_table_name + "_ssx")
                add(level_table)
            else:
                add(level_table)
                add(indicator.index_table_name)
        # 3) 其他层级/后缀兜底
        add(indicator.index_table_name)
        for table in LEVEL_TABLES:
            add(table)
        return [table for table in candidates if self._table_exists(table)]

    def _table_exists(self, table: str) -> bool:
        try:
            return bool(table) and self.client.table_exists(table)
        except DataClientError:
            return False

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

    # -- 整形/诊断 ------------------------------------------------------

    def _shape_rows(
        self,
        rows: list[dict[str, Any]],
        resolved: ResolvedRequest,
        *,
        group_by: str,
        aggregate: str,
        metrics: list[str],
        limit: int,
    ) -> dict[str, Any]:
        allowed = [metric for metric in metrics if metric in DATA_COLUMNS]
        if not allowed:
            allowed = ["val", "val_tb", "val_hb"]
        rows = _rows_jsonable(rows)
        if group_by == "none":
            columns = ["stat_date", "org_id", "dim_name", "index_name", "unit", *allowed]
            trimmed = [{key: row.get(key) for key in columns if key in row} for row in rows[:limit]]
            return {"columns": columns, "rows": trimmed, "row_count": len(trimmed), "truncated": len(rows) > limit}

        if aggregate == "none":
            columns = ["index_name", "index_id", "stat_date", "period", "org_id", "dim_val", "dim_name", "unit", *allowed]
            trimmed = [{key: row.get(key) for key in columns if key in row} for row in rows[:limit]]
            return {"columns": columns, "rows": trimmed, "row_count": len(trimmed), "truncated": len(rows) > limit}

        key_name = {"date": "stat_date", "org": "org_id", "dimension": "dim_val"}[group_by]
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row.get(key_name) or "")
            # 多指标时按指标分开，避免把不同指标的值加在一起。
            series_key = f"{key}||{row.get('index_name') or ''}"
            group = grouped.setdefault(
                series_key,
                {
                    "key": key,
                    **(
                        {"org_name": (self.catalog.get_org(key).label if self.catalog.get_org(key) else key)}
                        if key_name == "org_id"
                        else {}
                    ),
                    "index_name": row.get("index_name"),
                    "unit": row.get("unit"),
                    "dim_name": row.get("dim_name"),
                    "count": 0,
                },
            )
            group["count"] += 1
            for metric in allowed:
                value = row.get(metric)
                if value is None:
                    continue
                if aggregate == "sum" or (aggregate == "auto" and self._can_sum(metric)):
                    group[metric] = float(group.get(metric) or 0) + float(value)
                elif aggregate in {"avg"}:
                    group.setdefault(f"_{metric}_sum", 0.0)
                    group[f"_{metric}_sum"] += float(value)
                else:
                    # latest：按 stat_date 最大保留
                    if str(row.get("stat_date") or "") >= str(group.get("_latest_date") or ""):
                        group[metric] = value
                        group["_latest_date"] = str(row.get("stat_date") or "")
        if aggregate == "avg":
            for group in grouped.values():
                count = max(1, int(group.get("count") or 1))
                for metric in allowed:
                    if f"_{metric}_sum" in group:
                        group[metric] = round(group[f"_{metric}_sum"] / count, 2)
                        group.pop(f"_{metric}_sum", None)
        columns = ["key"]
        if group_by == "org":
            columns.append("org_name")
        columns.extend(["index_name", "unit", "dim_name", "count", *allowed])
        ordered = sorted(grouped.values(), key=lambda item: str(item.get("key") or ""))[:limit]
        for row in ordered:
            row.pop("_latest_date", None)
            for key in [item for item in list(row) if item.startswith("_") and item.endswith("_sum")]:
                row.pop(key, None)
        return {"columns": columns, "rows": ordered, "row_count": len(ordered), "truncated": len(grouped) > limit}

    @staticmethod
    def _can_sum(metric: str) -> bool:
        # 只有当期值/增减额可求和；比率、排名、完成率不可求和。
        return metric in {"val", "val_lj", "val_tq", "val_jh", "val_mb", "val_tb_zje", "val_hb_zje"}

    def _problem_candidates(self, rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
        problems: list[dict[str, Any]] = []
        if not rows:
            return problems
        # 同比下滑
        yoy_pairs = [
            (row.get("stat_date") or row.get("key"), row.get("val_tb"), row.get("val"))
            for row in rows
            if row.get("val_tb") is not None
        ]
        if yoy_pairs:
            worst = min(yoy_pairs, key=lambda item: float(item[1]))
            if float(worst[1]) <= -5:
                problems.append(
                    {
                        "code": "yoy_decline",
                        "severity": "high" if float(worst[1]) <= -10 else "warn",
                        "title": "同比下滑",
                        "evidence": {"stat_date": str(worst[0]), "val_tb": worst[1], "val": worst[2]},
                    }
                )
        # 连续环比下降
        hb_values = [
            (str(row.get("stat_date") or row.get("key") or ""), row.get("val_hb"))
            for row in rows
            if row.get("val_hb") is not None
        ]
        hb_values.sort()
        streak = 0
        max_streak = 0
        for _, value in hb_values:
            if float(value) < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        if max_streak >= 3:
            problems.append(
                {
                    "code": "mom_decline_streak",
                    "severity": "warn",
                    "title": "连续环比下降",
                    "evidence": {"streak": max_streak},
                }
            )
        # 目标完成：只有存在目标/计划值时才判断，避免把“月度进度”误当缺口。
        wcl = [
            (row.get("stat_date") or row.get("key"), row.get("val_wcl"), row.get("val_mb"), row.get("val_jh"))
            for row in rows
            if row.get("val_wcl") is not None and (row.get("val_mb") is not None or row.get("val_jh") is not None)
        ]
        if wcl:
            latest = wcl[-1]
            if float(latest[1]) < 100:
                problems.append(
                    {
                        "code": "target_gap",
                        "severity": "warn",
                        "title": "目标完成度不足",
                        "evidence": {"stat_date": str(latest[0]), "val_wcl": latest[1]},
                    }
                )
        # 恒定序列（来自质量检查，rows 里已经无可避免；这里补一个轻量提示）
        values = [row.get("val") for row in rows if row.get("val") is not None]
        if len(values) >= 3 and len({round(float(v), 4) for v in values}) == 1:
            problems.append(
                {
                    "code": "constant_series",
                    "severity": "high",
                    "title": "序列恒定异常",
                    "evidence": {"value": values[0], "periods": len(values)},
                }
            )
        return problems

    def _dimension_breakdown(
        self,
        indicator_name: str,
        org: str,
        period: str,
        start: str,
        end: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        hits = self.catalog.resolve_indicators(indicator_name)
        if not hits:
            return []
        indicator = hits[0].indicator
        org_hits = self.catalog.resolve_orgs(org)
        if not org_hits:
            return []
        if indicator.is_prediction:
            return []
        tables = self._candidate_tables(indicator, org_hits[0].org, period, [Dimension("0", "x")])
        for table in tables[:3]:
            query_rows, _ = self._run_table_query(
                indicator,
                org_hits[0].org,
                period,
                _as_date(start),
                _as_date(end),
                [],
                limit,
                table,
                ignore_dim=True,
            )
            if query_rows:
                break
        else:
            query_rows = []
        # 贡献度：按 dim_val 汇总 val 与 val_tq；优先只保留该指标登记的业务维度。
        allowed_wd_ids = {
            str(dim.wd_id) for dim in self.catalog.indicator_dimensions(indicator) if dim.wd_id
        }
        grouped: dict[str, dict[str, Any]] = {}
        for row in query_rows:
            dim = str(row.get("dim_val") or "")
            if not dim:
                continue
            if allowed_wd_ids and dim not in allowed_wd_ids:
                continue
            group = grouped.setdefault(
                dim,
                {"dim_val": dim, "dim_name": row.get("dim_name") or "", "val": 0.0, "val_tq": 0.0},
            )
            group["val"] += float(row.get("val") or 0)
            group["val_tq"] += float(row.get("val_tq") or 0)
        total_delta = sum(item["val"] - item["val_tq"] for item in grouped.values())
        result = []
        for item in grouped.values():
            delta = item["val"] - item["val_tq"]
            item["delta"] = round(delta, 4)
            item["share"] = round(delta / total_delta, 4) if total_delta else None
            result.append(item)
        result.sort(key=lambda item: item["val"], reverse=True)
        return result[:limit]

    def _theme_indicator_names(self, theme: str, scenario: AnalysisScenario) -> list[str]:
        text = str(theme or "")
        names: list[str] = []
        # 1) 主题里直接出现的指标名
        for indicator in self.catalog.indicators:
            if indicator.status == 1 and indicator.name and indicator.name in text:
                names.append(indicator.name)
        # 2) 场景指标关键词匹配（即使主题里已出现一个指标，也补充相关指标）
        for keyword in scenario.indicator_keywords:
            if len(names) >= 6:
                break
            hits = self.catalog.resolve_indicators(keyword)
            if hits and hits[0].indicator.name not in names:
                names.append(hits[0].indicator.name)
        # 3) 场景/部门主指标
        if not names:
            for row in self.catalog.index_system():
                if not row.get("is_main"):
                    continue
                scene = str(row.get("scene") or "")
                if scenario.scene_names and not any(item in scene for item in scenario.scene_names):
                    continue
                name = str(row.get("index_name") or "").strip()
                if name:
                    names.append(name)
        # 去重、限量
        unique: list[str] = []
        for name in names:
            if name not in unique:
                unique.append(name)
        return unique[:8]

    @staticmethod
    def _section_objective(title: str, scenario: AnalysisScenario) -> str:
        """给大纲章节一个具体的写作目标，避免所有章节都写同一句。"""
        text = str(title or "")
        if "摘要" in text:
            return "结论先行：3–5 条核心发现，每条带指标/日期/数字/来源。"
        if "背景" in text or "口径" in text:
            return "说明主题、机构、期间、指标定义、单位、实际/预测口径与数据来源。"
        if "总体" in text:
            return "回答规模、增速、目标完成、与同期/目标的差距，给出核心指标表。"
        if "趋势" in text or "时间" in text:
            return "看近 12 期走势、拐点、连续下降/上升；区分季节性、基数效应与异常。"
        if "区域" in text or "地市" in text:
            return "九地市排名、分化、贡献度；找出最强/最弱区域并解释。"
        if "产业" in text or "行业" in text or "结构" in text:
            return "看产业/行业/客户结构占比、增量贡献、集中度与结构变化。"
        if "问题" in text or "诊断" in text:
            return "列出 2–3 个问题，按严重程度排序；每个问题给数据证据、影响与涉及机构。"
        if "成因" in text:
            return "按政策/企业事件/天气/市场/基数等假设，给出影响路径与数据验证；区分事实/推断。"
        if "建议" in text or "预测" in text:
            return "给趋势/情景判断和 3–5 条可执行建议；标注不确定性与责任方向。"
        return f"围绕「{title}」回答：{('、'.join(scenario.required_analyses)) or '现状、问题、成因与建议'}。"

    @staticmethod
    def _suggested_agents(title: str) -> list[str]:
        if any(word in title for word in ("政策", "背景", "成因")):
            return ["knowledge-qa", "power-grid-analysis"]
        if any(word in title for word in ("趋势", "预测", "建议")):
            return ["power-grid-analysis", "smart-data-query"]
        return ["smart-data-query"]

    def _resolved_summary(self, resolved: ResolvedRequest) -> str:
        names = "、".join(item.name for item in resolved.indicators) or "无"
        orgs = "、".join(item.label for item in resolved.orgs) or "无"
        return f"指标：{names}；机构：{orgs}；周期：{resolved.period}；时间：{resolved.start} ~ {resolved.end}"

    def _resolved_dict(self, resolved: ResolvedRequest) -> dict[str, Any]:
        return {
            "indicators": [
                {"index_id": item.index_id, "name": item.name, "unit": item.unit}
                for item in resolved.indicators
            ],
            "orgs": [{"org_code": item.org_code, "name": item.label, "type": item.type} for item in resolved.orgs],
            "dimensions": {
                key: [{"wd_id": dim.wd_id, "name": dim.name, "type": dim.dim_type or dim.wd_type} for dim in dims]
                for key, dims in resolved.dimensions.items()
            },
            "period": resolved.period,
            "date_range": [resolved.start.isoformat() if resolved.start else "", resolved.end.isoformat() if resolved.end else ""],
        }

    def _query_summary(
        self,
        resolved: ResolvedRequest,
        shaped: dict[str, Any],
        quality: list[QualityIssue],
        latest_dates: list[str],
    ) -> str:
        lines = [
            f"查询完成：{len(shaped.get('rows', []))} 行。",
            self._resolved_summary(resolved),
        ]
        if latest_dates:
            lines.append("数据边界：" + "；".join(latest_dates[:5]))
        if quality:
            lines.append("数据质量：" + summarize_issues(quality))
        if resolved.notes:
            lines.append("说明：" + "；".join(dict.fromkeys(resolved.notes)))
        return "\n".join(lines)

    def _no_data_response(
        self,
        resolved: ResolvedRequest,
        reason: str,
        sql_log: list[dict[str, Any]],
        *,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        latest: list[str] = []
        available_periods: set[str] = set()
        available_dims: list[str] = []
        for indicator in resolved.indicators:
            org = resolved.orgs[0] if resolved.orgs else None
            latest_value = self._latest_available(indicator, org, resolved.period) if org else ""
            if latest_value:
                latest.append(f"{indicator.name}/{org.label if org else ''}: {latest_value}")
            available_periods.update(_periods_from_indicator(indicator))
            available_dims.extend(dim.name for dim in self.catalog.indicator_dimensions(indicator)[:10])
        return {
            "status": "no_data",
            "summary": f"{reason}。最新可用：{'；'.join(latest) or '未探明'}；可用周期：{'、'.join(sorted(available_periods)) or '未知'}。",
            "resolved": self._resolved_dict(resolved),
            "data": {"columns": [], "rows": [], "row_count": 0, "truncated": False},
            "diagnostics": {
                "latest_available": latest,
                "available_periods": sorted(available_periods),
                "available_dimensions": list(dict.fromkeys(available_dims))[:20],
                "tables": tables or [],
                "fallback_used": resolved.fallbacks,
                "notes": resolved.notes,
                "suggested_query": {
                    "period": resolved.period,
                    "start": latest[0].split(": ")[-1] if latest else "",
                    "end": latest[0].split(": ")[-1] if latest else "",
                },
            },
            "data_quality": [QualityIssue("no_data", "high", reason, {}).to_dict()],
            "sql": sql_log,
            "candidates": resolved.candidates,
            "needs_confirmation": resolved.needs_confirmation,
        }

    def _explore_summary(self, problems: list[dict[str, Any]]) -> str:
        if not problems:
            return "问题候选：未发现明显异常，可结合外部事件进一步判断。"
        return "问题候选：\n" + "\n".join(
            f"- [{item.get('severity')}] {item.get('title')}：{json.dumps(item.get('evidence'), ensure_ascii=False)}"
            for item in problems[:8]
        )

    def _write_artifact(
        self,
        rows: list[dict[str, Any]],
        columns: list[str],
        artifact_dir: Path | None,
        prefix: str,
    ) -> dict[str, Any]:
        if artifact_dir is None or not rows:
            return {}
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
            name = f"{prefix}-{stamp}.csv"
            path = artifact_dir / name
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            return {"path": str(path), "format": "csv", "rows": len(rows)}
        except OSError:
            return {}


__all__ = ["BusinessQueryService", "QueryError", "ResolvedRequest"]
