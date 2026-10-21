"""业务库元数据目录：指标、机构、维度、可用性与数据边界。

所有查询都通过 :class:`qaapp.data.mysql.MySqlClient`，表名来自
:mod:`qaapp.data.schema`。目录在进程内缓存（默认 300 秒），刷新由
``refresh()`` 或过期触发。
"""

from __future__ import annotations

import difflib
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from qaapp.data.datasources import MySQLConfig
from qaapp.data.mysql import DataClientError, MySqlClient
from qaapp.data.schema import (
    BOUNDARY_INDICATORS,
    LEVEL_TABLES,
    PROVINCE_ORG_ID,
    QUERY_NOISE_WORDS,
    T_DATA_STRUCT,
    T_DIM,
    T_INDEX,
    T_INDEX_CONFIG,
    T_INDEX_DIM,
    T_INDEX_SIM,
    T_INDEX_SYSTEM,
    T_ORG,
    T_SCENE,
)

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[，。！？、；：“”‘’（）()【】\[\]《》<>\"'`]+")


def normalize_text(text: str) -> str:
    """指标/维度/机构名的轻量规范化。"""
    value = str(text or "").strip()
    value = _PUNCT.sub(" ", value)
    value = _WS.sub("", value)
    for word in QUERY_NOISE_WORDS:
        value = value.replace(word, "")
    return value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


@dataclass(frozen=True)
class Indicator:
    index_id: str
    name: str
    unit: str = ""
    one_table: int = 0
    index_table_name: str = ""
    periods: tuple[str, ...] = ()
    city_types: tuple[int, ...] = ()
    predict_flag: int = 0
    status: int = 1
    scene: str = ""
    accuracy: int | None = None
    index_type: int = 0
    add_type: int = 1
    default_org: str = ""
    default_period: str = ""

    @property
    def is_prediction(self) -> bool:
        # 注意：predict_flag 表示“需要归因/预测”，不等于该指标本身是预测值；
        # 真正的预测指标看名称或 PRE- 前缀。
        return "预测" in self.name or "预估" in self.name or self.index_id.startswith("PRE-")


@dataclass
class IndicatorHit:
    indicator: Indicator
    score: float
    match_type: str
    org_hint: str = ""
    dimension_hint: str = ""


@dataclass(frozen=True)
class Org:
    org_code: str
    name: str
    short_name: str
    type: int
    province: str = ""
    city: str = ""
    county: str = ""
    parent_code: str = ""

    @property
    def label(self) -> str:
        return self.short_name or self.name or self.org_code


@dataclass
class OrgHit:
    org: Org
    score: float
    match_type: str


@dataclass(frozen=True)
class Dimension:
    wd_id: str
    name: str
    wd_type: str = ""
    dim_type: str = ""


@dataclass
class DimensionHit:
    dimension: Dimension
    score: float
    match_type: str


@dataclass
class Availability:
    table_name: str
    periods: set[str] = field(default_factory=set)
    orgs: set[str] = field(default_factory=set)
    dims_available: bool = False


class BusinessCatalog:
    """懒加载的元数据目录。"""

    def __init__(self, client: MySqlClient, config: MySQLConfig) -> None:
        self.client = client
        self.config = config
        self._ttl = max(30, int(config.cache_ttl_seconds))
        self._loaded_at = 0.0
        self._indicators: list[Indicator] = []
        self._indicator_by_id: dict[str, Indicator] = {}
        self._synonyms: list[dict[str, Any]] = []
        self._orgs: list[Org] = []
        self._dimensions: list[Dimension] = []
        self._index_dims: dict[str, list[Dimension]] = {}
        self._availability: dict[str, list[Availability]] = {}
        self._index_system: list[dict[str, Any]] = []
        self._index_config: dict[str, dict[str, Any]] = {}
        self._scenes: list[dict[str, Any]] = []
        self._boundary_cache: tuple[float, str] = (0.0, "")
        self.last_error = ""

    # -- 加载 -----------------------------------------------------------

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._loaded_at and now - self._loaded_at < self._ttl:
            return
        self._indicators = self._load_indicators()
        self._indicator_by_id = {item.index_id: item for item in self._indicators}
        self._synonyms = self._query(
            f"SELECT index_id, index_name, sim_name, priority FROM {T_INDEX_SIM} "
            "WHERE status = 1 ORDER BY priority ASC, id ASC",
            (),
        )
        self._orgs = self._load_orgs()
        self._index_system = self._query(
            f"SELECT index_id, index_name, bm_mc, scene, is_main, is_rising, "
            f"is_day, is_month, is_year FROM {T_INDEX_SYSTEM}",
            (),
        )
        self._index_config = {
            str(row.get("index_name") or ""): row
            for row in self._query(
                f"SELECT index_name, index_define, cal_method, department, class, "
                f"data_type, unit FROM {T_INDEX_CONFIG}",
                (),
            )
        }
        self._scenes = self._query(f"SELECT id, name, index_name FROM {T_SCENE}", ())
        self._loaded_at = now

    def _query(self, sql: str, params: Iterable[Any]) -> list[dict[str, Any]]:
        try:
            rows = self.client.fetch_all(sql, params).rows
            self.last_error = ""
            return rows
        except DataClientError as exc:
            self.last_error = str(exc)
            return []

    def _load_indicators(self) -> list[Indicator]:
        rows = self._query(
            f"SELECT index_id, index_name, unit, one_table, index_table_name, periods, "
            f"city_types, predict_flag, status, scene, accuracy, index_type, add_type, "
            f"default_org, default_period FROM {T_INDEX}",
            (),
        )
        result: list[Indicator] = []
        for row in rows:
            index_id = str(row.get("index_id") or "").strip()
            name = str(row.get("index_name") or "").strip()
            if not index_id or not name:
                continue
            accuracy_raw = row.get("accuracy")
            accuracy = _as_int(accuracy_raw, -1) if accuracy_raw not in (None, "") else None
            result.append(
                Indicator(
                    index_id=index_id,
                    name=name,
                    unit=str(row.get("unit") or "").strip(),
                    one_table=_as_int(row.get("one_table")),
                    index_table_name=str(row.get("index_table_name") or "").strip(),
                    periods=tuple(_as_list(row.get("periods"))),
                    city_types=tuple(_as_int(item) for item in _as_list(row.get("city_types"))),
                    predict_flag=_as_int(row.get("predict_flag")),
                    status=_as_int(row.get("status"), 1),
                    scene=str(row.get("scene") or "").strip(),
                    accuracy=accuracy if accuracy is not None and accuracy >= 0 else None,
                    index_type=_as_int(row.get("index_type")),
                    add_type=_as_int(row.get("add_type"), 1),
                    default_org=str(row.get("default_org") or "").strip(),
                    default_period=str(row.get("default_period") or "").strip(),
                )
            )
        return result

    def _load_orgs(self) -> list[Org]:
        rows = self._query(
            f"SELECT org_code, org_name, org_short_name, type, province, city, county, "
            f"parent_code FROM {T_ORG}",
            (),
        )
        result: list[Org] = []
        for row in rows:
            code = str(row.get("org_code") or "").strip()
            if not code:
                continue
            result.append(
                Org(
                    org_code=code,
                    name=str(row.get("org_name") or "").strip(),
                    short_name=str(row.get("org_short_name") or "").strip(),
                    type=_as_int(row.get("type")),
                    province=str(row.get("province") or "").strip(),
                    city=str(row.get("city") or "").strip(),
                    county=str(row.get("county") or "").strip(),
                    parent_code=str(row.get("parent_code") or "").strip(),
                )
            )
        return result

    def _ensure_dimensions(self) -> None:
        if self._dimensions:
            return
        rows = self._query(f"SELECT wd_id, wd_name, wd_type FROM {T_DIM}", ())
        self._dimensions = [
            Dimension(
                wd_id=str(row.get("wd_id") or "").strip(),
                name=str(row.get("wd_name") or "").strip(),
                wd_type=str(row.get("wd_type") or "").strip(),
            )
            for row in rows
            if str(row.get("wd_id") or "").strip() and str(row.get("wd_name") or "").strip()
        ]
        index_rows = self._query(
            f"SELECT index_name, dim_name, dim_type, wd_id FROM {T_INDEX_DIM}", ()
        )
        grouped: dict[str, list[Dimension]] = {}
        for row in index_rows:
            name = str(row.get("index_name") or "").strip()
            dim_name = str(row.get("dim_name") or "").strip()
            wd_id = str(row.get("wd_id") or "").strip()
            if not name or not dim_name:
                continue
            grouped.setdefault(name, []).append(
                Dimension(
                    wd_id=wd_id,
                    name=dim_name,
                    dim_type=str(row.get("dim_type") or "").strip(),
                )
            )
        self._index_dims = grouped

    # -- 指标 -----------------------------------------------------------

    @property
    def indicators(self) -> list[Indicator]:
        self.refresh()
        return list(self._indicators)

    def get_indicator(self, index_id: str) -> Indicator | None:
        self.refresh()
        return self._indicator_by_id.get(str(index_id or "").strip())

    def indicator_dimensions(self, indicator: Indicator) -> list[Dimension]:
        self.refresh()
        self._ensure_dimensions()
        return list(self._index_dims.get(indicator.name, []))

    def resolve_indicators(
        self,
        text: str,
        *,
        limit: int = 8,
        allow_inactive: bool = False,
        prefer_prediction: bool | None = None,
    ) -> list[IndicatorHit]:
        self.refresh()
        query = normalize_text(text)
        if not query:
            return []
        if prefer_prediction is None:
            prefer_prediction = any(word in str(text) for word in ("预测", "预估", "展望"))
        def _score(indicator: Indicator, name: str, base: float, kind: str) -> IndicatorHit | None:
            if (
                not allow_inactive
                and indicator.status != 1
                and not (prefer_prediction and indicator.is_prediction)
            ):
                # 预测指标允许 inactive（库里有 PRE- 记录）
                return None
            score = base
            if indicator.scene:
                score -= 0.05
            if indicator.is_prediction and not prefer_prediction:
                score -= 0.25
            if prefer_prediction and not indicator.is_prediction:
                score -= 0.1
            if score <= 0:
                return None
            return IndicatorHit(indicator=indicator, score=score, match_type=kind)

        hits: list[IndicatorHit] = []
        for indicator in self._indicators:
            if normalize_text(indicator.name) == query:
                hit = _score(indicator, indicator.name, 1.0, "exact")
                if hit:
                    hits.append(hit)

        # 别名
        for row in self._synonyms:
            sim_name = normalize_text(str(row.get("sim_name") or ""))
            if not sim_name or sim_name != query:
                continue
            index_id = str(row.get("index_id") or "").strip()
            indicator = self._indicator_by_id.get(index_id)
            if indicator is None:
                continue
            priority = _as_int(row.get("priority"), 100)
            hit = _score(indicator, indicator.name, 0.98 - min(priority, 99) / 10_000, "synonym")
            if hit:
                hits.append(hit)

        # 复合地市：福州售电量 → org_hint=福州 + 售电量（在 contains 之前处理，避免丢失机构）
        if not hits:
            compound = self._split_compound_city(query, limit=limit, allow_inactive=allow_inactive, prefer_prediction=prefer_prediction)
            if compound:
                return compound

        if not hits:
            for indicator in self._indicators:
                name = normalize_text(indicator.name)
                if not name:
                    continue
                if query in name or name in query:
                    base = 0.9 if query in name else 0.86
                    hit = _score(indicator, indicator.name, base, "contains")
                    if hit:
                        hits.append(hit)

        if not hits:
            candidates = [
                (indicator, normalize_text(indicator.name))
                for indicator in self._indicators
                if indicator.status == 1 or (prefer_prediction and indicator.is_prediction)
            ]
            for indicator, name in candidates:
                if not name:
                    continue
                ratio = difflib.SequenceMatcher(None, query, name).ratio()
                if ratio >= 0.72:
                    hit = _score(indicator, indicator.name, ratio * 0.8, "fuzzy")
                    if hit:
                        hits.append(hit)

        # 去重（同 index_id 取最高分）
        best: dict[str, IndicatorHit] = {}
        for hit in hits:
            current = best.get(hit.indicator.index_id)
            if current is None or hit.score > current.score:
                best[hit.indicator.index_id] = hit
        ordered = sorted(best.values(), key=lambda item: item.score, reverse=True)

        # 维度提示：工业售电量 → 指标=售电量 + dimension_hint=工业
        for item in ordered:
            dims = self.indicator_dimensions(item.indicator)
            for dim in sorted(dims, key=lambda d: len(d.name), reverse=True):
                if dim.name and len(dim.name) >= 2 and dim.name in query and dim.name != item.indicator.name:
                    item.dimension_hint = dim.name
                    break
        return ordered[:limit]

    def _split_compound_city(
        self,
        query: str,
        *,
        limit: int,
        allow_inactive: bool,
        prefer_prediction: bool | None,
    ) -> list[IndicatorHit]:
        for org in self._orgs:
            if org.type not in (3002, 3003):
                continue
            for token in (org.short_name, org.city, org.county):
                token = normalize_text(token)
                if not token or not query.startswith(token):
                    continue
                remainder = query[len(token) :]
                if len(remainder) < 2:
                    continue
                nested = self.resolve_indicators(
                    remainder,
                    limit=limit,
                    allow_inactive=allow_inactive,
                    prefer_prediction=prefer_prediction,
                )
                if nested:
                    for item in nested:
                        item.org_hint = token
                        item.score *= 0.95
                    return nested[:limit]
        return []

    # -- 机构 -----------------------------------------------------------

    @property
    def orgs(self) -> list[Org]:
        self.refresh()
        return list(self._orgs)

    def get_org(self, org_code: str) -> Org | None:
        self.refresh()
        return next((org for org in self._orgs if org.org_code == str(org_code)), None)

    def cities(self) -> list[Org]:
        self.refresh()
        return sorted(
            [org for org in self._orgs if org.parent_code == PROVINCE_ORG_ID and org.type == 3002],
            key=lambda item: item.label,
        )

    def counties(self, city: str = "") -> list[Org]:
        self.refresh()
        target = str(city or "").strip()
        return [
            org
            for org in self._orgs
            if org.type == 3003 and (not target or target in {org.city, org.county, org.short_name})
        ]

    def resolve_orgs(self, text: str, *, limit: int = 5) -> list[OrgHit]:
        self.refresh()
        query = normalize_text(text)
        if not query:
            return []
        if query in {"福建", "全省", "福建省"}:
            province = next((org for org in self._orgs if org.org_code == PROVINCE_ORG_ID), None)
            return [OrgHit(province, 1.0, "province")] if province else []

        hits: list[OrgHit] = []
        for org in self._orgs:
            names = [normalize_text(org.short_name), normalize_text(org.name)]
            score = 0.0
            kind = ""
            if query in names:
                score, kind = 1.0, "exact"
            elif any(name and (query in name or name in query) for name in names):
                score, kind = 0.9, "contains"
            elif org.type == 3001 and query in {normalize_text(org.province)}:
                score, kind = 0.95, "province"
            elif org.type == 3002 and query in {normalize_text(org.city)}:
                score, kind = 0.92, "city"
            elif org.type == 3003 and query in {normalize_text(org.county), normalize_text(org.short_name)}:
                score, kind = 0.92, "county"
            if score:
                hits.append(OrgHit(org, score, kind))
        hits.sort(key=lambda item: (item.score, -item.org.type), reverse=True)
        # 去重（同一机构编码）
        seen: set[str] = set()
        result: list[OrgHit] = []
        for hit in hits:
            if hit.org.org_code in seen:
                continue
            seen.add(hit.org.org_code)
            result.append(hit)
        return result[:limit]

    # -- 维度 -----------------------------------------------------------

    def resolve_dimensions(self, indicator: Indicator, text: str, *, limit: int = 8) -> list[DimensionHit]:
        self.refresh()
        self._ensure_dimensions()
        query = normalize_text(text)
        if not query:
            return []
        candidates = self.indicator_dimensions(indicator)
        if not candidates:
            candidates = [
                Dimension(wd_id=item.wd_id, name=item.name, wd_type=item.wd_type)
                for item in self._dimensions
            ]
        hits: list[DimensionHit] = []
        for dim in candidates:
            name = normalize_text(dim.name)
            if not name:
                continue
            score = 0.0
            kind = ""
            if query == name:
                score, kind = 1.0, "exact"
            elif query in name or name in query:
                score, kind = 0.88, "contains"
            else:
                ratio = difflib.SequenceMatcher(None, query, name).ratio()
                if ratio >= 0.75:
                    score, kind = ratio * 0.8, "fuzzy"
            if score:
                hits.append(DimensionHit(dim, score, kind))
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    # -- 可用性 ---------------------------------------------------------

    def availability(self, index_id: str) -> list[Availability]:
        self.refresh()
        key = str(index_id or "").strip()
        if key in self._availability:
            return list(self._availability[key])
        rows = self._query(
            f"SELECT table_name, period, dim_val, orgs FROM {T_DATA_STRUCT} WHERE index_id = %s",
            (key,),
        )
        result: list[Availability] = []
        for row in rows:
            table = str(row.get("table_name") or "").strip()
            if not table:
                continue
            dim_text = str(row.get("dim_val") or "").strip()
            result.append(
                Availability(
                    table_name=table,
                    periods=set(_as_list(row.get("period"))),
                    orgs=set(_as_list(row.get("orgs"))),
                    dims_available=dim_text.lower() not in {"", "null", "none"},
                )
            )
        self._availability[key] = result
        return list(result)

    def index_system(self) -> list[dict[str, Any]]:
        self.refresh()
        return list(self._index_system)

    def index_config(self, name: str) -> dict[str, Any]:
        self.refresh()
        return dict(self._index_config.get(str(name or "").strip(), {}))

    def scenes(self) -> list[dict[str, Any]]:
        self.refresh()
        return list(self._scenes)

    # -- 数据边界 -------------------------------------------------------

    def boundary_summary(self) -> str:
        now = time.monotonic()
        cached_at, cached = self._boundary_cache
        if cached and now - cached_at < 600:
            return cached
        parts: list[str] = []
        for name, index_id in BOUNDARY_INDICATORS:
            latest = ""
            for table in LEVEL_TABLES:
                if not self.client.table_exists(table):
                    continue
                row = self._query(
                    f"SELECT MAX(stat_date) AS latest FROM {table} WHERE index_id = %s",
                    (index_id,),
                )
                value = row[0].get("latest") if row else None
                if value:
                    latest = str(value)
                    break
            # 部分指标（如发电量）在专用表里，回退到可用性表。
            if not latest:
                for avail in self.availability(index_id):
                    if not self.client.table_exists(avail.table_name):
                        continue
                    row = self._query(
                        f"SELECT MAX(stat_date) AS latest FROM {avail.table_name} WHERE index_id = %s",
                        (index_id,),
                    )
                    value = row[0].get("latest") if row else None
                    if value:
                        latest = str(value)
                        break
            if latest:
                parts.append(f"{name} 最新 {latest}")
        summary = "；".join(parts)
        self._boundary_cache = (now, summary)
        return summary


__all__ = [
    "Availability",
    "BusinessCatalog",
    "Dimension",
    "DimensionHit",
    "Indicator",
    "IndicatorHit",
    "Org",
    "OrgHit",
    "normalize_text",
]
