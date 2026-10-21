"""``db_query``：业务库结构化查询工具。

设计要点：
- 只读；所有 SQL 由 :class:`~qaapp.data.query.BusinessQueryService` 生成/校验；
- 支持指标解析、描述、场景大纲、查询、EDA、新闻、图表、受控 SQL；
- 完整结果落 ``outputs/data``，模型只看摘要 + 预览，避免上下文膨胀。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from qaapp.data.charting import render_chart
from qaapp.data.query import BusinessQueryService
from qaapp.tools.script import CONVERSATION_DIR_KEY

Scenario = Literal[
    "business",
    "sales",
    "recovery",
    "service",
    "grid",
    "asset",
    "new_energy",
    "region",
    "forecast",
    "custom",
]


class DbQueryInput(BaseModel):
    """``db_query`` 入参。"""

    operation: Literal["resolve", "describe", "outline", "query", "explore", "news", "chart", "forecast", "sql"] = Field(
        default="query",
        description=(
            "操作类型：resolve=解析指标/机构/维度；describe=指标元数据；outline=生成分析大纲；"
            "query=取数；explore=EDA（趋势/地市/维度/问题候选）；news=政策/事件；"
            "chart=生成图表；forecast=Redis 预测/特征缓存；sql=受控只读 SQL（默认关闭）。"
        ),
    )
    scenario: Scenario = Field(
        default="business",
        description="业务场景：business/sales/recovery/service/grid/asset/new_energy/region/forecast/custom。",
    )
    indicators: list[str] = Field(default_factory=list, description="指标名称/别名，如 ['售电量']。")
    index_ids: list[str] = Field(default_factory=list, description="已知指标 ID（优先于名称）。")
    theme: str = Field(default="", description="分析主题（outline 用），如 '2026年9月福建售电量分析'。")
    org: str = Field(default="福建", description="机构名称：福建/厦门/思明区/某供电所。")
    org_level: Literal["auto", "province", "city", "county", "station", "all_cities", "all_counties"] = Field(
        default="auto", description="机构级别；all_cities=九地市，all_counties=区县。"
    )
    period: Literal["日", "周", "月", "季", "年"] = Field(default="月", description="统计周期。")
    start: str = Field(default="", description="开始时间：YYYY-MM 或 YYYY-MM-DD；留空=最近 12 个周期。")
    end: str = Field(default="", description="结束时间：YYYY-MM 或 YYYY-MM-DD；留空=上一个完整周期。")
    dimensions: list[str] = Field(default_factory=list, description="维度名称，如 ['第一产业','第二产业']。")
    group_by: Literal["date", "org", "dimension", "none"] = Field(
        default="none", description="结果分组：date=时间序列，org=机构对比，dimension=维度拆分。"
    )
    aggregate: Literal["auto", "none", "sum", "avg", "latest"] = Field(
        default="auto", description="分组聚合方式；auto 按时点/累计/可加指标自动判断。"
    )
    metrics: list[str] = Field(
        default_factory=lambda: ["val", "val_tb", "val_hb"],
        description="返回指标列：val/val_tb/val_hb/val_lj/val_tq/val_jh/val_mb/val_wcl/val_pm 等。",
    )
    limit: int = Field(default=200, ge=1, le=1000, description="返回行数上限。")
    keywords: list[str] = Field(default_factory=list, description="news 操作的关键词。")
    chart_type: Literal["line", "bar", "pie"] = Field(default="line", description="chart 类型。")
    title: str = Field(default="", description="图表/报告标题。")
    sql: str = Field(default="", description="operation=sql 时的只读 SQL（需显式开启）。")
    save: bool = Field(default=True, description="是否把完整结果写入 outputs/data。")


class DbQueryTool(BaseTool):
    """查询客户业务库（只读）。"""

    name = "db_query"
    description = (
        "查询客户业务库（MySQL，只读）的指标数据。**任何业务数字/明细/趋势/对比都应先用本工具取数**，"
        "不要凭记忆或让外部智能体代查。\n"
        "常用：operation=query（单/多指标取数）、explore（EDA：趋势/九地市/维度/问题候选）、"
        "outline（按业务场景生成分析大纲）、describe（指标口径/最新日期/维度）、"
        "news（政策/企业事件）、chart（生成图表）、forecast（Redis 预测/特征缓存）、"
        "resolve（指标/机构/维度解析）。\n"
        "会自动处理：指标别名、A1/A2/B1/B2 表路由、月末日期、周期回退、维度回退、无数据诊断、数据质量检查。"
        "返回摘要 + 少量预览；完整数据在 outputs/data/*.csv。"
    )
    input_model = DbQueryInput

    def __init__(self, service: BusinessQueryService | None) -> None:
        self.service = service

    def is_read_only(self, arguments: DbQueryInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: DbQueryInput, context: ToolExecutionContext) -> ToolResult:
        service = self.service
        if service is None:
            return ToolResult(
                output=(
                    "db_query 不可用：未配置 datasources.json（MySQL 未启用）。"
                    "请配置 ~/.ohmo/datasources.json 或 config/datasources.json，并运行 abcqa datasource check。"
                ),
                is_error=True,
            )
        artifact_dir = self._artifact_dir(context, "data") if arguments.save else None
        try:
            if arguments.operation == "resolve":
                result = await asyncio.to_thread(
                    service.resolve,
                    indicators=arguments.indicators,
                    index_ids=arguments.index_ids,
                    org=arguments.org,
                    dimensions=arguments.dimensions,
                    period=arguments.period,
                )
            elif arguments.operation == "describe":
                result = await asyncio.to_thread(
                    service.describe,
                    indicators=arguments.indicators,
                    index_ids=arguments.index_ids,
                )
            elif arguments.operation == "outline":
                result = await asyncio.to_thread(
                    service.outline,
                    theme=arguments.theme or "、".join(arguments.indicators),
                    scenario=arguments.scenario,
                    org=arguments.org,
                    period=arguments.period,
                    start=arguments.start,
                    end=arguments.end,
                    indicators=arguments.indicators,
                )
            elif arguments.operation == "explore":
                result = await asyncio.to_thread(
                    service.explore,
                    indicators=arguments.indicators,
                    index_ids=arguments.index_ids,
                    org=arguments.org,
                    period=arguments.period,
                    start=arguments.start,
                    end=arguments.end,
                    dimensions=arguments.dimensions,
                    limit=arguments.limit,
                    artifact_dir=artifact_dir,
                )
            elif arguments.operation == "news":
                result = await asyncio.to_thread(
                    service.news,
                    keywords=arguments.keywords or arguments.indicators,
                    start=arguments.start,
                    end=arguments.end,
                    limit=arguments.limit,
                )
            elif arguments.operation == "forecast":
                area_map = {
                    "福建": "fj",
                    "福州": "fz",
                    "厦门": "xm",
                    "漳州": "zz",
                    "泉州": "qz",
                    "龙岩": "ly",
                    "三明": "sm",
                    "南平": "np",
                    "宁德": "nd",
                    "莆田": "pt",
                }
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            service.forecast,
                            date=arguments.start or arguments.end,
                            area=area_map.get(str(arguments.org or "").strip(), "fj"),
                        ),
                        timeout=15.0,
                    )
                except TimeoutError:
                    result = {
                        "status": "unavailable",
                        "summary": "Redis 预测缓存查询超时；请改用业务库预测指标或实际值。",
                    }
            elif arguments.operation == "sql":
                result = await asyncio.to_thread(service.sql, arguments.sql)
            elif arguments.operation == "chart":
                result = await asyncio.to_thread(
                    service.query,
                    indicators=arguments.indicators,
                    index_ids=arguments.index_ids,
                    org=arguments.org,
                    org_level=arguments.org_level,
                    period=arguments.period,
                    start=arguments.start,
                    end=arguments.end,
                    dimensions=arguments.dimensions,
                    group_by=arguments.group_by if arguments.group_by != "none" else "date",
                    aggregate=arguments.aggregate,
                    metrics=arguments.metrics,
                    limit=arguments.limit,
                    artifact_dir=artifact_dir,
                )
                if result.get("status") not in {"error", "no_data"}:
                    chart_dir = self._artifact_dir(context, "charts")
                    chart = await asyncio.to_thread(
                        render_chart,
                        result.get("data", {}).get("rows", []),
                        chart_type=arguments.chart_type,
                        title=arguments.title or "数据图表",
                        output_dir=chart_dir,
                    )
                    result["chart"] = chart
            else:
                result = await asyncio.to_thread(
                    service.query,
                    indicators=arguments.indicators,
                    index_ids=arguments.index_ids,
                    org=arguments.org,
                    org_level=arguments.org_level,
                    period=arguments.period,
                    start=arguments.start,
                    end=arguments.end,
                    dimensions=arguments.dimensions,
                    group_by=arguments.group_by,
                    aggregate=arguments.aggregate,
                    metrics=arguments.metrics,
                    limit=arguments.limit,
                    artifact_dir=artifact_dir,
                )
        except Exception as exc:  # noqa: BLE001 - 工具层兜底，避免异常穿透到 SSE
            return ToolResult(output=f"db_query 执行失败：{type(exc).__name__}: {exc}", is_error=True)

        text = self._format(arguments, result)
        return ToolResult(
            output=text,
            is_error=result.get("status") == "error",
            metadata={
                "status": result.get("status"),
                "artifact": (result.get("artifact") or {}).get("path", ""),
                "chart": (result.get("chart") or {}).get("path", ""),
            },
        )

    @staticmethod
    def _artifact_dir(context: ToolExecutionContext, kind: str) -> Path | None:
        raw = context.metadata.get(CONVERSATION_DIR_KEY)
        if not raw:
            return None
        return Path(str(raw)) / "outputs" / kind

    def _format(self, arguments: DbQueryInput, result: dict[str, Any]) -> str:
        lines = [f"## db_query · {arguments.operation}", ""]
        summary = str(result.get("summary") or "").strip()
        if summary:
            lines.extend([summary, ""])
        status = result.get("status")
        if status == "error":
            lines.append("请根据错误提示调整参数；不要编造数据。")
            return "\n".join(lines)

        if arguments.operation == "resolve":
            resolved = result.get("resolved") or {}
            for item in resolved.get("indicators", []):
                lines.append(f"- 指标：{item.get('name')}（{item.get('index_id')}，{item.get('unit')}）")
            for item in resolved.get("orgs", []):
                lines.append(f"- 机构：{item.get('name')}（{item.get('org_code')}，type={item.get('type')}）")
            for dims in (resolved.get("dimensions") or {}).values():
                if dims:
                    lines.append(f"- 维度：{'、'.join(dim.get('name', '') for dim in dims)}")
            if result.get("needs_confirmation"):
                lines.append("- ⚠️ 指标存在多个候选，请先向用户确认。")
            return "\n".join(lines)

        if arguments.operation == "describe":
            for item in result.get("indicators", []):
                lines.append(f"### {item.get('name')}（{item.get('index_id')}）")
                lines.append(
                    f"- 单位：{item.get('unit')}；周期：{'、'.join(item.get('periods') or [])}；"
                    f"最新可用：{item.get('latest_available') or '未探明'}"
                )
                definition = item.get("definition") or {}
                if definition.get("index_define"):
                    lines.append(f"- 定义：{definition.get('index_define')}")
                if definition.get("cal_method"):
                    lines.append(f"- 计算方法：{definition.get('cal_method')}")
                dims = item.get("dimensions") or []
                if dims:
                    lines.append("- 可用维度：" + "、".join(dim.get("name", "") for dim in dims[:15]))
            return "\n".join(lines)

        if arguments.operation == "outline":
            lines.append(f"场景：{(result.get('scenario') or {}).get('name', arguments.scenario)}")
            lines.append("")
            for item in result.get("indicators", []):
                lines.append(
                    f"- {item.get('name')}（{item.get('unit')}）：最新 {item.get('latest_available') or '未探明'}；"
                    f"维度 {len(item.get('dimensions') or [])} 个"
                )
            lines.append("")
            lines.append("### 建议章节")
            for section in result.get("sections", []):
                lines.append(f"- {section.get('heading')}：{section.get('objective')}")
            if result.get("gaps"):
                lines.append("")
                lines.append("### 数据缺口")
                for gap in result.get("gaps", []):
                    lines.append(f"- {gap.get('indicator')}：{gap.get('reason')}")
            lines.append("")
            lines.append(
                "最低要求：3+ 指标、3+ 维度、2+ 对比基准、2–3 个问题点、2+ 条成因、3–5 条建议；"
                "数据质量必须披露。"
            )
            lines.append("请先向用户确认大纲，再进入 EDA；确认前不要开始大段分析。")
            return "\n".join(lines)

        if arguments.operation == "news":
            for item in result.get("items", []):
                lines.append(f"- {item.get('date')}｜{item.get('title')}｜{item.get('source')}")
                if item.get("summary"):
                    lines.append(f"  {str(item.get('summary'))[:300]}")
            return "\n".join(lines)

        if arguments.operation == "explore":
            trend = result.get("trend") or {}
            rows = trend.get("rows") or []
            lines.append("### 趋势（最近若干期）")
            lines.extend(self._table(rows, trend.get("columns") or [], max_rows=12))
            problems = result.get("problem_candidates") or []
            if problems:
                lines.append("")
                lines.append("### 问题候选")
                for item in problems[:8]:
                    lines.append(f"- [{item.get('severity')}] {item.get('title')}：{json.dumps(item.get('evidence'), ensure_ascii=False)}")
            orgs = result.get("org_compare") or []
            if orgs:
                lines.append("")
                lines.append("### 九地市对比（预览）")
                lines.extend(self._table(orgs, list(orgs[0].keys()) if orgs else [], max_rows=9))
            dims = result.get("dimension_breakdown") or []
            if dims:
                lines.append("")
                lines.append("### 维度拆分 Top（预览）")
                lines.extend(self._table(dims[:10], list(dims[0].keys()) if dims else [], max_rows=10))
            if result.get("data_quality"):
                lines.append("")
                lines.append("### 数据质量")
                for item in result["data_quality"]:
                    lines.append(f"- [{item.get('severity')}] {item.get('message')}")
            artifact = (result.get("artifact") or {}).get("path")
            if artifact:
                lines.append("")
                lines.append(f"完整数据：{artifact}")
            return "\n".join(lines)

        data = result.get("data") or {}
        rows = data.get("rows") or []
        columns = data.get("columns") or []
        if rows:
            lines.append("### 数据预览")
            lines.extend(self._table(rows, columns, max_rows=30))
        if result.get("data_quality"):
            lines.append("")
            lines.append("### 数据质量")
            for item in result["data_quality"]:
                lines.append(f"- [{item.get('severity')}] {item.get('message')}")
        artifact = (result.get("artifact") or {}).get("path")
        if artifact:
            lines.append("")
            lines.append(f"完整数据：{artifact}")
        chart = result.get("chart") or {}
        if chart.get("path"):
            lines.append(f"图表：{chart.get('path')}")
        elif chart.get("message"):
            lines.append(f"图表未生成：{chart.get('message')}")
        diagnostics = result.get("diagnostics") or {}
        if diagnostics.get("latest_available"):
            lines.append("")
            lines.append("数据边界：" + "；".join(str(item) for item in diagnostics["latest_available"][:5]))
        return "\n".join(lines)

    @staticmethod
    def _table(rows: list[dict[str, Any]], columns: list[str], *, max_rows: int = 20) -> list[str]:
        if not rows:
            return ["（无数据）"]
        columns = [column for column in columns if any(column in row for row in rows)] or list(rows[0])
        columns = columns[:8]
        lines = [
            "| " + " | ".join(columns) + " |",
            "|" + "|".join("---" for _ in columns) + "|",
        ]
        for row in rows[:max_rows]:
            values = []
            for column in columns:
                value = row.get(column)
                text = "" if value is None else str(value)
                values.append(text.replace("|", "/").replace("\n", " ")[:60])
            lines.append("| " + " | ".join(values) + " |")
        if len(rows) > max_rows:
            lines.append(f"| …（共 {len(rows)} 行，已截断） |" + " |" * (len(columns) - 1))
        return lines


__all__ = ["DbQueryInput", "DbQueryTool"]
