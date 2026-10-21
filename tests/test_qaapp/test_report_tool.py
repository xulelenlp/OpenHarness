"""Word 报告工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from openharness.tools.base import ToolExecutionContext
from qaapp.tools.report import (
    ReportSection,
    ReportTable,
    WriteReportDocxInput,
    WriteReportDocxTool,
    parse_markdown,
)
from qaapp.tools.script import CONVERSATION_DIR_KEY


def _context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=tmp_path, metadata={CONVERSATION_DIR_KEY: str(tmp_path)})


@pytest.mark.asyncio
async def test_generate_docx_from_markdown(tmp_path: Path):
    markdown = """# 一、总体情况

2026年9月福建售电量为 **64.9459 亿千瓦时**，同比下降 6.31%。

## 1.1 分产业

| 产业 | 售电量 | 同比 |
|---|---:|---:|
| 第一产业 | 1.1044 | -13.85 |
| 第二产业 | 36.7980 | 0.20 |

- 第一产业降幅最大
- 第二产业基本持平
"""
    tool = WriteReportDocxTool()
    result = await tool.execute(
        WriteReportDocxInput(
            title="福建售电量分析",
            org="福建",
            period="2026-09",
            markdown=markdown,
            data_quality=["电费回收率序列恒定"],
            sources=[{"label": "业务库", "detail": "zhss_result_data_qs"}],
        ),
        _context(tmp_path),
    )
    assert result.is_error is False
    path = Path(result.metadata["path"])
    assert path.is_file()
    doc = Document(str(path))
    texts = [paragraph.text for paragraph in doc.paragraphs]
    assert any("总体情况" in text for text in texts)
    assert len(doc.tables) == 1
    assert any("数据质量与局限性" in text for text in texts)


@pytest.mark.asyncio
async def test_generate_docx_from_sections(tmp_path: Path):
    tool = WriteReportDocxTool()
    result = await tool.execute(
        WriteReportDocxInput(
            title="结构化报告",
            sections=[
                ReportSection(
                    heading="一、总体情况",
                    paragraphs=["这是正文。"],
                    bullets=["要点一"],
                    tables=[ReportTable(columns=["指标", "值"], rows=[["售电量", "64.9459"]])],
                )
            ],
        ),
        _context(tmp_path),
    )
    assert result.is_error is False
    doc = Document(result.metadata["path"])
    assert len(doc.tables) == 1
    assert any("这是正文" in paragraph.text for paragraph in doc.paragraphs)


@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path: Path):
    tool = WriteReportDocxTool()
    result = await tool.execute(
        WriteReportDocxInput(title="x", markdown_path="../../etc/passwd"),
        _context(tmp_path),
    )
    assert result.is_error is True
    assert "越界" in result.output


@pytest.mark.asyncio
async def test_missing_image_warns_but_still_generates(tmp_path: Path):
    tool = WriteReportDocxTool()
    result = await tool.execute(
        WriteReportDocxInput(
            title="缺图报告",
            sections=[ReportSection(heading="一、图", images=[{"path": "outputs/missing.png", "caption": "图1"}])],
        ),
        _context(tmp_path),
    )
    assert result.is_error is False
    assert "图片不存在" in "；".join(result.metadata["warnings"])


def test_parse_markdown_table_and_heading():
    blocks = parse_markdown("# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    kinds = [block["kind"] for block in blocks]
    assert "heading" in kinds
    assert "table" in kinds
