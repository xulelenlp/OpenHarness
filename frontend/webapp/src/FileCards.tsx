/**
 * 一轮回答的两组文件卡片：
 *
 * - `TurnFilesCard`：中间过程文件（脚本 / 数据 / 图片等，不含结果文件），折叠展示；
 * - `ResultFilesCard`：`outputs/` 下的成果性文档（Word / PDF / PPT / Excel /
 *   Markdown），点击卡片或「下载」直接下载。
 *
 * 组件只接收数据与回调，下载鉴权、错误提示由 App 统一处理。
 */

import { useState } from "react";
import { formatBytes, isPreviewableText } from "./attachments";
import { kindLabel, kindTag, summarizeLines } from "./turnFiles";
import type { ChangedFile, FileKind, ResultFile } from "./types";

export interface TurnFilesActions {
  /** 文本类文件预览（前 2000 字符）。 */
  onPreview: (path: string) => void;
  /** 下载会话文件。 */
  onDownload: (path: string) => void;
  /** 正在下载的相对路径（按钮显示「下载中…」）。 */
  downloading?: string | null;
}

/** 类型色块：短标签 + 颜色，比 emoji 更稳定。 */
function KindIcon({ kind }: { kind: FileKind }) {
  return <span className={`file-kind ${kind}`}>{kindTag(kind)}</span>;
}

function lineStats(file: ChangedFile) {
  if (file.added === null && file.removed === null) {
    return (
      <span className="tf-lines muted">{file.size > 0 ? formatBytes(file.size) : "二进制"}</span>
    );
  }
  return (
    <span className="tf-lines">
      <span className="add">+{(file.added ?? 0).toLocaleString()}</span>
      <span className="del">-{(file.removed ?? 0).toLocaleString()}</span>
    </span>
  );
}

/**
 * 中间过程文件卡片。
 *
 * 默认展开前 5 个文件：与「已编辑 N 个文件」的摘要一起，既能看到流程，又不会
 * 让一轮几十个脚本把对话刷屏。点标题折叠整卡，点底部展开全部。
 */
export function TurnFilesCard({
  files,
  truncated,
  onPreview,
  onDownload,
  downloading,
}: {
  files: ChangedFile[];
  truncated?: boolean;
} & TurnFilesActions) {
  const [open, setOpen] = useState(true);
  const [showAll, setShowAll] = useState(false);
  if (files.length === 0) return null;

  const stats = summarizeLines(files);
  const visible = showAll ? files : files.slice(0, 5);

  return (
    <div className="turn-files">
      <button
        type="button"
        className="tf-head"
        title={open ? "收起文件列表" : "展开文件列表"}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="tf-icon">📄</span>
        <span className="tf-title">已编辑 {files.length} 个文件</span>
        {stats.known && (
          <span className="tf-stat">
            <span className="add">+{stats.added.toLocaleString()}</span>
            <span className="del">-{stats.removed.toLocaleString()}</span>
          </span>
        )}
        <span className="tf-caret">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="tf-body">
          {visible.map((file) => {
            const previewable = isPreviewableText(file.path);
            return (
              <div className="tf-row" key={file.path}>
                <KindIcon kind={file.kind} />
                <button
                  type="button"
                  className="tf-name"
                  title={previewable ? `${file.path}（点击预览）` : file.path}
                  onClick={() =>
                    previewable ? onPreview(file.path) : onDownload(file.path)
                  }
                >
                  {file.path}
                </button>
                {lineStats(file)}
                <span className="tf-actions">
                  {previewable && (
                    <button
                      type="button"
                      className="mini-btn"
                      title="预览文本内容"
                      onClick={() => onPreview(file.path)}
                    >
                      预览
                    </button>
                  )}
                  <button
                    type="button"
                    className="mini-btn"
                    title="下载该文件"
                    disabled={downloading === file.path}
                    onClick={() => onDownload(file.path)}
                  >
                    {downloading === file.path ? "下载中…" : "下载"}
                  </button>
                </span>
              </div>
            );
          })}

          {files.length > 5 && (
            <button type="button" className="tf-more" onClick={() => setShowAll((value) => !value)}>
              {showAll ? "收起文件列表 ▴" : `全部 ${files.length} 个文件 ▾`}
            </button>
          )}
          {truncated && <div className="tf-note muted">本轮文件过多，仅展示前 100 个。</div>}
        </div>
      )}
    </div>
  );
}

/**
 * 结果文件卡片。
 *
 * 点击整张卡片 = 下载（用户最常用的动作）；文本类额外提供「预览」按钮。
 * 卡片主体是 div：按钮不能嵌套在 button 里，用 stopPropagation 区分。
 */
export function ResultFilesCard({
  files,
  onPreview,
  onDownload,
  downloading,
}: { files: ResultFile[] } & TurnFilesActions) {
  if (files.length === 0) return null;

  return (
    <div className="result-files">
      {files.map((file) => {
        const previewable = isPreviewableText(file.path);
        return (
          <div
            className="result-card"
            key={file.path}
            role="button"
            tabIndex={0}
            title={`点击下载 ${file.name}`}
            onClick={() => onDownload(file.path)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onDownload(file.path);
              }
            }}
          >
            <KindIcon kind={file.kind} />
            <div className="result-main">
              <div className="result-title" title={file.path}>
                {file.title || file.name}
              </div>
              <div className="result-meta">
                {kindLabel(file.kind)}
                {file.size > 0 ? ` · ${formatBytes(file.size)}` : ""} · {file.name}
              </div>
            </div>
            <div className="result-actions">
              {previewable && (
                <button
                  type="button"
                  className="mini-btn"
                  title="预览文本内容"
                  onClick={(event) => {
                    event.stopPropagation();
                    onPreview(file.path);
                  }}
                >
                  预览
                </button>
              )}
              <button
                type="button"
                className="btn primary"
                disabled={downloading === file.path}
                onClick={(event) => {
                  event.stopPropagation();
                  onDownload(file.path);
                }}
              >
                {downloading === file.path ? "下载中…" : "下载"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
