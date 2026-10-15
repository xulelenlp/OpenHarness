/**
 * 附件的纯函数工具集。
 *
 * 刻意保持"零依赖 + 不碰 DOM"（只看 `File.name` / `File.size`），
 * 这样 `scripts/check-attachments.mjs` 能用 esbuild 直接编译后在 Node 里跑断言，
 * 不需要浏览器环境、也不需要任何测试框架。
 */

import type { AttachmentInfo, WorkspaceFile } from "./types";

/** 单轮最多携带的附件数（后端同样按 2 校验）。 */
export const MAX_FILES = 2;

/** 允许上传的后缀白名单（小写比较）。 */
export const SUPPORTED_SUFFIXES = [
  ".txt",
  ".md",
  ".csv",
  ".docx",
  ".xlsx",
  ".xls",
  ".json",
  ".log",
  ".yaml",
  ".yml",
  ".xml",
];

/** 可在右侧面板里直接预览正文的文本类后缀（二进制格式无法当文本读）。 */
export const TEXT_PREVIEW_SUFFIXES = [".txt", ".md", ".csv", ".json", ".log"];

/** 取小写后缀（含点）；没有后缀时返回空串。 */
function suffixOf(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? name;
  const dot = base.lastIndexOf(".");
  // dot <= 0 覆盖"没有后缀"和".gitignore"这类只有点开头的隐藏文件
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

/** 后缀是否在白名单内。 */
export function isSupportedFile(name: string): boolean {
  return SUPPORTED_SUFFIXES.includes(suffixOf(name));
}

/** 是否属于可当文本读出来预览的文件。 */
export function isPreviewableText(name: string): boolean {
  return TEXT_PREVIEW_SUFFIXES.includes(suffixOf(name));
}

/**
 * 字节数 → 可读体积：`512B` / `1.2KB` / `3.45MB` / `1.20GB`。
 *
 * KB 保留 1 位小数（聊天里最常见的量级，够用且不啰嗦），MB/GB 保留 2 位。
 */
export function formatBytes(size: number): string {
  const n = Number.isFinite(size) && size > 0 ? size : 0;
  if (n < 1024) return `${Math.round(n)}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)}MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)}GB`;
}

/**
 * 按"总上限 max 个"筛选待添加的文件。
 *
 * 先判上限再判类型：超出上限的文件直接标 `limit`，这样用户看到的原因是
 * "最多 2 个"而不是被类型问题误导（文件本身可能是受支持的格式）。
 */
export function pickWithinLimit(
  existingCount: number,
  incoming: File[],
  max = MAX_FILES,
): { accepted: File[]; rejected: { name: string; reason: "limit" | "type" }[] } {
  const accepted: File[] = [];
  const rejected: { name: string; reason: "limit" | "type" }[] = [];
  let count = Math.max(0, existingCount);

  for (const file of incoming) {
    if (count >= max) {
      rejected.push({ name: file.name, reason: "limit" });
      continue;
    }
    if (!isSupportedFile(file.name)) {
      rejected.push({ name: file.name, reason: "type" });
      continue;
    }
    accepted.push(file);
    count += 1;
  }

  return { accepted, rejected };
}

/** 附件解析状态的中文文案（chip 与气泡共用）。 */
export function parseStatusLabel(a: AttachmentInfo): string {
  if (a.parse_status === "pending") return "解析中…";
  if (a.parse_status === "failed") return `解析失败：${a.parse_error || "未知原因"}`;
  return `已解析 ${a.text_chars} 字`;
}

/** epoch 秒 → 本地 `YYYY-MM-DD HH:mm`。 */
function formatMtime(epochSeconds: number): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return "时间未知";
  const d = new Date(epochSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * 右侧「会话文件」面板的次要信息行：目录 + 大小 + 修改时间。
 *
 * 目录单独列出来是因为文件名在分组里只显示末段，光看名字分不清
 * `scripts/x.py` 与 `outputs/x.py`。
 */
export function workspaceLabel(file: WorkspaceFile): string {
  const dir = file.dir && file.dir !== "." ? file.dir : "根目录";
  return `${dir} · ${formatBytes(file.size)} · ${formatMtime(file.mtime)}`;
}
