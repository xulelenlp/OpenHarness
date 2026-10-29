"""业务查询路由/质量/安全测试（使用假客户端，不连真库）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from qaapp.agents.scenarios import SCENARIOS, get_scenario, match_scenario
from qaapp.data.catalog import (
    Availability,
    Dimension,
    DimensionHit,
    Indicator,
    IndicatorHit,
    Org,
    OrgHit,
)
from qaapp.data.datasources import MySQLConfig
from qaapp.data.mysql import QueryResult, SqlGuard, SqlSafetyError
from qaapp.data.periods import _month_end, _month_start
from qaapp.data.quality import check_quality
from qaapp.data.query import BusinessQueryService

INDICATOR = Indicator(
    index_id="IDX-1006-00010001",
    name="售电量",
    unit="亿千瓦时",
    one_table=0,
    index_table_name="zhss_result_data_khydl",
    periods=("日", "周", "月", "年"),
    city_types=(3001, 3002),
    status=1,
)
PROVINCE = Org("210000000000127", "福建", "福建", 3001, province="福建")
XIAMEN = Org("210000000000128", "国网厦门供电公司", "厦门", 3002, province="福建", city="厦门", parent_code="210000000000127")


class FakeCatalog:
    def refresh(self, force: bool = False) -> None:
        return None

    @property
    def indicators(self):
        return [INDICATOR]

    def get_indicator(self, index_id: str):
        return INDICATOR if index_id == INDICATOR.index_id else None

    def resolve_indicators(self, text: str, **kwargs):
        if "福州" in text:
            return [IndicatorHit(INDICATOR, 0.9, "compound", org_hint="福州")]
        return [IndicatorHit(INDICATOR, 1.0, "exact")]

    def resolve_orgs(self, text: str, **kwargs):
        if "厦门" in text:
            return [OrgHit(XIAMEN, 1.0, "exact")]
        return [OrgHit(PROVINCE, 1.0, "province")]

    def cities(self):
        return [XIAMEN, PROVINCE]

    def counties(self, city: str = ""):
        return []

    def get_org(self, org_code: str):
        return XIAMEN if org_code == XIAMEN.org_code else PROVINCE

    def availability(self, index_id: str):
        return [Availability("zhss_result_data_qs", {"月"}, {PROVINCE.org_code}, False)]

    def indicator_dimensions(self, indicator: Indicator):
        return [Dimension("10010101", "第一产业"), Dimension("10010102", "第二产业")]

    def resolve_dimensions(self, indicator: Indicator, text: str, **kwargs):
        if "第一产业" in text:
            return [DimensionHit(Dimension("10010101", "第一产业"), 1.0, "exact")]
        return []

    def index_system(self):
        return [{"index_id": INDICATOR.index_id, "index_name": INDICATOR.name, "scene": "数说经营", "is_main": 1}]

    def index_config(self, name: str):
        return {}

    def boundary_summary(self):
        return "售电量 最新 2026-09-30"


class FakeClient:
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []
        self.data_rows = [
            {
                "index_id": INDICATOR.index_id,
                "index_name": INDICATOR.name,
                "stat_date": date(2026, 9, 30),
                "period": "月",
                "org_id": PROVINCE.org_code,
                "dim_val": None,
                "unit": "亿千瓦时",
                "val": Decimal("64.9459"),
                "val_tb": Decimal("-6.31"),
                "val_hb": Decimal("-9.17"),
            }
        ]

    def table_exists(self, table: str) -> bool:
        return True

    def fetch_all(self, sql: str, params=None, max_rows=None) -> QueryResult:
        self.queries.append((sql, tuple(params or ())))
        if "MAX(stat_date)" in sql:
            return QueryResult(rows=[{"latest": date(2026, 9, 30)}], columns=["latest"], sql=sql)
        if "FROM zhss_result_data_qs" in sql:
            return QueryResult(rows=list(self.data_rows), columns=list(self.data_rows[0]), sql=sql)
        return QueryResult(rows=[], columns=[], sql=sql)

    def execute_raw(self, sql: str) -> QueryResult:
        return QueryResult(rows=[{"x": 1}], columns=["x"], sql=sql)


def _config() -> MySQLConfig:
    return MySQLConfig(enabled=True, host="localhost", user="u", database="d")


def _service() -> tuple[BusinessQueryService, FakeClient]:
    client = FakeClient()
    return BusinessQueryService(client, FakeCatalog(), _config()), client


class AmbiguousCatalog(FakeCatalog):
    def resolve_indicators(self, text: str, **kwargs):
        other = Indicator(
            index_id="IDX-2006-00010001",
            name="全社会用电量",
            unit="亿千瓦时",
            periods=("月", "年"),
            city_types=(3001, 3002),
        )
        return [
            IndicatorHit(INDICATOR, 0.9, "exact"),
            IndicatorHit(other, 0.89, "fuzzy"),
        ]


def test_describe_returns_confirmation_metadata():
    service, _client = _service()
    result = service.describe(indicators=["售电量"])
    assert result["status"] == "ok"
    assert result["candidates"]
    assert result["needs_confirmation"] is False
    assert result["notes"] == []
    assert result["indicators"][0]["unit"] == "亿千瓦时"


def test_describe_flags_ambiguous_candidates():
    service = BusinessQueryService(FakeClient(), AmbiguousCatalog(), _config())
    result = service.describe(indicators=["售电量"])
    assert result["needs_confirmation"] is True
    assert [item["name"] for item in result["candidates"]] == ["售电量", "全社会用电量"]


def test_outline_exposes_adaptive_framework():
    service, _client = _service()
    result = service.outline(
        theme="2026年9月售电量分析",
        scenario="sales",
        start="2026-09",
        end="2026-09",
    )
    assert result["status"] == "ok"
    assert result["framework"]["style"] == "趋势归因"
    assert result["framework"]["principle"]
    assert result["framework"]["note"]
    assert result["framework"]["candidate_sections"]
    assert result["sections"][0]["heading"].startswith("1. 核心结论")


def test_month_end_and_start():
    assert _month_end(date(2025, 2, 1)) == date(2025, 2, 28)
    assert _month_end(date(2025, 12, 1)) == date(2025, 12, 31)
    assert _month_start(date(2025, 12, 20)) == date(2025, 12, 1)


def test_query_uses_availability_table():
    service, client = _service()
    result = service.query(
        indicators=["售电量"], org="福建", period="月", start="2026-09", end="2026-09"
    )
    assert result["status"] == "ok"
    assert result["data"]["rows"][0]["val"] == 64.9459
    assert result["data"]["rows"][0]["val_tb"] == -6.31
    assert result["diagnostics"]["tables"] == ["zhss_result_data_qs"]
    assert any("zhss_result_data_qs" in sql for sql, _ in client.queries)


def test_dimension_falls_back_to_total():
    service, client = _service()

    # 维度查询返回空，总量查询返回一行。
    def fetch_all(sql: str, params=None, max_rows=None):
        client.queries.append((sql, tuple(params or ())))
        if "MAX(stat_date)" in sql:
            return QueryResult(rows=[{"latest": date(2026, 9, 30)}], columns=["latest"], sql=sql)
        if "dim_val IN" in sql:
            return QueryResult(rows=[], columns=[], sql=sql)
        return QueryResult(rows=list(client.data_rows), columns=list(client.data_rows[0]), sql=sql)

    client.fetch_all = fetch_all  # type: ignore[method-assign]
    result = service.query(
        indicators=["售电量"], org="福建", period="月", start="2026-09", end="2026-09", dimensions=["第一产业"]
    )
    assert result["status"] == "ok"
    assert "dim_fallback" in result["diagnostics"]["fallback_used"] or any(
        "dim_fallback" in str(item) for item in result["diagnostics"].get("notes", [])
    )


def test_no_data_reports_latest_available():
    service, client = _service()

    def fetch_all(sql: str, params=None, max_rows=None):
        client.queries.append((sql, tuple(params or ())))
        if "MAX(stat_date)" in sql:
            return QueryResult(rows=[{"latest": date(2026, 6, 30)}], columns=["latest"], sql=sql)
        return QueryResult(rows=[], columns=[], sql=sql)

    client.fetch_all = fetch_all  # type: ignore[method-assign]
    result = service.query(
        indicators=["售电量"], org="福建", period="月", start="2026-09", end="2026-09"
    )
    assert result["status"] == "no_data"
    assert "2026-06-30" in result["summary"]
    assert result["diagnostics"]["latest_available"]


def test_constant_series_quality():
    rows = [
        {"stat_date": f"2026-0{i}-28", "period": "月", "org_id": "1", "dim_val": None, "val": 256.0803, "unit": "%"}
        for i in range(1, 5)
    ]
    issues = check_quality(rows, requested_period="月", requested_end="2026-04-30")
    assert any(issue.code == "constant_series" and issue.severity == "high" for issue in issues)


def test_sql_guard_blocks_writes_and_comments():
    guard = SqlGuard(_config())
    with pytest.raises(SqlSafetyError):
        guard.check("DELETE FROM zhss_result_data_qs")
    with pytest.raises(SqlSafetyError):
        guard.check("SELECT * FROM x; DROP TABLE y")
    with pytest.raises(SqlSafetyError):
        guard.check("SELECT * FROM x -- comment")
    with pytest.raises(SqlSafetyError):
        guard.check("SELECT * FROM ai_llm_agent_api")
    assert guard.with_limit("SELECT * FROM zhss_result_data_qs").endswith("LIMIT 1000")


def test_raw_sql_disabled_by_default():
    guard = SqlGuard(_config())
    with pytest.raises(SqlSafetyError):
        guard.check("SELECT * FROM zhss_result_data_qs")


def test_scenarios_cover_ten_business_cases():
    assert len(SCENARIOS) == 10
    assert get_scenario("unknown").key == "business"
    assert match_scenario("分析电费回收和欠费风险").key == "recovery"
    assert match_scenario("2026年9月售电量分析").key == "sales"
