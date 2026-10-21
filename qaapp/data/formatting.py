"""查询结果 JSON 化工具。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _rows_jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]
