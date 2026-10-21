"""真实业务库 smoke test（默认跳过）。

运行：
    QAAPP_TEST_DB=1 DB_HOST=... DB_PORT=... DB_USER=... DB_PASSWORD=... DB_NAME=... \
      python -m pytest tests/test_qaapp/test_db_smoke.py -q
"""

from __future__ import annotations

import os

import pytest

from qaapp.data.catalog import BusinessCatalog
from qaapp.data.datasources import MySQLConfig
from qaapp.data.mysql import MySqlClient
from qaapp.data.query import BusinessQueryService

pytestmark = pytest.mark.skipif(
    not os.getenv("QAAPP_TEST_DB"), reason="set QAAPP_TEST_DB=1 to run real MySQL smoke tests"
)


def _service() -> tuple[BusinessQueryService, MySqlClient]:
    config = MySQLConfig(
        enabled=True,
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""),
        database=os.environ["DB_NAME"],
    )
    client = MySqlClient(config)
    return BusinessQueryService(client, BusinessCatalog(client, config), config), client


def test_xiamen_sales_2026_09():
    service, client = _service()
    try:
        result = service.query(
            indicators=["售电量"], org="厦门", period="月", start="2026-09", end="2026-09"
        )
        assert result["status"] == "ok"
        row = result["data"]["rows"][0]
        assert round(float(row["val"]), 4) == 9.8389
        assert round(float(row["val_tb"]), 2) == -2.48
    finally:
        client.close()


def test_fujian_primary_industry_2026_09():
    service, client = _service()
    try:
        result = service.query(
            indicators=["售电量"],
            org="福建",
            period="月",
            start="2026-09",
            end="2026-09",
            dimensions=["第一产业"],
        )
        assert result["status"] == "ok"
        row = result["data"]["rows"][0]
        assert round(float(row["val"]), 4) == 1.1044
        assert round(float(row["val_tb"]), 2) == -13.85
    finally:
        client.close()


def test_recovery_no_data_reports_latest():
    service, client = _service()
    try:
        result = service.query(
            indicators=["电费回收率"], org="福建", period="月", start="2026-09", end="2026-09"
        )
        assert result["status"] == "no_data"
        assert "2026-06" in result["summary"]
    finally:
        client.close()


def test_sales_outline_has_scenario_sections():
    service, client = _service()
    try:
        result = service.outline(
            theme="2026年9月福建售电量分析",
            scenario="sales",
            org="福建",
            period="月",
            start="2026-09",
            end="2026-09",
        )
        assert result["status"] == "ok"
        assert len(result["sections"]) >= 5
        assert any(item["name"] == "售电量" for item in result["indicators"])
    finally:
        client.close()
