import { useEffect, useRef } from "react";

import { reasoningSegments } from "../reasoning";

/**
 * 思考过程正文。
 *
 * 取舍：
 * - 普通叙述按空行分段、段内换行压成空格，避免文字被切得很碎；
 * - 「准备调用工具」后面的 JSON/代码单独成块，用等宽字体紧凑展示；
 * - 固定最大高度 + 内部滚动，展开后不会把整轮对话顶得很长；
 * - 流式输出时自动贴底，用户手动向上翻后不再抢滚动位置。
 */
export function ReasoningBlock({ text, running }: { text: string; running: boolean }) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    if (!running) return;
    const node = bodyRef.current;
    if (!node || !stickToBottom.current) return;
    node.scrollTop = node.scrollHeight;
  }, [text, running]);

  const segments = reasoningSegments(text);
  if (!segments.length) return null;

  return (
    <div
      className="reasoning-body"
      ref={bodyRef}
      role="region"
      aria-label="思考"
      tabIndex={0}
      onScroll={(event) => {
        const node = event.currentTarget;
        stickToBottom.current =
          node.scrollHeight - node.scrollTop - node.clientHeight < 40;
      }}
    >
      {segments.map((segment, index) =>
        segment.kind === "tool" ? (
          <div className="reasoning-tool" key={`tool-${index}`}>
            <span className="reasoning-tool-label">调用工具 · {segment.label}</span>
            {segment.text ? <pre className="reasoning-tool-code">{segment.text}</pre> : null}
          </div>
        ) : (
          <p key={`text-${index}`}>{segment.text}</p>
        ),
      )}
    </div>
  );
}
