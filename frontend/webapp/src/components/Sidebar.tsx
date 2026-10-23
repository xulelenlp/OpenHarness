import { BRAND } from "../branding";
import { conversationTime, shortId, truncateText } from "../chat/format";
import {
  BrandMark,
  CloseIcon,
  PlusIcon,
  SearchIcon,
} from "../icons";
import type { ConversationSummary } from "../types";

interface SidebarProps {
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  conversations: ConversationSummary[];
  filteredConversations: ConversationSummary[];
  activeId: string | null;
  conversationFilter: string;
  setConversationFilter: (value: string) => void;
  onNewConversation: () => void;
  onOpenConversation: (id: string) => void;
  /** 只读账户不能新建对话。 */
  readOnly?: boolean;
}

export function Sidebar({
  sidebarOpen,
  setSidebarOpen,
  conversations,
  filteredConversations,
  activeId,
  conversationFilter,
  setConversationFilter,
  onNewConversation,
  onOpenConversation,
  readOnly = false,
}: SidebarProps) {
  return (
    <>
      {sidebarOpen ? (
        <div
          className="sidebar-backdrop"
          aria-hidden="true"
          onClick={() => setSidebarOpen(false)}
        />
      ) : null}

      <aside className="sidebar" aria-label="会话列表">
        <div className="sidebar-brand">
          <BrandMark size={34} />
          <div className="sidebar-brand-text">
            <strong>{BRAND.appName}</strong>
            <span>{BRAND.subtitle}</span>
          </div>
          <button
            type="button"
            className="icon-btn sidebar-close"
            title="收起会话列表"
            aria-label="收起会话列表"
            onClick={() => setSidebarOpen(false)}
          >
            <CloseIcon size={17} />
          </button>
        </div>

        <button
          type="button"
          className="new-chat"
          title={readOnly ? "只读账户不能新建对话" : undefined}
          disabled={readOnly}
          onClick={() => void onNewConversation()}
        >
          <PlusIcon size={16} />
          新建对话
        </button>

        <div className="sidebar-search">
          <SearchIcon size={15} />
          <input
            value={conversationFilter}
            onChange={(event) => setConversationFilter(event.target.value)}
            placeholder="搜索对话"
            aria-label="搜索对话"
          />
          {conversationFilter ? (
            <button
              type="button"
              className="search-clear"
              title="清空搜索"
              aria-label="清空搜索"
              onClick={() => setConversationFilter("")}
            >
              <CloseIcon size={12} />
            </button>
          ) : null}
        </div>

        <div className="conversation-list">
          {filteredConversations.map((c) => {
            const title = truncateText(c.summary || c.preview || "新对话", 30) || "新对话";
            const time = conversationTime(c.created_at);
            return (
              <button
                type="button"
                key={c.conversation_id}
                className={`conversation-item ${c.conversation_id === activeId ? "active" : ""}`}
                onClick={() => void onOpenConversation(c.conversation_id)}
                title={c.summary || c.preview || c.conversation_id}
              >
                <span className="conv-title">{title}</span>
                <span className="conv-meta">
                  <span>#{shortId(c.conversation_id)}</span>
                  {c.message_count ? <span>{c.message_count} 条</span> : null}
                  {time ? <span>{time}</span> : null}
                </span>
              </button>
            );
          })}
          {filteredConversations.length === 0 && (
            <p className="sidebar-empty">
              {conversations.length === 0 ? "暂无对话，点击「新建对话」开始" : "没有匹配的对话"}
            </p>
          )}
        </div>

        <div className="sidebar-foot">
          <span className="status-dot" />
          {BRAND.productName}
        </div>
      </aside>
    </>
  );
}
