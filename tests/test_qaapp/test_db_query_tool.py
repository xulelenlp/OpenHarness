"""db_query 工具层测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.tools.base import ToolExecutionContext
from qaapp.tools.db_query import DbQueryInput, DbQueryTool
from qaapp.tools.script import CONVERSATION_DIR_KEY


class FakeService:
    def query(self, **kwargs):
        return {
            "status": "ok",
            "summary": "查询完成：1 行。",
            "data": {
                "columns": ["stat_date", "org_id", "index_name", "unit", "val", "val_tb", "val_hb"],
                "rows": [
                    {
                        "stat_date": "2026-09-30",
                        "org_id": "210000000000128",
                        "index_name": "售电量",
                        "unit": "亿千瓦时",
                        "val": 9.8389,
                        "val_tb": -2.48,
                        "val_hb": -6.49,
                    }
                ],
            },
            "data_quality": [{"severity": "warn", "message": "示例告警"}],
            "artifact": {"path": "outputs/data/x.csv"},
            "diagnostics": {"latest_available": ["售电量/厦门: 2026-09-30"]},
        }


@pytest.mark.asyncio
async def test_no_service_returns_helpful_error(tmp_path: Path):
    tool = DbQueryTool(None)
    result = await tool.execute(
        DbQueryInput(indicators=["售电量"]),
        ToolExecutionContext(cwd=tmp_path, metadata={CONVERSATION_DIR_KEY: str(tmp_path)}),
    )
    assert result.is_error is True
    assert "datasources.json" in result.output


@pytest.mark.asyncio
async def test_query_output_contains_metrics_and_quality(tmp_path: Path):
    tool = DbQueryTool(FakeService())
    result = await tool.execute(
        DbQueryInput(indicators=["售电量"], org="厦门", start="2026-09", end="2026-09"),
        ToolExecutionContext(cwd=tmp_path, metadata={CONVERSATION_DIR_KEY: str(tmp_path)}),
    )
    assert result.is_error is False
    assert "9.8389" in result.output
    assert "-2.48" in result.output
    assert "示例告警" in result.output
    assert "outputs/data/x.csv" in result.output


class FakeDescribeService:
    def describe(self, **kwargs):
        return {
            "status": "ok",
            "summary": "指标：售电量",
            "indicators": [
                {
                    "name": "售电量",
                    "index_id": "IDX-1",
                    "unit": "亿千瓦时",
                    "periods": ["月", "年"],
                    "latest_available": "2026-09-30",
                    "definition": {"index_define": "统计期内售出的电量"},
                    "dimensions": [{"name": "产业"}],
                }
            ],
            "candidates": [
                {
                    "name": "售电量",
                    "index_id": "IDX-1",
                    "score": 0.91,
                    "match_type": "exact",
                },
                {
                    "name": "全社会用电量",
                    "index_id": "IDX-2",
                    "score": 0.89,
                    "match_type": "fuzzy",
                },
            ],
            "needs_confirmation": True,
            "notes": ["按“售电量”匹配到多个候选"],
        }


@pytest.mark.asyncio
async def test_describe_surfaces_candidates_for_confirmation(tmp_path: Path):
    tool = DbQueryTool(FakeDescribeService())
    result = await tool.execute(
        DbQueryInput(operation="describe", indicators=["售电量"]),
        ToolExecutionContext(cwd=tmp_path, metadata={CONVERSATION_DIR_KEY: str(tmp_path)}),
    )
    assert result.is_error is False
    assert "多个候选" in result.output
    assert "ask_user_question" in result.output
    assert "全社会用电量" in result.output
    assert "备注" in result.output
    assert "亿千瓦时" in result.output
