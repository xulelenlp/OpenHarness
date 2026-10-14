import { useCallback, useEffect, useRef, useState } from "react";
import {
  createConversation,
  getConversation,
  interruptConversation,
  listAgents,
  listConversations,
  resolveApproval,
  streamMessage,
} from "./api";
import type { AgentInfo, ApprovalEvent, ChatMessage, ConversationSummary, ToolCallEvent } from "./types";

interface AgentListResponseShape {
  agents: AgentInfo[];
  default_agent?: string;
}

interface ConversationListShape {
  conversations: ConversationSummary[];
}

interface ConversationDetailShape {
  conversation_id: string;
  turns?: Array<{
    turn_id: string;
    agent?: string;
    user_message?: string;
    assistant_message?: string;
    tool_calls?: unknown[];
  }>;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

export default function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem("qaapp_token") ?? "");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [streaming, setStreaming] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<ApprovalEvent | null>(null);
  const [error, setError] = useState<string>("");
  const [historyOpen, setHistoryOpen] = useState<Record<string, boolean>>({});

  const abortRef = useRef<AbortController | null>(null);
  const assistantIndexRef = useRef<number>(-1);

  useEffect(() => {
    localStorage.setItem("qaapp_token", token);
  }, [token]);

  const refreshAgents = useCallback(async () => {
    try {
      const data = await listAgents();
      setAgents((data as unknown as AgentListResponseShape).agents ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      const data = await listConversations();
      setConversations((data as unknown as ConversationListShape).conversations ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshAgents();
    void refreshConversations();
  }, [refreshAgents, refreshConversations]);

  const updateMessage = useCallback((index: number, updater: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => prev.map((m, i) => (i === index ? updater(m) : m)));
  }, []);

  const openConversation = useCallback(async (id: string) => {
    setActiveId(id);
    setMessages([]);
    setPendingApproval(null);
    try {
      const detail = (await getConversation(id)) as ConversationDetailShape;
      const rebuilt: ChatMessage[] = [];
      for (const turn of detail.turns ?? []) {
        if (turn.user_message) {
          rebuilt.push({ role: "user", content: turn.user_message, turnId: turn.turn_id });
        }
        if (turn.assistant_message) {
          rebuilt.push({
            role: "assistant",
            content: turn.assistant_message,
            agentName: turn.agent,
            turnId: turn.turn_id,
            toolEvents: (turn.tool_calls ?? []) as unknown as ToolCallEvent[],
          });
        }
      }
      setMessages(rebuilt);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const handleNewConversation = useCallback(async () => {
    try {
      const { conversation_id } = await createConversation();
      await refreshConversations();
      setActiveId(conversation_id);
      setMessages([]);
      setPendingApproval(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [refreshConversations]);

  const handleSend = useCallback(async () => {
    const content = input.trim();
    if (!content || streaming) return;
    let conversationId = activeId;
    if (!conversationId) {
      try {
        conversationId = (await createConversation()).conversation_id;
        setActiveId(conversationId);
        await refreshConversations();
      } catch (e) {
        setError((e as Error).message);
        return;
      }
    }

    setInput("");
    setError("");
    setPendingApproval(null);
    setStreaming(true);

    const userMsg: ChatMessage = { role: "user", content };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", streaming: true, toolEvents: [] };
    const assistantIndex = messages.length + 1;
    assistantIndexRef.current = assistantIndex;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;

    const onEvent = (evt: Record<string, unknown>) => {
      const type = evt.type as string;
      const idx = assistantIndexRef.current;
      if (type === "agent_routed") {
        updateMessage(idx, (m) => ({ ...m, agentName: (evt.agent as string) ?? m.agentName }));
      } else if (type === "assistant_delta") {
        updateMessage(idx, (m) => ({ ...m, content: m.content + ((evt.text as string) ?? "") }));
      } else if (type === "assistant_complete") {
        updateMessage(idx, (m) => ({ ...m, content: (evt.text as string) ?? m.content }));
      } else if (type === "tool_started" || type === "tool_completed" || type === "tool_progress") {
        updateMessage(idx, (m) => ({
          ...m,
          toolEvents: [...(m.toolEvents ?? []), evt as unknown as ToolCallEvent],
        }));
      } else if (type === "approval_required") {
        setPendingApproval({
          request_id: evt.request_id as string,
          kind: evt.kind as ApprovalEvent["kind"],
          payload: evt.payload as Record<string, unknown>,
        });
      } else if (type === "error") {
        updateMessage(idx, (m) => ({ ...m, content: m.content || `⚠️ ${evt.text ?? "出错了"}`, error: true }));
        setError((evt.text as string) ?? "出错了");
      } else if (type === "turn_complete") {
        updateMessage(idx, (m) => ({ ...m, streaming: false }));
      }
    };

    try {
      await streamMessage(conversationId, content, selectedAgent || undefined, onEvent, controller.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        updateMessage(assistantIndexRef.current, (m) => ({
          ...m,
          content: m.content || `⚠️ ${(e as Error).message}`,
          error: true,
          streaming: false,
        }));
      }
    } finally {
      setStreaming(false);
      setPendingApproval(null);
      abortRef.current = null;
      void refreshConversations();
    }
  }, [activeId, input, messages.length, refreshConversations, selectedAgent, streaming, updateMessage]);

  const handleInterrupt = useCallback(async () => {
    if (!activeId) return;
    abortRef.current?.abort();
    try {
      await interruptConversation(activeId);
    } catch {
      // 忽略
    }
  }, [activeId]);

  const handleApproval = useCallback(
    async (decision: "approve" | "deny" | "always") => {
      if (!activeId || !pendingApproval) return;
      const { request_id } = pendingApproval;
      setPendingApproval(null);
      try {
        await resolveApproval(activeId, request_id, decision);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [activeId, pendingApproval],
  );

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>问答产品</h1>
          <button className="btn primary" onClick={() => void handleNewConversation()}>
            + 新建对话
          </button>
        </div>

        <div className="token-box">
          <input
            type="password"
            placeholder="Bearer Token（可选）"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        </div>

        <div className="conversation-list">
          {conversations.map((c) => (
            <button
              key={c.conversation_id}
              className={`conversation-item ${c.conversation_id === activeId ? "active" : ""}`}
              onClick={() => void openConversation(c.conversation_id)}
            >
              <span className="conv-id">#{shortId(c.conversation_id)}</span>
              <span className="conv-preview">{c.preview || "新对话"}</span>
            </button>
          ))}
          {conversations.length === 0 && <p className="muted">暂无对话，点击「新建对话」开始</p>}
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="agent-select">
            <label htmlFor="agent">Agent</label>
            <select
              id="agent"
              value={selectedAgent}
              onChange={(e) => setSelectedAgent(e.target.value)}
              disabled={streaming}
            >
              <option value="">自动路由</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name || a.id}
                </option>
              ))}
            </select>
          </div>
          {streaming && (
            <button className="btn warn" onClick={() => void handleInterrupt()}>
              中断
            </button>
          )}
        </header>

        <div className="messages">
          {messages.map((m, i) => (
            <div key={i} className={`message ${m.role}`}>
              <div className="message-head">
                <span className="role">{m.role === "user" ? "我" : m.agentName || "助手"}</span>
                {m.turnId && <span className="turn-id">turn #{shortId(m.turnId)}</span>}
              </div>
              <div className={`bubble ${m.error ? "error" : ""}`}>
                {m.content || (m.streaming ? "思考中…" : "")}
                {m.streaming && <span className="cursor">▍</span>}
              </div>

              {(m.toolEvents?.length ?? 0) > 0 && (
                <div className="tools">
                  <button
                    className="tools-toggle"
                    onClick={() => setHistoryOpen((prev) => ({ ...prev, [String(i)]: !prev[String(i)] }))}
                  >
                    {historyOpen[String(i)] ? "▾" : "▸"} 工具执行过程（{m.toolEvents!.length}）
                  </button>
                  {historyOpen[String(i)] &&
                    m.toolEvents!.map((t, j) => (
                      <div key={j} className="tool-entry">
                        <span className="tool-name">
                          {(t as { tool_name?: string }).tool_name ?? (t as { type?: string }).type}
                        </span>
                        <pre>{JSON.stringify(t, null, 2).slice(0, 800)}</pre>
                      </div>
                    ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {pendingApproval && (
          <div className="approval-bar">
            <div className="approval-text">
              <strong>需要确认</strong>
              <span>
                {pendingApproval.kind === "permission"
                  ? `工具「${(pendingApproval.payload?.tool as string) ?? ""}」请求执行`
                  : pendingApproval.kind === "question"
                    ? `问题：${(pendingApproval.payload?.question as string) ?? ""}`
                    : `修改文件：${(pendingApproval.payload?.path as string) ?? ""}`}
              </span>
              {pendingApproval.payload?.reason ? (
                <span className="muted">原因：{pendingApproval.payload.reason as string}</span>
              ) : null}
            </div>
            <div className="approval-actions">
              <button className="btn primary" onClick={() => void handleApproval("approve")}>
                允许
              </button>
              <button className="btn" onClick={() => void handleApproval("always")}>
                总是允许
              </button>
              <button className="btn danger" onClick={() => void handleApproval("deny")}>
                拒绝
              </button>
            </div>
          </div>
        )}

        {error && (
          <div className="error-bar">
            {error}
            <button className="close" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}

        <div className="composer">
          <textarea
            value={input}
            placeholder="输入问题，回车发送（Shift+回车换行）"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
            disabled={streaming}
          />
          <button className="btn primary send" onClick={() => void handleSend()} disabled={streaming || !input.trim()}>
            发送
          </button>
        </div>
      </main>
    </div>
  );
}
