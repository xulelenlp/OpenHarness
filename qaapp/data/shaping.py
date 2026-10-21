"""查询结果整形、维度拆分与问题候选。"""

from __future__ import annotations

from typing import Any

from qaapp.data.catalog import Dimension
from qaapp.data.formatting import _rows_jsonable
from qaapp.data.periods import _as_date
from qaapp.data.resolver import ResolvedRequest
from qaapp.data.schema import DATA_COLUMNS


class ShapingMixin:
    """Shape raw rows for output and derive analysis hints."""

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
