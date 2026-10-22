import { formatBytes, isPreviewableText, workspaceLabel } from "../attachments";
import {
  CheckIcon,
  CloseIcon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  FileIcon,
  FolderIcon,
} from "../icons";
import type { ConversationWorkspace, WorkspaceFile } from "../types";

interface WorkspaceDrawerProps {
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean) => void;
  activeId: string | null;
  workspace: ConversationWorkspace | null;
  workspaceLoading: boolean;
  copiedDir: boolean;
  downloadingPath: string | null;
  fileGroups: Array<[string, WorkspaceFile[]]>;
  onCopyDir: () => void;
  onPreview: (path: string) => void;
  onDownload: (path: string) => void;
}

export function WorkspaceDrawer({
  drawerOpen,
  setDrawerOpen,
  activeId,
  workspace,
  workspaceLoading,
  copiedDir,
  downloadingPath,
  fileGroups,
  onCopyDir,
  onPreview,
  onDownload,
}: WorkspaceDrawerProps) {
  return (
    <>
      {drawerOpen ? (
        <div
          className="drawer-backdrop"
          aria-hidden="true"
          onClick={() => setDrawerOpen(false)}
        />
      ) : null}

      {drawerOpen && (
        <aside className="file-drawer" aria-label="会话文件">
          <div className="drawer-head">
            <div className="drawer-title">
              <FolderIcon size={17} />
              <strong>会话文件</strong>
            </div>
            <button
              type="button"
              className="icon-btn"
              title="关闭面板"
              aria-label="关闭面板"
              onClick={() => setDrawerOpen(false)}
            >
              <CloseIcon size={16} />
            </button>
          </div>

          <div className="drawer-dir">
            <code title={workspace?.dir || ""}>{workspace?.dir || "（未加载）"}</code>
            <button
              type="button"
              className="mini-btn"
              disabled={!workspace?.dir}
              onClick={() => void onCopyDir()}
            >
              {copiedDir ? <CheckIcon size={12} /> : <CopyIcon size={12} />}
              {copiedDir ? "已复制" : "复制路径"}
            </button>
          </div>

          <div className="drawer-body">
            {!activeId && <p className="muted">请先新建或选择一个会话</p>}
            {activeId && workspaceLoading && <p className="muted">加载中…</p>}
            {activeId && !workspaceLoading && workspace && workspace.files.length === 0 && (
              <p className="muted">该会话目录暂无文件</p>
            )}
            {activeId && !workspaceLoading && !workspace && <p className="muted">暂无数据</p>}

            {fileGroups.map(([dir, files]) => (
              <div className="ws-group" key={dir}>
                <div className="ws-group-title">
                  <span>{dir}</span>
                  <span>{files.length}</span>
                </div>
                {files.map((f) => (
                  <div className="ws-file" key={f.path}>
                    <span className="ws-file-icon">
                      <FileIcon size={14} />
                    </span>
                    <button
                      type="button"
                      className="ws-file-name"
                      title={isPreviewableText(f.path) ? `${f.path}（点击预览）` : f.path}
                      disabled={!isPreviewableText(f.path)}
                      onClick={() => void onPreview(f.path)}
                    >
                      {f.path.split("/").pop()}
                    </button>
                    <div className="ws-file-meta">{workspaceLabel(f)}</div>
                    <div className="ws-file-actions">
                      {isPreviewableText(f.path) ? (
                        <button
                          type="button"
                          className="mini-btn"
                          title="预览"
                          aria-label={`预览 ${f.path}`}
                          onClick={() => void onPreview(f.path)}
                        >
                          <EyeIcon size={13} />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="mini-btn"
                        title="下载"
                        aria-label={`下载 ${f.path}`}
                        disabled={downloadingPath === f.path}
                        onClick={() => void onDownload(f.path)}
                      >
                        <DownloadIcon size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>

          <div className="drawer-foot muted">
            共 {workspace?.files.length ?? 0} 个文件 · {formatBytes(workspace?.total_bytes ?? 0)}
            {workspace?.truncated ? "（文件过多，未列全）" : ""}
            {workspaceLoading ? " · 刷新中…" : ""}
          </div>
        </aside>
      )}
    </>
  );
}
