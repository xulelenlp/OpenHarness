"""日期与周期工具（业务查询专用）。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from qaapp.data.catalog import Indicator

_PERIOD_ORDER = ("月", "年", "季", "周", "日")


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


def _periods_from_indicator(indicator: Indicator) -> list[str]:
    periods = [item for item in indicator.periods if item in _PERIOD_ORDER]
    return periods or list(_PERIOD_ORDER)
