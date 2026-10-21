/**
 * 「中间过程文件 / 结果文件」纯函数。
 *
 * 两条数据来源：
 * 1. 新轨迹：后端 `turn_files` 事件（文件系统 diff，权威）；
 * 2. 旧轨迹：本模块从工具输出推导（`write_file` / `edit_file` / `run_script` /
 *    `write_report_docx`），保证已存在的会话刷新后也能看到卡片。
 *
 * 不依赖 React / DOM，便于 `scripts/check-turn-files.mjs` 直接编译自检。
 */

import type { ChangedFile, FileKind, ResultFile, Step } from "./types";

/**
 * 结果文件后缀：只有 `outputs/` 下的成果性文档。
 *
 * csv / py / txt / json / 图片是过程产物，只在过程文件卡片里展示，不进入结果区。
 */
const RESULT_SUFFIXES = new Set([
  "docx",
  "doc",
  "pdf",
  "pptx",
  "ppt",
  "xlsx",
  "xls",
  "md",
  "markdown",
]);
const RESULT_ORDER = ["docx", "pdf", "pptx", "xlsx", "md", "doc", "ppt", "xls", "markdown"];

/** 会话目录里会出现在卡片中的子目录。 */
const CONVERSATION_DIRS = new Set(["uploads", "extracted", "scripts", "tmp", "outputs"]);

const ANSI_PATTERN = /\u001b\[[0-9;]*m/g;

/** 去掉终端颜色码：工具输出里 `(+78 -0)` 带 ANSI，直接展示会乱码。 */
export function stripAnsi(text: unknown): string {
  return String(text ?? "").replace(ANSI_PATTERN, "");
}

function suffixOf(path: string): string {
  const index = path.lastIndexOf(".");
  return index >= 0 ? path.slice(index + 1).toLowerCase() : "";
}

/** 扩展名 → 图标分类（与后端 `classify_kind` 对齐）。 */
export function fileKind(path: string): FileKind {
  const suffix = suffixOf(path);
  if (suffix === "docx" || suffix === "doc") return "word";
  if (suffix === "xlsx" || suffix === "xls") return "excel";
  if (suffix === "pptx" || suffix === "ppt") return "pptx";
  if (suffix === "pdf") return "pdf";
  if (suffix === "md" || suffix === "markdown") return "markdown";
  if (["png", "jpg", "jpeg", "webp", "gif", "svg"].includes(suffix)) return "image";
  if (["py", "js", "mjs", "cjs", "sh"].includes(suffix)) return "script";
  if (["csv", "json", "yaml", "yml", "xml"].includes(suffix)) return "data";
  if (["zip", "tar", "gz", "7z"].includes(suffix)) return "archive";
  if (suffix === "txt" || suffix === "log") return "text";
  return "other";
}

/** 类型的中文文案（结果卡片副标题）。 */
export function kindLabel(kind: FileKind): string {
  if (kind === "word") return "Word 文档";
  if (kind === "excel") return "Excel 表格";
  if (kind === "pptx") return "PPT";
  if (kind === "pdf") return "PDF";
  if (kind === "markdown") return "Markdown";
  if (kind === "image") return "图片";
  if (kind === "script") return "脚本";
  if (kind === "data") return "数据文件";
  if (kind === "archive") return "压缩包";
  if (kind === "text") return "文本";
  return "文件";
}

/** 色块里的短标签（比 emoji 更稳定，跨平台一致）。 */
export function kindTag(kind: FileKind): string {
  if (kind === "word") return "W";
  if (kind === "excel") return "X";
  if (kind === "pptx") return "P";
  if (kind === "pdf") return "PDF";
  if (kind === "markdown") return "MD";
  if (kind === "image") return "IMG";
  if (kind === "script") return "PY";
  if (kind === "data") return "DATA";
  if (kind === "archive") return "ZIP";
  if (kind === "text") return "TXT";
  return "FILE";
}

/** 汇总新增/删除行数；`known=false` 表示所有文件都是二进制 / 过大，不展示统计。 */
export function summarizeLines(files: ChangedFile[]): {
  added: number;
  removed: number;
  known: boolean;
} {
  let added = 0;
  let removed = 0;
  let known = false;
  for (const file of files) {
    if (file.added !== null && file.added !== undefined) {
      added += file.added;
      known = true;
    }
    if (file.removed !== null && file.removed !== undefined) {
      removed += file.removed;
      known = true;
    }
  }
  return { added, removed, known };
}

/**
 * 从变更里挑出结果文件：只看 `outputs/` 下的成果性文档。
 *
 * 接受 Word / PDF / PPT / Excel / Markdown；csv、py、txt、json、图片等过程
 * 产物不会进入结果区。与后端规则一致，旧轨迹兜底时复用。
 */
export function pickResultFiles(changed: ChangedFile[], limit = 8): ResultFile[] {
  const candidates = changed.filter((file) => file.dir === "outputs");
  const chosen = candidates.filter((file) => RESULT_SUFFIXES.has(suffixOf(file.path))).slice();
  const rank = (file: ChangedFile): number => {
    const index = RESULT_ORDER.indexOf(suffixOf(file.path));
    return index >= 0 ? index : 99;
  };
  chosen.sort((a, b) => rank(a) - rank(b) || b.mtime - a.mtime);
  return chosen.slice(0, limit).map((file) => {
    const name = file.path.split("/").filter(Boolean).pop() ?? file.path;
    return {
      path: file.path,
      name,
      title: name.replace(/\.[^.]+$/, ""),
      kind: file.kind,
      size: file.size,
      mtime: file.mtime,
      download_path: "",
    };
  });
}

/**
 * 过滤掉旧后端 / 旧轨迹可能混进 `results` 的非成果文件（csv / py / txt / 图片等）。
 *
 * 前端新版本不依赖后端重启：即使服务端仍按旧的退化规则下发结果，这里也会把它们
 * 还原到过程区，符合「结果区只放成果文档」的约定。
 */
export function keepResultFiles(results: ResultFile[]): ResultFile[] {
  return results.filter((file) => RESULT_SUFFIXES.has(suffixOf(file.path)));
}

/**
 * 过程文件 = 本轮变更中排除结果文件后的部分。
 *
 * 结果文件只在结果区展示，过程区不再重复；统计「已编辑 N 个文件」时也用过滤后
 * 的列表，避免把 docx 同时算进两边。
 */
export function excludeResultFiles(
  changed: ChangedFile[],
  results: ResultFile[],
): ChangedFile[] {
  if (results.length === 0) return changed;
  const resultPaths = new Set(results.map((file) => file.path));
  return changed.filter((file) => !resultPaths.has(file.path));
}

/**
 * 把工具输入/输出里的路径归一化成「会话相对路径」。
 *
 * 模型有时传绝对路径（`/Users/x/.ohmo/conversations/<id>/outputs/report.docx`），
 * 有时传相对路径（`outputs/report.docx`）；只要包含会话子目录，就截取该段。
 * 仓库里的普通文件（如 `case_converter.py`）返回 null，不进入卡片。
 */
export function normalizeConversationPath(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  let path = stripAnsi(raw).trim().replace(/^file:\/\//, "").replace(/\\/g, "/");
  path = path.replace(/^["']|["']$/g, "");
  path = path.replace(/^\.\//, "");
  if (!path || path.split("/").includes("..")) return null;

  // 取**最后**一个会话目录标记：Windows/远程路径可能形如 C:/tmp/outputs/r.docx，
  // 前面的 /tmp/ 只是系统目录，真正的会话相对路径从 outputs/ 开始。
  let bestIndex = -1;
  for (const dir of CONVERSATION_DIRS) {
    const index = path.indexOf(`/${dir}/`);
    if (index > bestIndex) bestIndex = index;
  }
  if (bestIndex >= 0) path = path.slice(bestIndex + 1);

  const segments: string[] = [];
  for (const segment of path.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") return null;
    segments.push(segment);
  }
  if (segments.length < 2 || !CONVERSATION_DIRS.has(segments[0])) return null;
  return segments.join("/");
}

function parseStats(output: string): { added?: number; removed?: number } {
  const match = /\+(\d+)\s+-(\d+)/.exec(output);
  if (!match) return {};
  return { added: Number(match[1]), removed: Number(match[2]) };
}

function parseReportSize(output: string): number | undefined {
  const match = /大小：(\d+)\s*字节/.exec(output);
  return match ? Number(match[1]) : undefined;
}

/**
 * 旧轨迹兜底：从工具步骤推导本轮文件。
 *
 * 覆盖四类工具：
 * - `write_file` / `edit_file` / `notebook_edit`：`path` + 输出里的 `(+N -M)`；
 * - `write_report_docx`：`已生成 Word 报告：<相对路径>`、`大小：N 字节`；
 * - `run_script`：首行 `[run_script] scripts/x.py` + `本次运行后 outputs/ 下的文件：a、b`。
 *
 * 推导不出文件时返回空列表，调用方据此决定是否渲染卡片。
 */
export function deriveTurnFilesFromSteps(steps: Step[]): {
  changed: ChangedFile[];
  results: ResultFile[];
} {
  const byPath = new Map<string, ChangedFile>();

  const add = (
    rawPath: unknown,
    options: { added?: number; removed?: number; size?: number; mtime?: number } = {},
  ): void => {
    const path = normalizeConversationPath(rawPath);
    if (!path) return;
    const existing = byPath.get(path);
    const dir = path.split("/")[0] ?? "";
    const kind = fileKind(path);
    if (existing) {
      if (existing.added === null && options.added !== undefined) existing.added = options.added;
      if (existing.removed === null && options.removed !== undefined) {
        existing.removed = options.removed;
      }
      if (options.size && options.size > existing.size) existing.size = options.size;
      return;
    }
    byPath.set(path, {
      path,
      dir,
      // 推导无法区分「新建」与「修改」，统一按修改展示（不影响下载/预览）。
      action: "modified",
      size: options.size ?? 0,
      mtime: options.mtime ?? 0,
      added: options.added ?? null,
      removed: options.removed ?? null,
      kind,
    });
  };

  steps.forEach((step, index) => {
    if (step.kind !== "tool" || !step.toolName) return;
    const input = (step.toolInput ?? {}) as Record<string, unknown>;
    const output = stripAnsi(step.toolOutput);
    const mtime = step.ts ?? index;

    if (["write_file", "edit_file", "notebook_edit"].includes(step.toolName)) {
      const stats = parseStats(output);
      add(input.path ?? input.file_path ?? input.notebook_path, { ...stats, mtime });
      return;
    }

    if (step.toolName === "write_report_docx") {
      // 路径可能含空格，取到行尾；后续行是「- 大小：…」。
      const match = /已生成 Word 报告：(.+)/.exec(output);
      if (match) {
        add(match[1].trim(), { size: parseReportSize(output), mtime });
      }
      return;
    }

    if (step.toolName === "run_script") {
      const script = /\[run_script\]\s+(.+?)(?:（|\(|\n|$)/.exec(output);
      if (script) add(script[1].trim(), { mtime });
      const produced = /本次运行后 outputs\/ 下的文件：(.+)/.exec(output);
      if (produced) {
        for (const item of produced[1].split("、")) {
          add(item.trim(), { mtime });
        }
      }
    }
  });

  const changed = [...byPath.values()].sort(
    (a, b) => a.mtime - b.mtime || a.path.localeCompare(b.path),
  );
  return { changed, results: pickResultFiles(changed) };
}
