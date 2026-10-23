import type { DragEvent, RefObject } from "react";

import { MAX_FILES, SUPPORTED_SUFFIXES } from "../attachments";
import { ArrowUpIcon, PaperclipIcon, StopIcon } from "../icons";
import type { AttachmentInfo } from "../types";
import { AttachmentChips } from "./AttachmentChips";

interface ComposerProps {
  attachments: AttachmentInfo[];
  onRemoveAttachment: (attachmentId: string) => void;
  attachmentsLocked: boolean;
  uploading: boolean;
  dragging: boolean;
  input: string;
  setInput: (value: string) => void;
  onSend: () => void;
  onInterrupt: () => void;
  canSend: boolean;
  streaming: boolean;
  pendingQuestion: boolean;
  pendingApproval: boolean;
  /** 只读账户：可浏览历史 / 文件，但不能发起对话或上传附件。 */
  readOnly?: boolean;
  textareaRef: RefObject<HTMLTextAreaElement>;
  fileInputRef: RefObject<HTMLInputElement>;
  onFileInputChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}

export function Composer({
  attachments,
  onRemoveAttachment,
  attachmentsLocked,
  uploading,
  dragging,
  input,
  setInput,
  onSend,
  onInterrupt,
  canSend,
  streaming,
  pendingQuestion,
  pendingApproval,
  readOnly = false,
  textareaRef,
  fileInputRef,
  onFileInputChange,
  onDragOver,
  onDragLeave,
  onDrop,
}: ComposerProps) {
  return (

        <div
          className={`composer-dock${dragging ? " drag-active" : ""}`}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <AttachmentChips
            items={attachments}
            onRemove={onRemoveAttachment}
            disabled={attachmentsLocked}
          />

          <div className="composer-card">
            {/* 隐藏的原生选择框：样式受限且各浏览器不一，统一用工具条按钮触发。 */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              accept={SUPPORTED_SUFFIXES.join(",")}
              onChange={onFileInputChange}
            />
            <textarea
              ref={textareaRef}
              value={input}
              rows={1}
              placeholder={
                readOnly
                  ? "只读账户：可查看历史对话与文件，不能发起新对话"
                  : streaming
                  ? pendingQuestion
                    ? "请先回答上方的问题…"
                    : pendingApproval
                      ? "请先确认上方操作…"
                      : "正在执行，可点击右侧停止…"
                  : "输入问题，Enter 发送，Shift+Enter 换行；可拖入文件"
              }
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void onSend();
                }
              }}
              disabled={streaming || readOnly}
            />
            <div className="composer-toolbar">
              <div className="composer-left">
                <button
                  type="button"
                  className="icon-btn attach-btn"
                  title={`上传附件（最多 ${MAX_FILES} 个：Word / Excel / TXT 等），也可以直接把文件拖进页面`}
                  aria-label="上传附件"
                  disabled={attachmentsLocked || readOnly}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <PaperclipIcon size={16} />
                  <span>{uploading ? "解析中…" : "附件"}</span>
                </button>
                <span className="composer-hint">
                  {readOnly
                    ? "只读账户"
                    : pendingQuestion
                    ? "回答上方问题后继续"
                    : pendingApproval
                      ? "确认上方操作后继续"
                      : "Enter 发送 · Shift+Enter 换行"}
                </span>
              </div>
              <div className="composer-right">
                {pendingApproval ? <span className="composer-note">等待确认</span> : null}
                {streaming ? (
                  <button
                    type="button"
                    className="send-btn stop"
                    title="停止当前任务"
                    aria-label="停止当前任务"
                    onClick={() => void onInterrupt()}
                  >
                    <StopIcon size={16} />
                  </button>
                ) : (
                  <button
                    type="button"
                    className="send-btn"
                    title="发送"
                    aria-label="发送"
                    disabled={!canSend}
                    onClick={() => void onSend()}
                  >
                    <ArrowUpIcon size={18} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
  );
}
