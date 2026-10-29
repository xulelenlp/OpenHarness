"""``write_report_docx`` tool entry point."""

from __future__ import annotations

from pathlib import Path

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from qaapp.tools.report.docx import ReportRendererMixin
from qaapp.tools.report.models import WriteReportDocxInput
from qaapp.tools.script import CONVERSATION_DIR_KEY


class WriteReportDocxTool(ReportRendererMixin, BaseTool):
    """生成 Word 报告。"""

    name = "write_report_docx"
    description = (
        "把分析内容生成 Word 报告（.docx），写到会话目录 outputs/reports/。\n"
        "输入可以是 sections（结构化章节/表格/图片），也可以是 markdown 或 markdown_path"
        "（适合先分段写长文，再一次性转 Word）。默认标题/单位/日期与正文同页，自动加标题层级、"
        "表格样式、图片、页码与来源附录；章节结构由你按主题决定（4–7 个主题化标题），不要套固定八章；"
        "长报告可传 include_toc=true 单独生成目录页。\n"
        "【版式固定】严格按国网公文格式输出：A4，上下页边距 35mm、左右 28mm；标题宋体二号加粗；"
        "作者/日期楷体三号加粗；一级标题黑体三号；二级标题楷体三号加粗；三级标题宋体三号加粗；"
        "四级标题与正文仿宋三号；固定行距 29 磅。标题层级用 1–4 级，正文首行缩进两字符。\n"
        "【要求】报告内容必须由你综合加工；数字先用 db_query 核验；不要直接把智能体原文粘进来。"
        "生成后返回路径；前端会在本轮正文下方展示结果文件卡片，用户可直接点击下载。"
    )
    input_model = WriteReportDocxInput

    def is_read_only(self, arguments: WriteReportDocxInput) -> bool:
        del arguments
        return False

    async def execute(
        self, arguments: WriteReportDocxInput, context: ToolExecutionContext
    ) -> ToolResult:
        raw = (context.metadata or {}).get(CONVERSATION_DIR_KEY)
        if not raw:
            return ToolResult(
                output="write_report_docx 不可用：当前会话没有临时目录。", is_error=True
            )
        root = Path(str(raw)).resolve()
        try:
            markdown_text, source_name = self._resolve_markdown(arguments, root)
            output_path = self._output_path(root, arguments)
            warnings = self._render(
                arguments,
                markdown_text,
                source_name,
                output_path,
                root,
            )
        except ValueError as exc:
            return ToolResult(output=f"报告参数不合法：{exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - 文档库异常统一转成可读错误
            return ToolResult(
                output=f"生成 Word 失败：{type(exc).__name__}: {exc}", is_error=True
            )
        rel = output_path.relative_to(root).as_posix()
        lines = [
            f"已生成 Word 报告：{rel}",
            f"- 大小：{output_path.stat().st_size} 字节",
            f"- 章节：{len(arguments.sections) if arguments.sections else 'Markdown 解析'}",
        ]
        if warnings:
            lines.append("- 警告：" + "；".join(warnings))
        conversation_id = str((context.metadata or {}).get("qaapp_conversation_id") or "")
        download_path = (
            f"/api/v1/conversations/{conversation_id}/files/{rel}" if conversation_id else ""
        )
        if download_path:
            lines.append(f"- 下载：{download_path}")
        else:
            lines.append("- 下载：在右侧「会话文件」中打开 outputs/reports/。")
        return ToolResult(
            output="\n".join(lines),
            metadata={
                "path": str(output_path),
                "relative_path": rel,
                "download_path": download_path,
                "warnings": warnings,
            },
        )
