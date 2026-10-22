import type { DragEvent, RefObject } from "react";

import { MAX_FILES } from "../attachments";
import { ResultFilesCard, TurnFilesCard } from "../FileCards";
import { excludeResultFiles } from "../turnFiles";
import { shortId } from "../chat/format";
import { EmptyState } from "../EmptyState";
import { BrandMark, CopyIcon, PaperclipIcon } from "../icons";
import type { AgentInfo, ChatMessage } from "../types";
import { AttachmentChips } from "./AttachmentChips";
import { AssistantBubble } from "./AssistantBubble";
import { StepCard, type ApprovalAction } from "./StepCard";

interface MessageListProps {
  messages: ChatMessage[];
  agents: AgentInfo[];
  renderOverride: Record<number, boolean>;
  setRenderOverride: React.Dispatch<React.SetStateAction<Record<number, boolean>>>;
  markdownDefault: boolean;
  copiedIndex: number | null;
  onCopyMessage: (index: number, text: string) => void;
  stepOpen: Record<string, boolean>;
  onToggleStep: (key: string) => void;
  onApproval: ApprovalAction;
  onPreview: (path: string) => void;
  onDownload: (path: string) => void;
  downloadingPath: string | null;
  loadingConversation: boolean;
  onPickPrompt: (prompt: string) => void;
  dragging: boolean;
  messagesRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}

export function MessageList({
  messages,
  agents,
  renderOverride,
  setRenderOverride,
  markdownDefault,
  copiedIndex,
  onCopyMessage,
  stepOpen,
  onToggleStep,
  onApproval,
  onPreview,
  onDownload,
  downloadingPath,
  loadingConversation,
  onPickPrompt,
  dragging,
  messagesRef,
  onScroll,
  onDragOver,
  onDragLeave,
  onDrop,
}: MessageListProps) {
  return (

        <div
          ref={messagesRef}
          className={`messages${dragging ? " drag-active" : ""}`}
          onScroll={onScroll}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          {loadingConversation ? (
            <div className="message-skeleton" aria-label="正在加载对话">
              <span />
              <span />
              <span />
            </div>
          ) : null}

          {!loadingConversation && messages.length === 0 ? (
            <EmptyState
              onPick={(prompt) => onPickPrompt(prompt)}
            />
          ) : null}

          {messages.map((m, i) => {
            const isAssistant = m.role === "assistant";
            const useMarkdown = renderOverride[i] ?? markdownDefault;
            // 结果文件只在结果区出现；过程区展示其余文件（脚本/数据/图片/md 等）。
            const processFiles = excludeResultFiles(m.changedFiles ?? [], m.resultFiles ?? []);
            const hasPendingApproval = m.steps.some(
              (step) => step.kind === "approval" && step.status === "running",
            );
            return (
              <div key={i} className={`message ${m.role}`}>
                <div className="message-head">
                  <span className={`avatar ${isAssistant ? "assistant" : "user"}`}>
                    {isAssistant ? <BrandMark size={24} /> : "我"}
                  </span>
                  {isAssistant ? <span className="role">助手</span> : null}
                  {isAssistant && m.agentName ? (
                    <span className="agent-badge">
                      {agents.find((agent) => agent.id === m.agentName)?.name || m.agentName}
                    </span>
                  ) : null}
                  {m.turnId && <span className="turn-id">turn #{shortId(m.turnId)}</span>}
                  {isAssistant && m.content && (
                    <span className="msg-actions">
                      <button
                        type="button"
                        className="mini-btn"
                        title={useMarkdown ? "查看 Markdown 原文" : "按 Markdown 渲染"}
                        onClick={() =>
                          setRenderOverride((prev) => ({ ...prev, [i]: !useMarkdown }))
                        }
                      >
                        {useMarkdown ? "源码" : "预览"}
                      </button>
                      <button
                        type="button"
                        className="mini-btn"
                        title="复制 Markdown 原文"
                        onClick={() => void onCopyMessage(i, m.content)}
                      >
                        <CopyIcon size={12} />
                        {copiedIndex === i ? "已复制" : "复制"}
                      </button>
                    </span>
                  )}
                </div>

                {isAssistant && m.steps.length > 0 && (
                  <div className="activity">
                    <div className="activity-head">
                      <span className="activity-title">执行过程</span>
                      <span className="activity-count">{m.steps.length} 步</span>
                    </div>
                    <div className="steps">
                      {m.steps.map((s) => {
                        const openKey = `${i}:${s.key}`;
                        // 待处理的审批必须展开（需用户操作）；正在运行的工具步骤展开；
                        // 思考与历史步骤默认折叠，保持对话干净。
                        const needsAction = s.kind === "approval" && s.status === "running";
                        const collapsed = needsAction
                          ? false
                          : hasPendingApproval && s.kind === "tool" && s.status === "running"
                            ? !stepOpen[openKey]
                            : s.kind === "tool" && s.toolName === "ask_user_question"
                              ? !stepOpen[openKey]
                              : s.kind === "reasoning"
                                ? !stepOpen[openKey]
                                : s.status === "running"
                                  ? false
                                  : !stepOpen[openKey];
                        return (
                          <StepCard
                            key={s.key}
                            step={s}
                            collapsed={collapsed}
                            onToggle={() => onToggleStep(openKey)}
                            onApproval={onApproval}
                          />
                        );
                      })}
                    </div>
                  </div>
                )}

                {isAssistant ? (
                  <>
                    <AssistantBubble
                      content={m.content}
                      streaming={Boolean(m.streaming) && !hasPendingApproval}
                      markdown={useMarkdown}
                      error={m.error}
                    />
                    {/* 正文之后依次是过程文件（可折叠）与结果文件（可直接下载）：
                        结论优先，过程只占用折叠卡片，结果文件紧邻正文便于点击。 */}
                    {processFiles.length > 0 && (
                      <TurnFilesCard
                        files={processFiles}
                        truncated={m.filesTruncated}
                        onPreview={onPreview}
                        onDownload={onDownload}
                        downloading={downloadingPath}
                      />
                    )}
                    {(m.resultFiles?.length ?? 0) > 0 && (
                      <ResultFilesCard
                        files={m.resultFiles ?? []}
                        onPreview={onPreview}
                        onDownload={onDownload}
                        downloading={downloadingPath}
                      />
                    )}
                    {m.filesDerived &&
                      (processFiles.length > 0 || (m.resultFiles?.length ?? 0) > 0) && (
                        <div className="files-derived muted">根据本轮工具轨迹整理，仅供参考</div>
                      )}
                  </>
                ) : (
                  <>
                    {/* 内容为空（只发附件）时不渲染空气泡，避免多出一块空白。 */}
                    {m.content ? (
                      <div className={`bubble ${m.error ? "error" : ""}`}>{m.content}</div>
                    ) : null}
                    {/* 气泡下方渲染本轮附件芯片：实时发送用本地状态，历史回放来自 attachments_ready 事件。 */}
                    <AttachmentChips items={m.attachments ?? []} />
                  </>
                )}
              </div>
            );
          })}

          {dragging ? (
            <div className="drop-overlay" aria-hidden="true">
              <PaperclipIcon size={22} />
              <strong>松开即可上传</strong>
              <span>Word / Excel / 文本，最多 {MAX_FILES} 个</span>
            </div>
          ) : null}
        </div>
  );
}
