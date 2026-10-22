"""Date helpers shared by business-query modules."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


def as_date(value: Any) -> date | None:
    """Parse the business date formats used by the catalog/database."""
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


def to_date(value: Any) -> date | None:
    """Parse a database date (first 10 chars, ``YYYY-MM-DD``)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def today() -> date:
    """Business-timezone (Asia/Shanghai) today."""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


__all__ = ["as_date", "to_date", "today"]
