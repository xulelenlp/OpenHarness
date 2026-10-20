import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";
import {
  bootstrap,
  createConversation,
  fetchConversationFile,
  getConversation,
  hasStaticToken,
  interruptConversation,
  listAgents,
  listConversationFiles,
  listConversations,
  resolveApproval,
  streamMessage,
  uploadAttachments,
} from "./api";
import {
  MAX_FILES,
  SUPPORTED_SUFFIXES,
  formatBytes,
  isPreviewableText,
  parseStatusLabel,
  pickWithinLimit,
  workspaceLabel,
} from "./attachments";
import { makeAccumulator, reduceEvent } from "./turn";
import type { TurnAccumulator } from "./turn";
import { MarkdownView, useThrottledText } from "./markdown";
import type {
  AgentInfo,
  AttachmentInfo,
  ChatMessage,
  ConversationSummary,
  ConversationWorkspace,
  Step,
  WorkspaceFile,
} from "./types";

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

/** 文本预览只取前 2000 字符：大 CSV 全量渲染会卡死页面。 */
const PREVIEW_CHARS = 2000;

function shortId(id: string): string {
  return id.slice(0, 8);
}

/**
 * 复制文本。
 *
 * 注意：`navigator.clipboard` 只在**安全上下文**（https / localhost）可用，而本页
 * 常见部署方式是局域网 `http://<ip>:8787`，此时它不存在，必须回退到
 * `document.execCommand("copy")`，否则「复制」按钮会静默失效。
 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底实现
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

interface AssistantBubbleProps {
  content: string;
  streaming: boolean;
  /** true = 按 Markdown 渲染；false = 显示原始文本（源码）。 */
  markdown: boolean;
  error?: boolean;
}

/**
 * 助手气泡。
 *
 * 流式期间对 Markdown 渲染做节流（见 `useThrottledText`）：每个 SSE 增量都重新解析
 * 整篇 Markdown + 重建 React 树，是页面卡顿的主因；节流后按 ~150ms 合并渲染。
 */
function AssistantBubble({ content, streaming, markdown, error }: AssistantBubbleProps) {
  const throttled = useThrottledText(content, streaming ? 150 : 0);
  const text = streaming ? throttled : content;
  // 出错文案按纯文本显示：那是系统消息，不该被当成 Markdown 解释。
  const asMarkdown = markdown && !error;
  const cls = `bubble${asMarkdown && text ? " md" : ""}${error ? " error" : ""}`;

  if (!text) return <div className={cls}>{streaming ? "思考中…" : ""}</div>;
  if (!asMarkdown) return <div className={cls}>{text}</div>;
  return (
    <div className={cls}>
      <MarkdownView content={text} />
    </div>
  );
}

/** 附件类型小图标：只为一眼区分 Word / Excel / 文本，不追求覆盖所有 kind。 */
function kindIcon(kind: string): string {
  if (kind === "word") return "📘";
  if (kind === "excel") return "📗";
  if (kind === "text" || kind === "markdown" || kind === "csv" || kind === "log") return "📄";
  return "📎";
}

/**
 * 附件芯片列表。
 *
 * 同一组件复用于两处：输入框上方（可删除、受 streaming 禁用）与用户气泡下方
 * （只读历史）。`onRemove` 不传即为只读态。
 */
function AttachmentChips({
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
          <span className="att-icon">{kindIcon(a.kind)}</span>
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
              ×
            </button>
          )}
        </span>
      ))}
    </div>
  );
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
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string>("");
  const [stepOpen, setStepOpen] = useState<Record<string, boolean>>({});
  /** 助手回答是否按 Markdown 渲染（全局默认，存本地）。 */
  const [markdownDefault, setMarkdownDefault] = useState<boolean>(
    () => localStorage.getItem("qaapp_render_markdown") !== "0",
  );
  /** 单条消息的渲染模式覆盖：true = 强制 Markdown，false = 强制源码。 */
  const [renderOverride, setRenderOverride] = useState<Record<number, boolean>>({});
  /** 刚复制过的消息下标（用于按钮上的短暂反馈）。 */
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  // ---- 附件相关状态 ----
  /** 已上传但还没随消息发出的附件（输入框上方的 chip 列表）。 */
  const [attachments, setAttachments] = useState<AttachmentInfo[]>([]);
  /** 上传请求进行中：此时按钮禁用并显示「解析中…」。 */
  const [uploading, setUploading] = useState(false);
  /** 拖拽文件悬停在消息区 / 输入区时的整块高亮。 */
  const [dragging, setDragging] = useState(false);
  // ---- 会话文件抽屉 ----
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [workspace, setWorkspace] = useState<ConversationWorkspace | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  /** 文本文件预览弹窗内容（只放前 2000 字符）。 */
  const [preview, setPreview] = useState<{ name: string; text: string; truncated: boolean } | null>(null);
  /** 「复制路径」按钮的短暂反馈。 */
  const [copiedDir, setCopiedDir] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const assistantIndexRef = useRef<number>(-1);
  /** 本轮流式事件的累加器；供审批处理同步修改，避免被后续事件覆盖。 */
  const accRef = useRef<TurnAccumulator | null>(null);
  /** 正在提交的审批 id（同步判重），防止连点导致重复提交。 */
  const submittingApprovalsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    localStorage.setItem("qaapp_render_markdown", markdownDefault ? "1" : "0");
  }, [markdownDefault]);

  const handleCopyMessage = useCallback(async (index: number, text: string) => {
    if (await copyText(text)) {
      setCopiedIndex(index);
      window.setTimeout(() => setCopiedIndex((prev) => (prev === index ? null : prev)), 1500);
    }
  }, []);

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
    // 先向服务端建立浏览器会话：服务端读取 webapp.json 的 api_tokens 并签发
    // HttpOnly Cookie，因此页面不再需要用户手填 Bearer token。
    let cancelled = false;
    void (async () => {
      try {
        const info = await bootstrap();
        if (cancelled) return;
        if (info.auth_required && !info.browser_session && !hasStaticToken()) {
          setError(
            "服务端要求 Bearer token，但未启用浏览器会话。请启用 browser_auth，" +
              "或在构建前端时设置 VITE_API_TOKEN，或由反向代理注入 Authorization。",
          );
        }
      } catch (e) {
        if (!cancelled) setError(`初始化会话失败：${(e as Error).message}`);
      }
      if (cancelled) return;
      // 即使 bootstrap 失败也继续拉取：未配置 api_tokens 的部署本来就不需要会话。
      await Promise.all([refreshAgents(), refreshConversations()]);
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshAgents, refreshConversations]);

  const updateMessage = useCallback((index: number, updater: (m: ChatMessage) => ChatMessage) => {
    setMessages((prev) => prev.map((m, i) => (i === index ? updater(m) : m)));
  }, []);

  const openConversation = useCallback(async (id: string) => {
    setActiveId(id);
    setMessages([]);
    setStepOpen({});
    // 附件挂在会话上，切会话必须清空，否则会把 A 会话的附件发进 B 会话。
    setAttachments([]);
    setWorkspace(null);
    try {
      const detail = (await getConversation(id)) as ConversationDetailShape;
      // 用户消息文本来自 snapshot.messages；步骤序列与附件来自 turns[].events。
      const userTexts: string[] = [];
      for (const m of detail.messages ?? []) {
        if (m.role === "user") userTexts.push(extractText(m.content));
      }
      const rebuilt: ChatMessage[] = [];
      (detail.turns ?? []).forEach((turn, i) => {
        const acc = makeAccumulator();
        for (const evt of turn.events ?? []) reduceEvent(acc, evt);
        const userText = userTexts[i] ?? "";
        // 只发附件不写文字是合法的一轮，此时 content 为空但必须仍然渲染气泡与芯片。
        if (userText || acc.attachments?.length) {
          rebuilt.push({
            role: "user",
            content: userText,
            turnId: turn.turn_id,
            steps: [],
            attachments: acc.attachments,
          });
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
      setAttachments([]);
      setWorkspace(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [refreshConversations]);

  // ---- 会话文件抽屉 ----

  const refreshWorkspace = useCallback(async () => {
    if (!activeId) return;
    setWorkspaceLoading(true);
    try {
      setWorkspace(await listConversationFiles(activeId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorkspaceLoading(false);
    }
  }, [activeId]);

  // 抽屉打开（或切换会话）时拉取一次；关闭时不动，省掉无谓请求。
  useEffect(() => {
    if (drawerOpen && activeId) void refreshWorkspace();
  }, [drawerOpen, activeId, refreshWorkspace]);

  // 每轮流式结束（turn_complete → streaming 置回 false）后自动刷新，
  // 这样 Agent 新生成的脚本无需手动刷新就能出现在面板里。
  const wasStreamingRef = useRef(false);
  useEffect(() => {
    if (wasStreamingRef.current && !streaming && drawerOpen) void refreshWorkspace();
    wasStreamingRef.current = streaming;
  }, [streaming, drawerOpen, refreshWorkspace]);

  const handleCopyDir = useCallback(async () => {
    const dir = workspace?.dir;
    if (!dir) return;
    if (await copyText(dir)) {
      setCopiedDir(true);
      window.setTimeout(() => setCopiedDir(false), 1500);
    }
  }, [workspace?.dir]);

  /**
   * 下载：必须 fetch 成 Blob 再触发 <a download>。
   * 立刻 revokeObjectURL 会让部分浏览器（Safari / Firefox）取消下载，故延后释放。
   */
  const downloadFile = useCallback(
    async (file: WorkspaceFile) => {
      if (!activeId) return;
      try {
        const { blob, filename } = await fetchConversationFile(activeId, file.path);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [activeId],
  );

  /** 文本类文件点击预览：读 Blob 文本，只留前 2000 字符。 */
  const openPreview = useCallback(
    async (file: WorkspaceFile) => {
      if (!activeId) return;
      try {
        const { blob } = await fetchConversationFile(activeId, file.path);
        const text = await blob.text();
        setPreview({
          name: file.path,
          text: text.slice(0, PREVIEW_CHARS),
          truncated: text.length > PREVIEW_CHARS,
        });
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [activeId],
  );

  // ---- 附件上传 ----

  /**
   * 处理用户选中 / 拖入的文件。
   *
   * 顺序很关键：先本地校验（超量 / 类型不支持都写进错误条，不静默丢弃）→
   * 没有会话就先建（附件必须挂在某个会话下）→ 再上传。
   */
  const handleFilesPicked = useCallback(
    async (incoming: File[]) => {
      // streaming 期间不触发上传：那一轮的消息体已经发出，附件会挂错轮次。
      if (streaming || uploading || incoming.length === 0) return;

      const { accepted, rejected } = pickWithinLimit(attachments.length, incoming);
      if (rejected.length > 0) {
        setError(
          rejected
            .map((r) =>
              r.reason === "limit"
                ? `最多上传 ${MAX_FILES} 个文件，「${r.name}」未添加`
                : `不支持的文件类型：「${r.name}」（支持 ${SUPPORTED_SUFFIXES.join(" ")}）`,
            )
            .join("；"),
        );
      }
      if (accepted.length === 0) return;

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

      setUploading(true);
      try {
        const resp = await uploadAttachments(conversationId, accepted);
        setAttachments((prev) => [...prev, ...(resp.attachments ?? [])].slice(0, MAX_FILES));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setUploading(false);
      }
    },
    [activeId, attachments.length, refreshConversations, streaming, uploading],
  );

  const removeAttachment = useCallback((attachmentId: string) => {
    setAttachments((prev) => prev.filter((a) => a.attachment_id !== attachmentId));
  }, []);

  const handleFileInputChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      // 清空 value：否则再次选择同一个文件不会触发 change。
      e.target.value = "";
      void handleFilesPicked(files);
    },
    [handleFilesPicked],
  );

  // ---- 拖拽上传 ----

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLElement>) => {
      if (streaming || uploading) return;
      // 只接管文件拖拽，避免干扰输入框里拖选文本。
      if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setDragging(true);
    },
    [streaming, uploading],
  );

  const handleDragLeave = useCallback((e: DragEvent<HTMLElement>) => {
    // 在子元素之间移动也会触发 dragleave，只有真正离开容器才取消高亮。
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(false);
      if (streaming || uploading) return;
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length > 0) void handleFilesPicked(files);
    },
    [handleFilesPicked, streaming, uploading],
  );

  /** 会话文件按目录分组；顺序固定，未知目录排最后。 */
  const fileGroups = useMemo(() => {
    const ORDER = ["uploads", "extracted", "scripts", "tmp", "outputs"];
    const map = new Map<string, WorkspaceFile[]>();
    for (const f of workspace?.files ?? []) {
      const dir = f.dir && f.dir !== "." ? f.dir : "根目录";
      const list = map.get(dir);
      if (list) list.push(f);
      else map.set(dir, [f]);
    }
    return [...map.entries()].sort((a, b) => {
      const ia = ORDER.indexOf(a[0]);
      const ib = ORDER.indexOf(b[0]);
      return (ia < 0 ? ORDER.length : ia) - (ib < 0 ? ORDER.length : ib);
    });
  }, [workspace]);

  const handleSend = useCallback(async () => {
    const content = input.trim();
    // 已就绪（非 pending）的附件可以独立支撑一次发送：只传文件、不写文字是合法的。
    const ready = attachments.filter((a) => a.parse_status !== "pending");
    // 上传还没结束就发：此时 attachment_id 还没拿到，发出去会丢掉附件。
    if (streaming || uploading || (!content && ready.length === 0)) return;
    // 但真正发出去的是**当前全部**附件：用户已经把它放进这一轮了，
    // 不该因为某个还在解析就静默丢掉（pending 只影响"能否单独靠附件发送"）。
    const sending = attachments;

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
    setAttachments([]);
    setError("");
    setStreaming(true);

    const userMsg: ChatMessage = {
      role: "user",
      content,
      steps: [],
      // 本地先挂上附件：气泡立刻出现芯片，无需等服务端事件回传。
      attachments: sending.length > 0 ? sending : undefined,
    };
    const assistantMsg: ChatMessage = { role: "assistant", content: "", streaming: true, steps: [] };
    const assistantIndex = messages.length + 1;
    assistantIndexRef.current = assistantIndex;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;

    const acc = makeAccumulator();
    accRef.current = acc;
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
      // 服务端回传的解析结果更权威（本地挂的是上传时的快照），用它刷新用户气泡。
      if (type === "attachments_ready") {
        const list = evt.attachments as AttachmentInfo[] | undefined;
        if (list?.length && idx > 0) {
          updateMessage(idx - 1, (m) => (m.role === "user" ? { ...m, attachments: list } : m));
        }
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
      await streamMessage(
        conversationId,
        content,
        selectedAgent || undefined,
        onEvent,
        controller.signal,
        sending.map((a) => a.attachment_id),
      );
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
  }, [
    activeId,
    attachments,
    input,
    messages.length,
    refreshConversations,
    selectedAgent,
    streaming,
    updateMessage,
    uploading,
  ]);

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
      // 同步判重：即便按钮还没从界面上消失，也拦住连点造成的重复提交。
      if (submittingApprovalsRef.current.has(requestId)) return;
      submittingApprovalsRef.current.add(requestId);

      const label = reply
        ? "已提交补充信息"
        : decision === "approve"
          ? "已允许"
          : decision === "always"
            ? "已总是允许"
            : "已拒绝";
      const titleOf = (s: Step): string => {
        const target =
          s.approvalKind === "permission"
            ? `工具「${String(s.payload?.tool ?? "")}」`
            : s.approvalKind === "edit"
              ? `文件「${String(s.payload?.path ?? "").split("/").pop() ?? ""}」`
              : "补充信息";
        return `${label} · ${target}`;
      };

      // 关键：同步改累加器里的这个步骤。
      // onEvent 每次都用 acc.steps 覆盖消息的 steps，只做 setMessages 乐观更新会被
      // 随后的流式事件覆盖，导致卡片"复活"、按钮重新可点。
      const acc = accRef.current;
      if (acc) {
        const step = acc.steps.find(
          (s) => s.kind === "approval" && s.requestId === requestId && s.status === "running",
        );
        if (step) {
          step.status = "done";
          step.title = titleOf(step);
          step.text = label;
        }
        acc.resolvedApprovals.set(requestId, label);
      }

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
                  return { ...s, status: "done" as const, title: titleOf(s), text: label };
                }),
              },
        ),
      );
      try {
        await resolveApproval(activeId, requestId, decision, reply);
      } catch (e) {
        const msg = (e as Error).message;
        setError(msg);
        // 提交失败（例如审批已过期）：把卡片标成失败态，按钮不再出现，避免被反复点击。
        const failed = `提交失败：${msg}`;
        const accNow = accRef.current;
        if (accNow) {
          accNow.resolvedApprovals.set(requestId, failed);
          const step = accNow.steps.find(
            (s) => s.kind === "approval" && s.requestId === requestId,
          );
          if (step) {
            step.status = "error";
            step.title = failed;
            step.text = msg;
          }
        }
        setMessages((prev) =>
          prev.map((m) =>
            m.role !== "assistant"
              ? m
              : {
                  ...m,
                  steps: m.steps.map((s) =>
                    s.kind === "approval" && s.requestId === requestId
                      ? { ...s, status: "error" as const, title: failed, text: msg }
                      : s,
                  ),
                },
          ),
        );
      } finally {
        submittingApprovalsRef.current.delete(requestId);
      }
    },
    [activeId],
  );

  const toggleStep = useCallback((key: string) => {
    setStepOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  // 有文字，或至少有一个"已就绪"的附件，即可发送。
  const canSend =
    !streaming && !uploading && (input.trim().length > 0 || attachments.some((a) => a.parse_status !== "pending"));
  // 流式回答期间（以及上传中）禁止增删附件：附件必须属于下一轮，中途变更会挂错轮次。
  const attachmentsLocked = streaming || uploading;

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>问答产品</h1>
          <button className="btn primary" onClick={() => void handleNewConversation()}>
            + 新建对话
          </button>
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
          <label className="md-toggle" title="助手回答按 Markdown 渲染（表格 / 代码块 / 列表）；关闭后显示原文">
            <input
              type="checkbox"
              checked={markdownDefault}
              onChange={(e) => {
                setMarkdownDefault(e.target.checked);
                setRenderOverride({});
              }}
              disabled={streaming}
            />
            Markdown 渲染
          </label>
          {streaming && (
            <button className="btn warn" onClick={() => void handleInterrupt()}>
              中断
            </button>
          )}
          <button
            className={`btn drawer-toggle${drawerOpen ? " active" : ""}`}
            title="列出本会话临时目录里的文件（含 Agent 生成的脚本），可下载 / 预览"
            onClick={() => setDrawerOpen((v) => !v)}
          >
            📁 会话文件
          </button>
        </header>

        <div
          className={`messages${dragging ? " drag-active" : ""}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {messages.map((m, i) => {
            const isAssistant = m.role === "assistant";
            const useMarkdown = renderOverride[i] ?? markdownDefault;
            return (
              <div key={i} className={`message ${m.role}`}>
                <div className="message-head">
                  <span className="role">{isAssistant ? m.agentName || "助手" : "我"}</span>
                  {m.turnId && <span className="turn-id">turn #{shortId(m.turnId)}</span>}
                  {isAssistant && m.content && (
                    <span className="msg-actions">
                      <button
                        className="mini-btn"
                        title={useMarkdown ? "查看 Markdown 原文" : "按 Markdown 渲染"}
                        onClick={() => setRenderOverride((prev) => ({ ...prev, [i]: !useMarkdown }))}
                      >
                        {useMarkdown ? "源码" : "预览"}
                      </button>
                      <button
                        className="mini-btn"
                        title="复制 Markdown 原文"
                        onClick={() => void handleCopyMessage(i, m.content)}
                      >
                        {copiedIndex === i ? "已复制" : "复制"}
                      </button>
                    </span>
                  )}
                </div>

                {isAssistant && m.steps.length > 0 && (
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

                {isAssistant ? (
                  <AssistantBubble
                    content={m.content}
                    streaming={Boolean(m.streaming)}
                    markdown={useMarkdown}
                    error={m.error}
                  />
                ) : (
                  <>
                    {/* 内容为空（只发附件）时不渲染空气泡，避免多出一块空白。 */}
                    {m.content ? <div className={`bubble ${m.error ? "error" : ""}`}>{m.content}</div> : null}
                    {/* 气泡下方渲染本轮附件芯片：实时发送用本地状态，历史回放来自 attachments_ready 事件。 */}
                    <AttachmentChips items={m.attachments ?? []} />
                  </>
                )}
              </div>
            );
          })}
        </div>

        {error && (
          <div className="error-bar">
            {error}
            <button className="close" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}

        <div
          className={`composer-wrap${dragging ? " drag-active" : ""}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <AttachmentChips items={attachments} onRemove={removeAttachment} disabled={attachmentsLocked} />

          <div className="composer">
            {/* 隐藏的原生选择框：样式受限且各浏览器不一，统一用左边的按钮触发。 */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              accept={SUPPORTED_SUFFIXES.join(",")}
              onChange={handleFileInputChange}
            />
            <button
              className="btn attach-btn"
              title={`上传附件（最多 ${MAX_FILES} 个：Word / Excel / TXT 等），也可以直接把文件拖进页面`}
              disabled={attachmentsLocked}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? "解析中…" : "📎 附件"}
            </button>
            <textarea
              value={input}
              placeholder="输入问题，回车发送（Shift+回车换行）；可拖入文件作为附件"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              disabled={streaming}
            />
            <button className="btn primary send" onClick={() => void handleSend()} disabled={!canSend}>
              发送
            </button>
          </div>
        </div>
      </main>

      {drawerOpen && (
        <aside className="file-drawer">
          <div className="drawer-head">
            <strong>会话文件</strong>
            <button className="mini-btn" title="关闭面板" onClick={() => setDrawerOpen(false)}>
              关闭
            </button>
          </div>

          <div className="drawer-dir">
            <code title={workspace?.dir || ""}>{workspace?.dir || "（未加载）"}</code>
            <button className="mini-btn" disabled={!workspace?.dir} onClick={() => void handleCopyDir()}>
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
                  {dir}（{files.length}）
                </div>
                {files.map((f) => (
                  <div className="ws-file" key={f.path}>
                    <button
                      className="ws-file-name"
                      title={isPreviewableText(f.path) ? `${f.path}（点击预览）` : f.path}
                      disabled={!isPreviewableText(f.path)}
                      onClick={() => void openPreview(f)}
                    >
                      {f.path.split("/").pop()}
                    </button>
                    <button className="mini-btn" title="下载该文件" onClick={() => void downloadFile(f)}>
                      下载
                    </button>
                    <div className="ws-file-meta">{workspaceLabel(f)}</div>
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

      {preview && (
        <div className="preview-mask" onClick={() => setPreview(null)}>
          {/* 点面板内部不该关掉弹窗，所以拦住冒泡 */}
          <div className="preview-panel" onClick={(e) => e.stopPropagation()}>
            <div className="preview-head">
              <strong title={preview.name}>{preview.name}</strong>
              <button className="mini-btn" onClick={() => setPreview(null)}>
                关闭
              </button>
            </div>
            <pre>{preview.text}</pre>
            {preview.truncated && <div className="muted">仅预览前 {PREVIEW_CHARS} 个字符</div>}
          </div>
        </div>
      )}
    </div>
  );
}
