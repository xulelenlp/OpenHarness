/** 文本预览只取前 2000 字符：大 CSV 全量渲染会卡死页面。 */
export const PREVIEW_CHARS = 2000;

/** 这些工具完成时可能往会话目录写文件：抽屉打开时触发一次静默刷新。 */
export const FILE_WRITING_TOOLS = new Set([
  "write_file",
  "edit_file",
  "notebook_edit",
  "run_script",
  "write_report_docx",
  "bash",
]);
