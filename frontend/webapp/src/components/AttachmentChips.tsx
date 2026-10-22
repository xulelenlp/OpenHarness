import { formatBytes, parseStatusLabel } from "../attachments";
import { CloseIcon, FileIcon } from "../icons";
import type { AttachmentInfo } from "../types";

/** 附件类型图标：统一文件轮廓，用颜色区分 Word / Excel / 文本。 */
function AttachmentIcon({ kind }: { kind: string }) {
  return <FileIcon size={14} className={`att-kind ${kind}`} />;
}

/**
 * 附件芯片列表：输入框上方（可删除）与用户气泡下方（只读）共用。
 */
export function AttachmentChips({
  items,
  onRemove,
  disabled,
}: {
  items: AttachmentInfo[];
  onRemove?: (attachmentId: string) => void;
  disabled?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="att-chips">
      {items.map((a) => (
        <span
          key={a.attachment_id}
          className={`att-chip ${a.parse_status}`}
          title={`${a.stored_path}\n${parseStatusLabel(a)}`}
        >
          <span className="att-icon">
            <AttachmentIcon kind={a.kind} />
          </span>
          <span className="att-name">{a.name}</span>
          <span className="att-size">{formatBytes(a.size)}</span>
          <span className="att-status">{parseStatusLabel(a)}</span>
          {onRemove && (
            <button
              type="button"
              className="att-remove"
              title="移除该附件"
              aria-label={`移除附件 ${a.name}`}
              disabled={disabled}
              onClick={() => onRemove(a.attachment_id)}
            >
              <CloseIcon size={12} />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}
