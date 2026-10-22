import { PREVIEW_CHARS } from "../chat/constants";
import { CloseIcon, FileIcon } from "../icons";

export interface PreviewState {
  name: string;
  text: string;
  truncated: boolean;
}

export function PreviewModal({
  preview,
  onClose,
}: {
  preview: PreviewState | null;
  onClose: () => void;
}) {
  if (!preview) return null;
  return (
    <div className="preview-mask" onClick={() => onClose()}>
          {/* 点面板内部不该关掉弹窗，所以拦住冒泡 */}
          <div
            className="preview-panel"
            role="dialog"
            aria-modal="true"
            aria-label="文件预览"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="preview-head">
              <div className="preview-title">
                <FileIcon size={16} />
                <strong title={preview.name}>{preview.name}</strong>
              </div>
              <button
                type="button"
                className="icon-btn"
                title="关闭预览"
                aria-label="关闭预览"
                autoFocus
                onClick={() => onClose()}
              >
                <CloseIcon size={16} />
              </button>
            </div>
            <pre>{preview.text}</pre>
            <div className="preview-foot muted">
              {preview.truncated ? `仅预览前 ${PREVIEW_CHARS} 个字符` : "已显示全部内容"}
            </div>
          </div>
    </div>
  );
}
