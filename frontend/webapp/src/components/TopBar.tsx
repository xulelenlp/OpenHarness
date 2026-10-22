import { ChevronDownIcon, FolderIcon, MenuIcon } from "../icons";
import type { AgentInfo, ConversationSummary } from "../types";

interface TopBarProps {
  sessionTitle: string;
  pendingApproval: boolean;
  streaming: boolean;
  activeSummary?: ConversationSummary;
  selectedAgent: string;
  setSelectedAgent: (id: string) => void;
  agents: AgentInfo[];
  markdownDefault: boolean;
  setMarkdownDefault: (value: boolean) => void;
  setRenderOverride: (value: Record<number, boolean>) => void;
  drawerOpen: boolean;
  sidebarOpen: boolean;
  onOpenSidebar: () => void;
  onToggleDrawer: () => void;
}

export function TopBar({
  sessionTitle,
  pendingApproval,
  streaming,
  activeSummary,
  selectedAgent,
  setSelectedAgent,
  agents,
  markdownDefault,
  setMarkdownDefault,
  setRenderOverride,
  drawerOpen,
  sidebarOpen,
  onOpenSidebar,
  onToggleDrawer,
}: TopBarProps) {
  return (
        <header className="topbar">
          <button
            type="button"
            className="icon-btn mobile-menu"
            title="打开会话列表"
            aria-label="打开会话列表"
            aria-expanded={sidebarOpen}
            onClick={() => onOpenSidebar()}
          >
            <MenuIcon size={18} />
          </button>

          <div className="topbar-title">
            <div className="topbar-title-row">
              <h1 title={sessionTitle}>{sessionTitle}</h1>
              {pendingApproval ? (
                <span className="status-pill waiting">
                  <span className="status-dot" />
                  等待确认
                </span>
              ) : streaming ? (
                <span className="status-pill running">
                  <span className="status-dot" />
                  执行中
                </span>
              ) : null}
            </div>
            <span className="topbar-sub">
              {activeSummary?.message_count ? `${activeSummary.message_count} 条消息` : "新对话"}
              {activeSummary?.model ? ` · ${activeSummary.model}` : ""}
            </span>
          </div>

          <div className="topbar-actions">
            <div className="agent-select">
              <label htmlFor="agent">智能体</label>
              <div className="select-wrap">
                <select
                  id="agent"
                  value={selectedAgent}
                  onChange={(e) => setSelectedAgent(e.target.value)}
                  disabled={streaming}
                >
                  <option value="">自动编排（推荐）</option>
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name || a.id}
                    </option>
                  ))}
                </select>
                <ChevronDownIcon size={14} />
              </div>
            </div>

            <label
              className="switch-toggle"
              title="助手回答按 Markdown 渲染（表格 / 代码块 / 列表）；关闭后显示原文"
            >
              <input
                type="checkbox"
                checked={markdownDefault}
                onChange={(e) => {
                  setMarkdownDefault(e.target.checked);
                  setRenderOverride({});
                }}
                disabled={streaming}
              />
              <span className="switch-track" aria-hidden="true">
                <span />
              </span>
              <span className="switch-label">Markdown</span>
            </label>

            <button
              type="button"
              className={`icon-btn${drawerOpen ? " active" : ""}`}
              title="列出本会话临时目录里的文件（含 Agent 生成的脚本），可下载 / 预览"
              aria-label="打开会话文件"
              aria-expanded={drawerOpen}
              onClick={() => onToggleDrawer()}
            >
              <FolderIcon size={17} />
              <span className="icon-btn-label">文件</span>
            </button>
          </div>
        </header>
  );
}
