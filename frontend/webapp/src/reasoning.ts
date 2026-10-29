/**
 * 思考步骤的纯展示逻辑。
 *
 * 模型的 reasoning 文本通常是「大段英文 + 空行分段」，直接塞进 `pre-wrap`
 * 会变成一整块密不透风的文字。这里只做两件事：按空行切段、生成折叠时的一行预览，
 * 便于 `ReasoningBlock` 和自检脚本复用。
 */

export function reasoningParagraphs(text: string): string[] {
  const normalized = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!normalized) return [];
  return normalized
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

export function reasoningPreview(text: string, maxLength = 96): string {
  const flat = String(text ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (!flat) return "";
  const limit = Math.max(1, Math.floor(maxLength));
  if (flat.length <= limit) return flat;
  return `${flat.slice(0, limit - 1).trimEnd()}…`;
}
