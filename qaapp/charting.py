"""轻量图表生成（matplotlib，Agg 后端）。

用于把 ``db_query`` 的结果快速变成报告可嵌入的 PNG。无 matplotlib/中文字体时
返回结构化错误，不阻断文字报告。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def _setup_matplotlib():
    os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="qaapp-mpl-"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    candidates = [
        "PingFang SC",
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
        "Source Han Sans SC",
        "Arial Unicode MS",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    chosen = next((name for name in candidates if name in available), "")
    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen]
    plt.rcParams["axes.unicode_minus"] = False
    return plt, bool(chosen)


def render_chart(
    rows: list[dict[str, Any]],
    *,
    chart_type: str = "line",
    title: str = "",
    output_dir: Path | None = None,
    y_field: str = "val",
) -> dict[str, Any]:
    if not rows:
        return {"status": "error", "message": "没有可用于绘图的数据"}
    if output_dir is None:
        return {"status": "error", "message": "没有会话目录，无法保存图表"}
    try:
        plt, has_cjk = _setup_matplotlib()
    except Exception as exc:  # pragma: no cover - 依赖缺失  # noqa: BLE001
        return {"status": "error", "message": f"matplotlib 不可用: {exc}"}

    output_dir.mkdir(parents=True, exist_ok=True)
    x_field = next(
        (field for field in ("key", "dim_name", "stat_date", "org_id", "index_name") if field in rows[0]),
        "key",
    )
    rows = rows[:30]
    labels = [str(row.get(x_field) or "") for row in rows]
    values = []
    for row in rows:
        value = row.get(y_field)
        try:
            values.append(float(value) if value is not None else 0.0)
        except (TypeError, ValueError):
            values.append(0.0)

    fig, ax = plt.subplots(figsize=(9, 4.2))
    try:
        if chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%")
            ax.axis("equal")
        elif chart_type == "bar":
            ax.bar(labels, values, color="#4472C4")
            ax.tick_params(axis="x", rotation=30)
        else:
            ax.plot(labels, values, marker="o", color="#4472C4")
            ax.tick_params(axis="x", rotation=30)
        ax.set_title(title or "数据图表")
        ax.grid(axis="y", alpha=0.25)
        if not has_cjk:
            ax.set_title(title or "Chart")
        fig.tight_layout()
        filename = (title or "chart").replace("/", "-").replace("\\", "-")[:60]
        path = output_dir / f"{filename}-{os.getpid()}.png"
        fig.savefig(path, dpi=150)
        return {"status": "ok", "path": str(path), "charts": 1, "cjk_font": has_cjk}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": f"绘图失败: {exc}"}
    finally:
        plt.close(fig)


__all__ = ["render_chart"]
