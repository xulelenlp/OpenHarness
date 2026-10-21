"""查询请求解析：指标/机构/维度/周期/时间范围。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from qaapp.data.catalog import Dimension, Indicator, Org, normalize_text
from qaapp.data.mysql import DataClientError
from qaapp.data.periods import (
    _PERIOD_ORDER,
    _as_date,
    _month_end,
    _month_start,
    _periods_from_indicator,
    _previous_month_end,
    _quarter_end,
    _quarter_start,
    _week_end,
    _week_start,
)
from qaapp.data.schema import (
    FOUR_RATE_NAMES,
    LEVEL_TABLE_BY_ORG_TYPE,
    LEVEL_TABLES,
    T_FOUR_RATE,
    T_WEATHER,
    WEATHER_KEYWORDS,
)


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


def _is_weather(indicator: Indicator) -> bool:
    return any(word in indicator.name for word in WEATHER_KEYWORDS)


def _is_four_rate(indicator: Indicator) -> bool:
    return any(word in indicator.name for word in FOUR_RATE_NAMES)


def _is_total_dim_fallback(row: dict[str, Any]) -> bool:
    dim = str(row.get("dim_val") or "").strip()
    return dim in {"", "NULL", "null", "None"}


class ResolverMixin:
    """Resolve a natural-language request into indicators, orgs, dims and dates."""

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
