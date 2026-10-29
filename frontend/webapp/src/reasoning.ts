/**
 * 思考步骤的纯展示逻辑。
 *
 * 模型的 reasoning 文本里既有普通叙述，也有「准备调用工具」+ JSON/代码片段。
 * 这里把它们拆成文本段与工具段：
 * - 文本段把段内换行压成空格，避免一行只有半句话、看起来东一块西一块；
 * - 工具段单独成块，用紧凑等宽字体展示，避免和叙述混在一起。
 */

export type ReasoningSegment =
  | { kind: "text"; text: string }
  | { kind: "tool"; label: string; text: string };

const TOOL_MARKER = /^\s*\[准备调用工具[:：]?\s*(.+?)\]\s*$/;

export function reasoningSegments(text: string): ReasoningSegment[] {
  const normalized = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  if (!normalized) return [];

  const segments: ReasoningSegment[] = [];
  for (const block of normalized.split(/\n{2,}/)) {
    const lines = block.split("\n");
    let prose: string[] = [];

    const flushProse = () => {
      const value = prose.join(" ").replace(/\s+/g, " ").trim();
      if (value) segments.push({ kind: "text", text: value });
      prose = [];
    };

    let index = 0;
    while (index < lines.length) {
      const match = TOOL_MARKER.exec(lines[index]);
      if (!match) {
        prose.push(lines[index].trim());
        index += 1;
        continue;
      }

      flushProse();
      const label = match[1].trim();
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !TOOL_MARKER.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      segments.push({ kind: "tool", label, text: body.join("\n").trim() });
    }
    flushProse();
  }
  return segments;
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
