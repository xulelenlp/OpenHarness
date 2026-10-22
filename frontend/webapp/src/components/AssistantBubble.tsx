import { MarkdownView, useThrottledText } from "../markdown";

interface AssistantBubbleProps {
  content: string;
  streaming: boolean;
  /** true = 按 Markdown 渲染；false = 显示原始文本（源码）。 */
  markdown: boolean;
  error?: boolean;
}

/**
 * 助手气泡。流式期间节流 Markdown 渲染，避免每个增量都重建整棵 React 树。
 */
export function AssistantBubble({ content, streaming, markdown, error }: AssistantBubbleProps) {
  const throttled = useThrottledText(content, streaming ? 150 : 0);
  const text = streaming ? throttled : content;
  // 出错文案按纯文本显示：那是系统消息，不该被当成 Markdown 解释。
  const asMarkdown = markdown && !error;
  const cls = `bubble${asMarkdown && text ? " md" : ""}${error ? " error" : ""}`;

  if (!text) {
    return (
      <div className={cls}>
        {streaming ? (
          <span className="thinking" role="status" aria-label="正在思考">
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-label">正在思考</span>
          </span>
        ) : (
          ""
        )}
      </div>
    );
  }
  if (!asMarkdown) return <div className={cls}>{text}</div>;
  return (
    <div className={cls}>
      <MarkdownView content={text} />
    </div>
  );
}

