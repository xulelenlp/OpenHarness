"""日期与周期工具（业务查询专用）。"""

from __future__ import annotations

from datetime import date, timedelta

from qaapp.data.catalog import Indicator
from qaapp.data.dates import as_date as _as_date  # noqa: F401 - re-exported
from qaapp.data.dates import today as _today

_PERIOD_ORDER = ("月", "年", "季", "周", "日")


def _month_end(value: date) -> date:
    if value.month == 12:
        return date(value.year, 12, 31)
    return date(value.year, value.month + 1, 1) - timedelta(days=1)


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


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
