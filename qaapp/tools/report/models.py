"""Report input models and layout constants."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# 国网公文格式：标题宋体二号加粗；作者/日期楷体三号加粗；一级标题黑体三号；
# 二级标题楷体三号加粗；三级标题宋体三号加粗；四级标题/正文仿宋三号。
_TITLE_FONT = "宋体"
_AUTHOR_FONT = "楷体"
_H1_FONT = "黑体"
_H2_FONT = "楷体"
_H3_FONT = "宋体"
_H4_FONT = "仿宋"
_BODY_FONT = "仿宋"

_TITLE_SIZE = 22  # 二号
_BODY_SIZE = 16  # 三号
_TABLE_SIZE = 10.5  # 五号
_LINE_SPACING_PT = 29
_BODY_INDENT_PT = _BODY_SIZE * 2  # 正文首行缩进两字符
_DEFAULT_ORG = "国家电网公司"

#: 各级标题的（字体, 是否加粗）映射。一级标题按规范不加粗。
_HEADING_SPECS = {
    1: (_H1_FONT, False),
    2: (_H2_FONT, True),
    3: (_H3_FONT, True),
    4: (_H4_FONT, False),
}

_MAX_TABLE_ROWS = 200
_MAX_TABLE_COLS = 12


class ReportTable(BaseModel):
    title: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    note: str = ""


class ReportImage(BaseModel):
    path: str
    caption: str = ""
    width_cm: float = 15.0


class ReportSection(BaseModel):
    heading: str
    level: int = 1
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    tables: list[ReportTable] = Field(default_factory=list)
    images: list[ReportImage] = Field(default_factory=list)
    page_break_before: bool = False


class WriteReportDocxInput(BaseModel):
    title: str = Field(description="报告标题")
    subtitle: str = ""
    author: str = ""
    org: str = Field(default="", description="作者单位；留空时依次用 author、国家电网公司")
    date: str = Field(default="", description="成文日期（如 2026年10月21日）；留空用当天")
    period: str = ""
    markdown: str = Field(default="", description="Markdown 全文；与 markdown_path/sections 三选一")
    markdown_path: str = Field(default="", description="会话目录内的 Markdown 文件，适合长报告")
    sections: list[ReportSection] = Field(default_factory=list, description="结构化章节；优先于 Markdown")
    sources: list[dict[str, Any]] = Field(default_factory=list, description="来源列表：label/detail/url")
    data_quality: list[str] = Field(default_factory=list, description="数据质量与局限性说明")
    file_name: str = Field(default="", description="输出文件名（不含目录），留空按标题生成")
    include_toc: bool = False
    include_page_numbers: bool = True

