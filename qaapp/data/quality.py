"""业务数据分析的数据质量检查。

目标是让模型“看见”数据问题，而不是把恒定序列/陈旧数据当成趋势。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from qaapp.data.dates import to_date as _to_date


@dataclass
class QualityIssue:
    code: str
    severity: str  # info | warn | high
    message: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _val_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(key) or "")
        for key in ("stat_date", "period", "org_id", "dim_val")
    )


def check_quality(
    rows: list[dict[str, Any]],
    *,
    requested_period: str = "",
    requested_end: str = "",
    actual_period: str = "",
    fallbacks: Iterable[str] = (),
    requested_dimensions: Iterable[str] = (),
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    rows = [row for row in rows if isinstance(row, dict)]
    fallback_list = [str(item) for item in fallbacks if item]

    if not rows:
        issues.append(
            QualityIssue(
                "no_data",
                "high",
                "请求范围内没有查到数据；请检查指标/机构/周期/日期，或使用 latest_available 回退。",
                {},
            )
        )
        return issues

    values = [(row, _to_float(row.get("val"))) for row in rows]
    numbers = [value for _, value in values if value is not None]
    if len(numbers) < len(values):
        missing = len(values) - len(numbers)
        ratio = missing / len(values)
        if ratio > 0.3:
            issues.append(
                QualityIssue(
                    "missing_ratio",
                    "warn",
                    f"{missing}/{len(values)} 条记录的 val 为空（{ratio:.0%}），样本可能不足。",
                    {"missing": missing, "total": len(values)},
                )
            )

    # 恒定序列：同一组织/维度下 >=3 期 val 完全相同。
    groups: dict[str, list[tuple[date | None, float, dict[str, Any]]]] = {}
    for row, value in values:
        if value is None:
            continue
        key = f"{row.get('org_id')}|{row.get('dim_val')}|{row.get('period')}"
        groups.setdefault(key, []).append((_to_date(row.get("stat_date")), value, row))
    for key, items in groups.items():
        if len(items) < 3:
            continue
        unique = {round(item[1], 4) for item in items}
        if len(unique) == 1:
            issues.append(
                QualityIssue(
                    "constant_series",
                    "high",
                    f"同一组织/维度下 {len(items)} 期数值完全一致（{next(iter(unique))}），"
                    "疑似占位/未更新数据，不能据此判断趋势。",
                    {"group": key, "value": next(iter(unique)), "periods": len(items)},
                )
            )
            break

    # 陈旧数据
    end_date = _to_date(requested_end)
    all_dates = [d for items in groups.values() for d, _, _ in items if d]
    latest = max(all_dates) if all_dates else None
    if end_date and latest and latest < end_date:
        issues.append(
            QualityIssue(
                "stale_data",
                "warn",
                f"最新可用日期为 {latest.isoformat()}，早于请求结束日期 {end_date.isoformat()}。",
                {"latest_available": latest.isoformat(), "requested_end": end_date.isoformat()},
            )
        )

    # 周期回退
    if requested_period and actual_period and requested_period != actual_period:
        issues.append(
            QualityIssue(
                "period_fallback",
                "warn",
                f"请求周期为「{requested_period}」，实际使用「{actual_period}」。",
                {"requested": requested_period, "actual": actual_period},
            )
        )

    # 表/维度/机构回退
    mapping = {
        "table_fallback": "使用了备用数据表/层级表",
        "dim_fallback": "维度无数据，已回退为总量",
        "org_fallback": "机构级别不支持，已回退到可用级别",
    }
    for code, text in mapping.items():
        if code in fallback_list:
            issues.append(QualityIssue(code, "warn", text, {"fallbacks": fallback_list}))

    # 单位不一致
    units = {str(row.get("unit") or "").strip() for row in rows if str(row.get("unit") or "").strip()}
    if len(units) > 1:
        issues.append(
            QualityIssue(
                "unit_mismatch",
                "warn",
                f"结果包含多个单位：{'、'.join(sorted(units))}，不可直接相加。",
                {"units": sorted(units)},
            )
        )

    # 重复行
    seen: dict[str, int] = {}
    for row in rows:
        key = _val_key(row)
        seen[key] = seen.get(key, 0) + 1
    duplicates = {key: count for key, count in seen.items() if count > 1}
    if duplicates:
        issues.append(
            QualityIssue(
                "duplicate_rows",
                "warn",
                f"存在 {len(duplicates)} 组重复键（同日期/周期/机构/维度）。",
                {"groups": len(duplicates)},
            )
        )

    # 异常值（IQR；样本 >=8）
    if len(numbers) >= 8:
        ordered = sorted(numbers)
        q1 = ordered[len(ordered) // 4]
        q3 = ordered[(len(ordered) * 3) // 4]
        iqr = q3 - q1
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = [
            {"stat_date": str(row.get("stat_date")), "val": value}
            for row, value in values
            if value is not None and (value < low or value > high)
        ]
        if outliers:
            issues.append(
                QualityIssue(
                    "outlier",
                    "warn",
                    f"检测到 {len(outliers)} 个 IQR 异常值，建议核对口径或事件。",
                    {"outliers": outliers[:10], "fence": [low, high]},
                )
            )

    return issues


def severity_rank(severity: str) -> int:
    return {"info": 0, "warn": 1, "high": 2}.get(str(severity), 0)


def summarize_issues(issues: Iterable[QualityIssue]) -> str:
    items = sorted(issues, key=lambda item: severity_rank(item.severity), reverse=True)
    if not items:
        return "数据质量未发现明显问题。"
    return "；".join(f"[{item.severity}] {item.message}" for item in items)


__all__ = ["QualityIssue", "check_quality", "severity_rank", "summarize_issues"]
