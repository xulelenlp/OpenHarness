"""业务查询服务：解析 → 路由 → 查询 → 质量诊断 → 整形。

本模块只依赖 :class:`~qaapp.data.mysql.MySqlClient` 与
:class:`~qaapp.data.catalog.BusinessCatalog`，不读取环境变量、不创建连接。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from qaapp.agents.scenarios import AnalysisScenario, get_scenario, match_scenario
from qaapp.data.catalog import BusinessCatalog, normalize_text
from qaapp.data.datasources import MySQLConfig
from qaapp.data.fetcher import FetcherMixin
from qaapp.data.formatting import _rows_jsonable
from qaapp.data.mysql import DataClientError, MySqlClient
from qaapp.data.periods import (
    _as_date,
    _periods_from_indicator,
    _today,
)
from qaapp.data.quality import QualityIssue, check_quality, summarize_issues
from qaapp.data.resolver import QueryError, ResolvedRequest, ResolverMixin
from qaapp.data.schema import (
    T_EVENTS,
    T_NEWS,
)
from qaapp.data.shaping import ShapingMixin


class BusinessQueryService(ResolverMixin, FetcherMixin, ShapingMixin):
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
                "minimum_external_categories": 4,
                "external_information_required": True,
                "fact_sources_required": True,
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
        if "外部" in text:
            return (
                "用官方来源补节假日与工作日数、气象与气候、重大外部事件；"
                "逐条记录机构/日期/链接，并与数据问题做交叉验证；矛盾处写明待验证。"
            )
        if "建议" in text or "预测" in text:
            return "给趋势/情景判断和 3–5 条可执行建议；标注不确定性与责任方向。"
        return f"围绕「{title}」回答：{('、'.join(scenario.required_analyses)) or '现状、问题、成因与建议'}。"

    @staticmethod
    def _suggested_agents(title: str) -> list[str]:
        if any(word in title for word in ("政策", "背景", "成因", "外部")):
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
