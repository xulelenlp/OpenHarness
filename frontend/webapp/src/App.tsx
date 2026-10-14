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
import type { AgentInfo, ChatMessage, ConversationSummary, Step } from "./types";

interface AgentListResponseShape {
  agents: AgentInfo[];
  default_agent?: string;
}

interface ConversationListShape {
  conversations: ConversationSummary[];
}

interface ConversationDetailShape {
  conversation_id: string;
  messages?: Array<{ role: string; content: unknown }>;
  turns?: Array<{ turn_id: string; message_id?: string; events?: Array<Record<string, unknown>> }>;
}

type ApprovalAction = (requestId: string, decision: "approve" | "deny" | "always", reply?: string) => void;

function shortId(id: string): string {
  return id.slice(0, 8);
}

// ---------------------------------------------------------------------------
// 事件 → 步骤 归约（实时流与历史回放共用同一套逻辑）
// ---------------------------------------------------------------------------

interface TurnAccumulator {
  content: string;
  steps: Step[];
  agentName?: string;
  seq: number;
  /** 正在执行的工具名 → 对应 step.key，用于 tool_progress / tool_completed 回填。 */
  currentTool: Map<string, string>;
  /** 当前思考步骤的 key；正文/工具开始时置 done 并清空。 */
  reasoningKey: string | null;
}

function makeAccumulator(): TurnAccumulator {
  return { content: "", steps: [], seq: 0, currentTool: new Map(), reasoningKey: null };
}

function reduceEvent(acc: TurnAccumulator, evt: Record<string, unknown>): void {
  const type = evt.type as string;
  const nextKey = () => `s${acc.seq++}`;
  const finishReasoning = () => {
    if (acc.reasoningKey) {
      const rstep = acc.steps.find((s) => s.key === acc.reasoningKey);
      if (rstep && rstep.status === "running") rstep.status = "done";
      acc.reasoningKey = null;
    }
  };

  switch (type) {
    case "agent_routed":
      acc.agentName = (evt.agent as string) ?? acc.agentName;
      break;

    case "status": {
      const text = (evt.text as string) ?? "";
      acc.steps.push({
        key: nextKey(),
        kind: "status",
        status: "done",
        title: text || "状态",
        text,
        ts: evt.ts as number | undefined,
      });
      break;
    }

    case "tool_started": {
      finishReasoning();
      const name = (evt.tool_name as string) ?? "tool";
      const key = nextKey();
      acc.currentTool.set(name, key);
      acc.steps.push({
        key,
        kind: "tool",
        status: "running",
        title: name,
        toolName: name,
        toolInput: evt.tool_input,
        ts: evt.ts as number | undefined,
      });
      break;
    }

    case "tool_progress": {
      const name = (evt.tool_name as string) ?? "";
      const key = acc.currentTool.get(name);
      const text = (evt.text as string) ?? "";
      if (key && text) {
        const step = acc.steps.find((s) => s.key === key);
        if (step) step.toolProgress = (step.toolProgress ?? "") + text;
      }
      break;
    }

    case "tool_completed": {
      const name = (evt.tool_name as string) ?? "";
      const key = acc.currentTool.get(name);
      if (key) {
        const step = acc.steps.find((s) => s.key === key);
        if (step) {
          step.status = evt.is_error ? "error" : "done";
          step.toolOutput = evt.output;
        }
        acc.currentTool.delete(name);
      }
      break;
    }

    case "approval_required":
      acc.steps.push({
        key: nextKey(),
        kind: "approval",
        status: "running",
        title: "需要审批",
        requestId: evt.request_id as string | undefined,
        approvalKind: evt.kind as Step["approvalKind"],
        payload: evt.payload as Record<string, unknown> | undefined,
        ts: evt.ts as number | undefined,
      });
      break;

    case "error":
      acc.steps.push({
        key: nextKey(),
        kind: "error",
        status: "error",
        title: (evt.text as string) ?? "出错了",
        text: evt.text as string | undefined,
        ts: evt.ts as number | undefined,
      });
      break;

    case "reasoning_delta": {
      const text = (evt.text as string) ?? "";
      if (!text) break;
      if (!acc.reasoningKey) {
        const key = nextKey();
        acc.reasoningKey = key;
        acc.steps.push({
          key,
          kind: "reasoning",
          status: "running",
          title: "思考",
          text,
          ts: evt.ts as number | undefined,
        });
      } else {
        const rstep = acc.steps.find((s) => s.key === acc.reasoningKey);
        if (rstep) rstep.text = (rstep.text ?? "") + text;
      }
      break;
    }

    case "assistant_delta":
      finishReasoning();
      acc.content += (evt.text as string) ?? "";
      break;

    case "assistant_complete":
      acc.content = (evt.text as string) ?? acc.content;
      break;

    case "turn_complete":
      for (const s of acc.steps) if (s.status === "running") s.status = "done";
      break;
  }
}

/** 从 snapshot.messages 的 content 提取纯文本（兼容 TextBlock 数组与字符串）。 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object" && "text" in block) {
          return String((block as { text: unknown }).text);
        }
        return "";
      })
      .join("");
  }
  return "";
}

function approvalDesc(step: Step): string {
  const p = step.payload ?? {};
  if (step.approvalKind === "permission") return `工具「${String(p.tool ?? "")}」请求执行`;
  if (step.approvalKind === "question") return String(p.question ?? "请补充信息");
  return `修改文件：${String(p.path ?? "")}`;
}

// ---------------------------------------------------------------------------
// 步骤卡片
// ---------------------------------------------------------------------------

function DetailBlock({ label, data, text }: { label: string; data?: unknown; text?: string }) {
  const body = text ?? (typeof data === "string" ? data : JSON.stringify(data, null, 2));
  return (
    <div className="step-detail">
      <div className="step-detail-label">{label}</div>
      <pre>{String(body).slice(0, 4000)}</pre>
    </div>
  );
}

function StepCard({
  step,
  collapsed,
  onToggle,
  onApproval,
}: {
  step: Step;
  collapsed: boolean;
  onToggle: () => void;
  onApproval?: ApprovalAction;
}) {
  const [reply, setReply] = useState("");
  const icon =
    step.kind === "tool"
      ? "🔧"
      : step.kind === "approval"
        ? "⚠️"
        : step.kind === "error"
          ? "❌"
          : step.kind === "reasoning"
            ? "💭"
            : "ℹ️";
  const badge =
    step.kind === "reasoning" && step.status === "running"
      ? "思考中"
      : step.status === "running"
        ? "运行中"
        : step.status === "error"
          ? "失败"
          : "完成";

  return (
    <div className={`step-card ${step.kind} ${step.status}`}>
      <button className="step-head" onClick={onToggle}>
        <span className="step-icon">{icon}</span>
        <span className="step-title">{step.title}</span>
        <span className={`step-badge ${step.status}`}>{badge}</span>
        <span className="step-caret">{collapsed ? "▸" : "▾"}</span>
      </button>

      {!collapsed && (
        <div className="step-body">
          {step.kind === "tool" && (
            <>
              {step.toolInput !== undefined && <DetailBlock label="输入" data={step.toolInput} />}
              {step.toolProgress ? <DetailBlock label="进度" text={step.toolProgress} /> : null}
              {step.toolOutput !== undefined && <DetailBlock label="输出" data={step.toolOutput} />}
            </>
          )}

          {step.kind === "approval" && (
            <div className="approval-step">
              <div className="approval-desc">{approvalDesc(step)}</div>
              {step.payload?.reason ? <div className="muted">原因：{String(step.payload.reason)}</div> : null}

              {step.status === "running" && step.requestId && onApproval ? (
                step.approvalKind === "question" ? (
                  <div className="approval-question">
                    <input
                      value={reply}
                      placeholder="输入补充信息…"
                      autoFocus
                      onChange={(e) => setReply(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && reply.trim()) {
                          e.preventDefault();
                          onApproval(step.requestId!, "approve", reply.trim());
                        }
                      }}
                    />
                    <button
                      className="btn primary"
                      disabled={!reply.trim()}
                      onClick={() => onApproval(step.requestId!, "approve", reply.trim())}
                    >
                      提交
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="approval-actions">
                      <button className="btn primary" onClick={() => onApproval(step.requestId!, "approve")}>
                        允许
                      </button>
                      <button className="btn" onClick={() => onApproval(step.requestId!, "always")}>
                        总是允许
                      </button>
                      <button className="btn danger" onClick={() => onApproval(step.requestId!, "deny")}>
                        拒绝
                      </button>
                    </div>
                    <div className="muted approval-hint">
                      本轮内同一工具只确认一次；点「总是允许」则本会话后续都不再询问。
                    </div>
                  </>
                )
              ) : (
                step.text ? <div className="step-text">{step.text}</div> : null
              )}
            </div>
          )}

          {(step.kind === "status" || step.kind === "error" || step.kind === "reasoning") && step.text ? (
            <div className="step-text">{step.text}</div>
          ) : null}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

export default function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem("qaapp_token") ?? "");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string>("");
  const [stepOpen, setStepOpen] = useState<Record<string, boolean>>({});

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
    setStepOpen({});
    try {
      const detail = (await getConversation(id)) as ConversationDetailShape;
      // 用户消息文本来自 snapshot.messages；步骤序列来自 turns[].events。
      const userTexts: string[] = [];
      for (const m of detail.messages ?? []) {
        if (m.role === "user") userTexts.push(extractText(m.content));
      }
      const rebuilt: ChatMessage[] = [];
      (detail.turns ?? []).forEach((turn, i) => {
        const acc = makeAccumulator();
        for (const evt of turn.events ?? []) reduceEvent(acc, evt);
        const userText = userTexts[i] ?? "";
        if (userText) {
          rebuilt.push({ role: "user", content: userText, turnId: turn.turn_id, steps: [] });
        }
        rebuilt.push({
          role: "assistant",
          content: acc.content,
          agentName: acc.agentName,
          turnId: turn.turn_id,
          steps: acc.steps,
        });
      });
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
    setStreaming(true);

    const userMsg: ChatMessage = { role: "user", content, steps: [] };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", streaming: true, steps: [] };
    const assistantIndex = messages.length + 1;
    assistantIndexRef.current = assistantIndex;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;

    const acc = makeAccumulator();
    const onEvent = (evt: Record<string, unknown>) => {
      // 观测日志：浏览器控制台查看收到的每个 SSE 事件，便于排查断点。
      // eslint-disable-next-line no-console
      console.log(
        "[sse]",
        evt.type,
        evt.type === "assistant_delta" || evt.type === "reasoning_delta"
          ? `len=${((evt.text as string) ?? "").length}`
          : evt.tool_name
            ? `tool=${evt.tool_name}`
            : "",
      );
      reduceEvent(acc, evt);
      const idx = assistantIndexRef.current;
      const type = evt.type as string;
      if (type === "error") {
        setError((evt.text as string) ?? "出错了");
      }
      updateMessage(idx, (m) => ({
        ...m,
        content: acc.content,
        agentName: acc.agentName ?? m.agentName,
        steps: JSON.parse(JSON.stringify(acc.steps)) as Step[],
        error: m.error || type === "error",
      }));
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

  const handleApproval = useCallback<ApprovalAction>(
    async (requestId, decision, reply) => {
      if (!activeId) return;
      const label = reply
        ? "已提交补充信息"
        : decision === "approve"
          ? "已允许"
          : decision === "always"
            ? "已总是允许"
            : "已拒绝";
      // 乐观更新：把对应审批步骤置为完成，并把标题改成「结果 · 目标」，
      // 否则一堆已处理卡片都写着「需要审批」，既刷屏又难分辨。
      setMessages((prev) =>
        prev.map((m) =>
          m.role !== "assistant"
            ? m
            : {
                ...m,
                steps: m.steps.map((s) => {
                  if (
                    s.kind !== "approval" ||
                    s.requestId !== requestId ||
                    s.status !== "running"
                  ) {
                    return s;
                  }
                  const target =
                    s.approvalKind === "permission"
                      ? `工具「${String(s.payload?.tool ?? "")}」`
                      : s.approvalKind === "edit"
                        ? `文件「${String(s.payload?.path ?? "").split("/").pop() ?? ""}」`
                        : "补充信息";
                  return { ...s, status: "done" as const, title: `${label} · ${target}`, text: label };
                }),
              },
        ),
      );
      try {
        await resolveApproval(activeId, requestId, decision, reply);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [activeId],
  );

  const toggleStep = useCallback((key: string) => {
    setStepOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

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

              {m.role === "assistant" && m.steps.length > 0 && (
                <div className="steps">
                  {m.steps.map((s) => {
                    const openKey = `${i}:${s.key}`;
                    // 待处理的审批必须展开（需用户操作）；思考默认展开便于看过程；
                    // 其余（含已处理的审批）默认折叠，避免重复卡片刷屏。
                    const needsAction = s.kind === "approval" && s.status === "running";
                    const collapsed =
                      needsAction || s.kind === "reasoning" ? false : !stepOpen[openKey];
                    return (
                      <StepCard
                        key={s.key}
                        step={s}
                        collapsed={collapsed}
                        onToggle={() => toggleStep(openKey)}
                        onApproval={handleApproval}
                      />
                    );
                  })}
                </div>
              )}

              <div className={`bubble ${m.error ? "error" : ""}`}>
                {m.content || (m.streaming ? "思考中…" : "")}
              </div>
            </div>
          ))}
        </div>

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
