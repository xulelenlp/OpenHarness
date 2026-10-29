import { useEffect, useRef } from "react";

import { reasoningParagraphs } from "../reasoning";

/**
 * 思考过程正文。
 *
 * 取舍：
 * - 按空行分段，避免把整段英文 reasoning 渲染成一块密文；
 * - 固定最大高度 + 内部滚动，展开后也不会把整轮对话顶得很长；
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

  const paragraphs = reasoningParagraphs(text);
  if (!paragraphs.length) return null;

  return (
    <div className="reasoning-wrap">
      <div className="reasoning-head">
        <span>模型思考过程</span>
        <span className="reasoning-note">仅用于了解分析路径，不影响最终结论</span>
      </div>
      <div
        className="reasoning-body"
        ref={bodyRef}
        role="region"
        aria-label="模型思考过程"
        tabIndex={0}
        onScroll={(event) => {
          const node = event.currentTarget;
          stickToBottom.current =
            node.scrollHeight - node.scrollTop - node.clientHeight < 40;
        }}
      >
        {paragraphs.map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </div>
      {text.length > 1200 ? (
        <div className="reasoning-foot">内容较长，可滚动查看完整思考</div>
      ) : null}
    </div>
  );
}
