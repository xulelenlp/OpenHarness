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
import { ResultFilesCard, TurnFilesCard } from "./FileCards";
import { deriveTurnFilesFromSteps, excludeResultFiles, keepResultFiles } from "./turnFiles";
import { copyText } from "./clipboard";
import { EmptyState } from "./EmptyState";
import { QuestionCard } from "./QuestionCard";
import {
  approvalResultLabel,
  approvalStepTitle,
  isPendingQuestion,
  questionText,
} from "./questions";
import {
  AlertIcon,
  ArrowUpIcon,
  BrandMark,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CloseIcon,
  CopyIcon,
  DownloadIcon,
  EyeIcon,
  FileIcon,
  FolderIcon,
  InfoIcon,
  MenuIcon,
  PaperclipIcon,
  PlusIcon,
  SearchIcon,
  SparkIcon,
  StopIcon,
  ToolIcon,
} from "./icons";
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

/** 这些工具完成时可能往会话目录写文件：抽屉打开时触发一次静默刷新。 */
const FILE_WRITING_TOOLS = new Set([
  "write_file",
  "edit_file",
  "notebook_edit",
  "run_script",
  "write_report_docx",
  "bash",
]);

function shortId(id: string): string {
  return id.slice(0, 8);
}

/** 侧边栏的轻量时间：今天显示 HH:mm，其余显示 MM-DD。 */
function conversationTime(epochSeconds?: number): string {
  if (!epochSeconds) return "";
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function truncateText(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit)}…` : clean;
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

  if (!text) {
    return (
      <div className={cls}>
        {streaming ? (
          <span className="thinking" role="status" aria-label="正在思考">
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-label">正在思考</span>
          </span>
        ) : (
          ""
        )}
      </div>
    );
  }
  if (!asMarkdown) return <div className={cls}>{text}</div>;
  return (
    <div className={cls}>
      <MarkdownView content={text} />
    </div>
  );
}

/** 附件类型图标：统一文件轮廓，用颜色区分 Word / Excel / 文本。 */
function AttachmentIcon({ kind }: { kind: string }) {
  return <FileIcon size={14} className={`att-kind ${kind}`} />;
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
  if (step.approvalKind === "question") return questionText(step);
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

function DiffBlock({ diff, truncated }: { diff: string; truncated?: boolean }) {
  const lines = diff.split(/\r?\n/);
  return (
    <div className="diff-wrap">
      <pre className="diff-block" aria-label="文件修改内容">
        {lines.map((line, index) => {
          const kind = line.startsWith("@@")
            ? "hunk"
            : line.startsWith("+") && !line.startsWith("+++")
              ? "add"
              : line.startsWith("-") && !line.startsWith("---")
                ? "del"
                : "ctx";
          return (
            <span key={index} className={`diff-line ${kind}`}>
              {line || " "}
              {"\n"}
            </span>
          );
        })}
      </pre>
      {truncated ? (
        <div className="diff-note muted">内容较长，仅显示前 12000 个字符</div>
      ) : null}
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
  const icon =
    step.kind === "tool" ? (
      <ToolIcon size={15} />
    ) : step.kind === "approval" ? (
      <AlertIcon size={15} />
    ) : step.kind === "error" ? (
      <AlertIcon size={15} />
    ) : step.kind === "reasoning" ? (
      <SparkIcon size={15} />
    ) : (
      <InfoIcon size={15} />
    );
  const badge =
    step.kind === "tool" && step.toolName === "ask_user_question" && step.status === "running"
      ? "等待回答"
      : step.kind === "approval" && step.status === "running"
        ? "等待确认"
        : step.kind === "reasoning" && step.status === "running"
          ? "思考中"
          : step.status === "running"
            ? "运行中"
            : step.status === "error"
              ? "失败"
              : "完成";
  const isQuestion = step.approvalKind === "question";
  const diff = typeof step.payload?.diff === "string" ? step.payload.diff : "";
  const visibleDiff = diff.length > 12000 ? diff.slice(0, 12000) : diff;

  return (
    <div className={`step-card ${step.kind} ${step.status}${isQuestion ? " question" : ""}`}>
      <button
        type="button"
        className="step-head"
        onClick={onToggle}
        aria-expanded={!collapsed}
      >
        <span className="step-icon">{icon}</span>
        <span className="step-title">{step.title}</span>
        <span className={`step-badge ${step.status}`}>{badge}</span>
        <span className="step-caret">
          {collapsed ? <ChevronRightIcon size={14} /> : <ChevronDownIcon size={14} />}
        </span>
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

          {step.kind === "approval" &&
            (isQuestion ? (
              <QuestionCard
                step={step}
                onAnswer={(requestId, answer) =>
                  onApproval?.(requestId, "approve", answer)
                }
              />
            ) : (
              <div className="approval-step">
                <div className="approval-desc">{approvalDesc(step)}</div>
                {step.payload?.reason ? (
                  <div className="muted">原因：{String(step.payload.reason)}</div>
                ) : null}

                {step.approvalKind === "permission" && step.payload?.input !== undefined ? (
                  <DetailBlock label="工具参数" data={step.payload.input} />
                ) : null}
                {step.approvalKind === "edit" && visibleDiff ? (
                  <DiffBlock diff={visibleDiff} truncated={diff.length > 12000} />
                ) : null}

                {step.status === "running" && step.requestId && onApproval ? (
                  <>
                    <div className="approval-actions">
                      <button
                        type="button"
                        className="btn primary"
                        onClick={() => onApproval(step.requestId!, "approve")}
                      >
                        <CheckIcon size={14} /> 允许
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => onApproval(step.requestId!, "always")}
                      >
                        总是允许
                      </button>
                      <button
                        type="button"
                        className="btn danger"
                        onClick={() => onApproval(step.requestId!, "deny")}
                      >
                        <CloseIcon size={14} /> 拒绝
                      </button>
                    </div>
                    <div className="muted approval-hint">
                      本轮内同一工具只确认一次；点「总是允许」则本会话后续都不再询问。
                    </div>
                  </>
                ) : step.text ? (
                  <div className="step-text">{step.text}</div>
                ) : null}
              </div>
            ))}

          {(step.kind === "status" || step.kind === "error" || step.kind === "reasoning") &&
          step.text ? (
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
  /** 正在下载的文件（相对路径）：按钮显示「下载中…」并禁用，避免重复点击。 */
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null);

  // ---- 布局与加载状态 ----
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [conversationFilter, setConversationFilter] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  /** 用户是否停在底部；只有停在底部时才随流式输出自动跟随。 */
  const stickToBottomRef = useRef(true);
  /** 上一个待确认请求 id：新请求出现时强制滚到可见位置，即使之前向上翻了。 */
  const pendingApprovalRef = useRef<string | null>(null);
  const assistantIndexRef = useRef<number>(-1);
  /** 最新 activeId：任务结束时的异步刷新用它判断用户是否已经切走。 */
  const activeIdRef = useRef<string | null>(null);
  /** 抽屉是否打开：SSE 回调里读最新值，避免发送消息时的旧闭包。 */
  const drawerOpenRef = useRef(false);
  /** 任务进行中「静默刷新」文件列表的防抖定时器。 */
  const workspaceRefreshTimerRef = useRef<number | null>(null);
  /** 本轮流式事件的累加器；供审批处理同步修改，避免被后续事件覆盖。 */
  const accRef = useRef<TurnAccumulator | null>(null);
  /** 正在提交的审批 id（同步判重），防止连点导致重复提交。 */
  const submittingApprovalsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    localStorage.setItem("qaapp_render_markdown", markdownDefault ? "1" : "0");
  }, [markdownDefault]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    drawerOpenRef.current = drawerOpen;
  }, [drawerOpen]);

  // 输入框随内容自动增高（最多 6 行左右），发送后由 handleSend 复位。
  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 168)}px`;
  }, [input]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const element = messagesRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior });
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const element = messagesRef.current;
    if (!element) return;
    stickToBottomRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 96;
  }, []);

  const activeSummary = useMemo(
    () => conversations.find((conversation) => conversation.conversation_id === activeId),
    [activeId, conversations],
  );

  const pendingQuestion = useMemo(() => {
    for (const message of messages) {
      for (const step of message.steps) {
        if (isPendingQuestion(step)) return step;
      }
    }
    return null;
  }, [messages]);

  const pendingApproval = useMemo(() => {
    for (const message of messages) {
      for (const step of message.steps) {
        if (step.kind === "approval" && step.status === "running") return step;
      }
    }
    return null;
  }, [messages]);

  const sessionTitle = useMemo(() => {
    const summary = activeSummary?.summary?.trim();
    if (summary) return truncateText(summary, 48);
    const firstUser = messages.find((message) => message.role === "user" && message.content.trim());
    return firstUser ? truncateText(firstUser.content, 48) : "新对话";
  }, [activeSummary?.summary, messages]);

  const filteredConversations = useMemo(() => {
    const query = conversationFilter.trim().toLowerCase();
    if (!query) return conversations;
    return conversations.filter((conversation) => {
      const text = `${conversation.summary ?? conversation.preview ?? ""} ${conversation.conversation_id}`;
      return text.toLowerCase().includes(query);
    });
  }, [conversationFilter, conversations]);

  // 流式输出 / 新消息 / 待确认操作出现时，只要用户还在底部就跟随。
  useEffect(() => {
    if (stickToBottomRef.current) scrollToBottom("auto");
  }, [messages, pendingApproval, scrollToBottom, streaming]);

  // 新的待确认请求出现时，强制滚到底部，避免用户向上翻后错过必答卡片。
  useEffect(() => {
    const requestId = pendingApproval?.requestId ?? null;
    if (requestId && requestId !== pendingApprovalRef.current) {
      stickToBottomRef.current = true;
      window.requestAnimationFrame(() => scrollToBottom("auto"));
    }
    pendingApprovalRef.current = requestId;
  }, [pendingApproval?.requestId, scrollToBottom]);

  // 预览弹窗支持 Esc 关闭。
  useEffect(() => {
    if (!preview) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreview(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [preview]);

  useEffect(
    () => () => {
      if (workspaceRefreshTimerRef.current !== null) {
        window.clearTimeout(workspaceRefreshTimerRef.current);
      }
    },
    [],
  );

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
    activeIdRef.current = id;
    setActiveId(id);
    setSidebarOpen(false);
    setMessages([]);
    setStepOpen({});
    setLoadingConversation(true);
    stickToBottomRef.current = true;
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
        // 只有本轮真正结束（有 turn_complete）才恢复文件卡片：任务进行中被刷新时，
        // 部分工具轨迹不能当成「已完成的文件列表」展示。
        // 新轨迹用 turn_files 事件；旧轨迹没有事件时从工具输出推导。
        const finished = Boolean(acc.finished);
        const hasEventFiles = (acc.changedFiles?.length ?? 0) > 0 || (acc.resultFiles?.length ?? 0) > 0;
        const derived = finished && !hasEventFiles ? deriveTurnFilesFromSteps(acc.steps) : null;
        rebuilt.push({
          role: "assistant",
          content: acc.content,
          agentName: acc.agentName,
          turnId: turn.turn_id,
          steps: acc.steps,
          changedFiles: finished ? acc.changedFiles ?? derived?.changed : undefined,
          resultFiles: finished
            ? keepResultFiles(acc.resultFiles ?? derived?.results ?? [])
            : undefined,
          filesDerived: finished && !hasEventFiles && Boolean(derived?.changed.length),
          filesTruncated: finished ? acc.filesTruncated : undefined,
        });
      });
      setMessages(rebuilt);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingConversation(false);
    }
  }, []);

  const handleNewConversation = useCallback(async () => {
    try {
      const { conversation_id } = await createConversation();
      await refreshConversations();
      activeIdRef.current = conversation_id;
      setActiveId(conversation_id);
      setSidebarOpen(false);
      setDrawerOpen(false);
      setLoadingConversation(false);
      setMessages([]);
      setInput("");
      setAttachments([]);
      setWorkspace(null);
      stickToBottomRef.current = true;
    } catch (e) {
      setError((e as Error).message);
    }
  }, [refreshConversations]);

  // ---- 会话文件抽屉 ----

  /**
   * 拉取会话文件列表。
   *
   * `conversationId` 可选：任务结束时用发送时的会话 ID 刷新，即使抽屉没打开也
   * 更新缓存；用户已经切走时只丢弃结果，不污染当前会话的文件列表。
   */
  const refreshWorkspace = useCallback(
    async (conversationId?: string, silent = false) => {
      const id = conversationId ?? activeId;
      if (!id) return;
      if (!silent && activeIdRef.current === id) setWorkspaceLoading(true);
      try {
        const data = await listConversationFiles(id);
        if (activeIdRef.current === id) setWorkspace(data);
      } catch (e) {
        if (activeIdRef.current === id) setError((e as Error).message);
      } finally {
        if (!silent && activeIdRef.current === id) setWorkspaceLoading(false);
      }
    },
    [activeId],
  );

  /**
   * 任务进行中静默刷新抽屉（仅当抽屉打开）。
   *
   * 写文件 / 生成脚本的工具完成时触发，600ms 防抖，避免一次任务里几十个工具
   * 各发一次请求；不显示「刷新中…」，列表原地更新。
   */
  const scheduleWorkspaceRefresh = useCallback(
    (conversationId: string) => {
      if (!drawerOpenRef.current) return;
      if (workspaceRefreshTimerRef.current !== null) {
        window.clearTimeout(workspaceRefreshTimerRef.current);
      }
      workspaceRefreshTimerRef.current = window.setTimeout(() => {
        workspaceRefreshTimerRef.current = null;
        void refreshWorkspace(conversationId, true);
      }, 600);
    },
    [refreshWorkspace],
  );

  // 抽屉打开（或切换会话）时拉取一次；关闭时不动，省掉无谓请求。
  useEffect(() => {
    if (drawerOpen && activeId) void refreshWorkspace();
  }, [drawerOpen, activeId, refreshWorkspace]);

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
   *
   * 参数是「会话相对路径」而不是 WorkspaceFile：结果文件卡片、过程文件卡片
   * 与右侧抽屉共用同一个下载入口。
   */
  const downloadPath = useCallback(
    async (relativePath: string) => {
      if (!activeId || downloadingPath) return;
      setDownloadingPath(relativePath);
      try {
        const { blob, filename } = await fetchConversationFile(activeId, relativePath);
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
      } finally {
        setDownloadingPath(null);
      }
    },
    [activeId, downloadingPath],
  );

  /** 文本类文件点击预览：读 Blob 文本，只留前 2000 字符。 */
  const previewPath = useCallback(
    async (relativePath: string) => {
      if (!activeId) return;
      try {
        const { blob } = await fetchConversationFile(activeId, relativePath);
        const text = await blob.text();
        setPreview({
          name: relativePath,
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
          activeIdRef.current = conversationId;
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
        activeIdRef.current = conversationId;
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
    setSidebarOpen(false);
    stickToBottomRef.current = true;
    if (textareaRef.current) textareaRef.current.style.height = "auto";

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
    /** true = 本轮已结束并展示过文件卡片，避免 finally 里重复计算。 */
    let turnFilesShown = false;
    /**
     * 把本轮文件写入消息。
     *
     * 只在 `turn_complete`（任务真正结束）时调用：进行中展示文件卡片会随着
     * 工具调用不断增减，视觉上很凌乱。事件优先，旧轨迹退回工具输出推导。
     */
    const applyTurnFiles = (m: ChatMessage): ChatMessage => {
      const hasEventFiles =
        (acc.changedFiles?.length ?? 0) > 0 || (acc.resultFiles?.length ?? 0) > 0;
      const derived = hasEventFiles ? null : deriveTurnFilesFromSteps(acc.steps);
      return {
        ...m,
        changedFiles: acc.changedFiles ?? derived?.changed ?? m.changedFiles,
        // 只有成果文档才留在结果区；旧后端下发的 csv / 图片等会被还原到过程区。
        resultFiles: keepResultFiles(acc.resultFiles ?? derived?.results ?? []),
        filesDerived: hasEventFiles ? false : Boolean(derived?.changed.length),
        filesTruncated: acc.filesTruncated ?? m.filesTruncated,
      };
    };
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
      // 抽屉打开时，写文件类工具一完成就静默刷新（600ms 防抖），
      // 不必等整轮结束才看到新生成的脚本 / 产物。
      if (
        type === "tool_completed" &&
        FILE_WRITING_TOOLS.has(String(evt.tool_name ?? ""))
      ) {
        scheduleWorkspaceRefresh(conversationId);
      }
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
      updateMessage(idx, (m) => {
        const next: ChatMessage = {
          ...m,
          content: acc.content,
          agentName: acc.agentName ?? m.agentName,
          steps: JSON.parse(JSON.stringify(acc.steps)) as Step[],
          error: m.error || type === "error",
        };
        // 过程/结果文件只在任务全部完成后一次性出现；turn_files 事件先到，
        // 这里等 turn_complete 再落到消息上。
        return type === "turn_complete" ? applyTurnFiles(next) : next;
      });
      if (type === "turn_complete") {
        turnFilesShown = true;
        // 任务结束：取消待执行的静默刷新，立即刷新一次「会话文件」列表。
        // 抽屉没打开也更新缓存，用户随后打开即可看到新生成的脚本 / 产物。
        if (workspaceRefreshTimerRef.current !== null) {
          window.clearTimeout(workspaceRefreshTimerRef.current);
          workspaceRefreshTimerRef.current = null;
        }
        void refreshWorkspace(conversationId);
      }
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
      // 网络断流 / 中断时可能没收到 turn_complete：流已经结束，补一次文件卡片，
      // 不要让它永远不出现。
      if (!turnFilesShown) {
        const idx = assistantIndexRef.current;
        if (idx >= 0) updateMessage(idx, applyTurnFiles);
        // 断流 / 中断没收到 turn_complete：流已结束，同样刷新文件列表。
        if (workspaceRefreshTimerRef.current !== null) {
          window.clearTimeout(workspaceRefreshTimerRef.current);
          workspaceRefreshTimerRef.current = null;
        }
        void refreshWorkspace(conversationId);
      }
      abortRef.current = null;
      void refreshConversations();
    }
  }, [
    activeId,
    attachments,
    input,
    messages.length,
    refreshConversations,
    refreshWorkspace,
    scheduleWorkspaceRefresh,
    selectedAgent,
    streaming,
    updateMessage,
    uploading,
  ]);

  const handleInterrupt = useCallback(async () => {
    if (!activeId) return;
    // 如果正卡在审批/问题，先按拒绝收尾，避免中断后卡片仍停在「等待确认」。
    const requestId = pendingApprovalRef.current;
    if (requestId) {
      try {
        await resolveApproval(activeId, requestId, "deny");
      } catch {
        // 审批可能已经结束，忽略。
      }
    }
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
      // 用户刚做出确认，后续输出继续跟随到底部。
      stickToBottomRef.current = true;

      const acc = accRef.current;
      const targetStep = acc?.steps.find(
        (s) => s.kind === "approval" && s.requestId === requestId,
      );
      const kind = targetStep?.approvalKind;
      const label = approvalResultLabel(kind, decision, reply);
      const answer = kind === "question" ? reply?.trim() || undefined : undefined;

      // 关键：同步改累加器里的这个步骤。
      // onEvent 每次都用 acc.steps 覆盖消息的 steps，只做 setMessages 乐观更新会被
      // 随后的流式事件覆盖，导致卡片"复活"、按钮重新可点。
      if (acc) {
        const step = acc.steps.find(
          (s) => s.kind === "approval" && s.requestId === requestId,
        );
        if (step) {
          step.status = "done";
          step.title = approvalStepTitle(step.approvalKind, step.payload, label);
          step.text = label;
          if (step.approvalKind === "question") step.answer = answer;
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
                  const stepLabel = approvalResultLabel(s.approvalKind, decision, reply);
                  return {
                    ...s,
                    status: "done" as const,
                    title: approvalStepTitle(s.approvalKind, s.payload, stepLabel),
                    text: stepLabel,
                    answer:
                      s.approvalKind === "question"
                        ? (reply?.trim() || undefined)
                        : s.answer,
                  };
                }),
              },
        ),
      );
      try {
        await resolveApproval(activeId, requestId, decision, reply);
      } catch (e) {
        const msg = (e as Error).message;
        setError(msg);
        const accNow = accRef.current;
        if (kind === "question") {
          // 问题提交失败时保留可操作态，让用户能重新选择/输入，而不是把卡永久标红。
          accNow?.resolvedApprovals.delete(requestId);
          const step = accNow?.steps.find(
            (s) => s.kind === "approval" && s.requestId === requestId,
          );
          if (step) {
            step.status = "running";
            step.title = approvalStepTitle(step.approvalKind, step.payload);
            step.text = undefined;
            step.answer = undefined;
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.role !== "assistant"
                ? m
                : {
                    ...m,
                    steps: m.steps.map((s) =>
                      s.kind === "approval" && s.requestId === requestId
                        ? {
                            ...s,
                            status: "running" as const,
                            title: approvalStepTitle(s.approvalKind, s.payload),
                            text: undefined,
                            answer: undefined,
                          }
                        : s,
                    ),
                  },
            ),
          );
        } else {
          // 权限/编辑审批：标成失败态，按钮不再出现，避免被反复点击。
          const failed = `提交失败：${msg}`;
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
        }
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
    <div className={`layout${sidebarOpen ? " sidebar-open" : ""}`}>
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
            <strong>智能问答</strong>
            <span>企业级 AI 助手</span>
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
          onClick={() => void handleNewConversation()}
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
                onClick={() => void openConversation(c.conversation_id)}
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
          ABCHarness
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn mobile-menu"
            title="打开会话列表"
            aria-label="打开会话列表"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(true)}
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
              onClick={() => setDrawerOpen((v) => !v)}
            >
              <FolderIcon size={17} />
              <span className="icon-btn-label">文件</span>
            </button>
          </div>
        </header>

        <div
          ref={messagesRef}
          className={`messages${dragging ? " drag-active" : ""}`}
          onScroll={handleMessagesScroll}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {loadingConversation ? (
            <div className="message-skeleton" aria-label="正在加载对话">
              <span />
              <span />
              <span />
            </div>
          ) : null}

          {!loadingConversation && messages.length === 0 ? (
            <EmptyState
              onPick={(prompt) => {
                setInput(prompt);
                window.requestAnimationFrame(() => textareaRef.current?.focus());
              }}
            />
          ) : null}

          {messages.map((m, i) => {
            const isAssistant = m.role === "assistant";
            const useMarkdown = renderOverride[i] ?? markdownDefault;
            // 结果文件只在结果区出现；过程区展示其余文件（脚本/数据/图片/md 等）。
            const processFiles = excludeResultFiles(m.changedFiles ?? [], m.resultFiles ?? []);
            const hasPendingApproval = m.steps.some(
              (step) => step.kind === "approval" && step.status === "running",
            );
            return (
              <div key={i} className={`message ${m.role}`}>
                <div className="message-head">
                  <span className={`avatar ${isAssistant ? "assistant" : "user"}`}>
                    {isAssistant ? <BrandMark size={24} /> : "我"}
                  </span>
                  {isAssistant ? <span className="role">助手</span> : null}
                  {isAssistant && m.agentName ? (
                    <span className="agent-badge">
                      {agents.find((agent) => agent.id === m.agentName)?.name || m.agentName}
                    </span>
                  ) : null}
                  {m.turnId && <span className="turn-id">turn #{shortId(m.turnId)}</span>}
                  {isAssistant && m.content && (
                    <span className="msg-actions">
                      <button
                        type="button"
                        className="mini-btn"
                        title={useMarkdown ? "查看 Markdown 原文" : "按 Markdown 渲染"}
                        onClick={() =>
                          setRenderOverride((prev) => ({ ...prev, [i]: !useMarkdown }))
                        }
                      >
                        {useMarkdown ? "源码" : "预览"}
                      </button>
                      <button
                        type="button"
                        className="mini-btn"
                        title="复制 Markdown 原文"
                        onClick={() => void handleCopyMessage(i, m.content)}
                      >
                        <CopyIcon size={12} />
                        {copiedIndex === i ? "已复制" : "复制"}
                      </button>
                    </span>
                  )}
                </div>

                {isAssistant && m.steps.length > 0 && (
                  <div className="activity">
                    <div className="activity-head">
                      <span className="activity-title">执行过程</span>
                      <span className="activity-count">{m.steps.length} 步</span>
                    </div>
                    <div className="steps">
                      {m.steps.map((s) => {
                        const openKey = `${i}:${s.key}`;
                        // 待处理的审批必须展开（需用户操作）；正在运行的工具步骤展开；
                        // 思考与历史步骤默认折叠，保持对话干净。
                        const needsAction = s.kind === "approval" && s.status === "running";
                        const collapsed = needsAction
                          ? false
                          : hasPendingApproval && s.kind === "tool" && s.status === "running"
                            ? !stepOpen[openKey]
                            : s.kind === "tool" && s.toolName === "ask_user_question"
                              ? !stepOpen[openKey]
                              : s.kind === "reasoning"
                                ? !stepOpen[openKey]
                                : s.status === "running"
                                  ? false
                                  : !stepOpen[openKey];
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
                  </div>
                )}

                {isAssistant ? (
                  <>
                    <AssistantBubble
                      content={m.content}
                      streaming={Boolean(m.streaming) && !hasPendingApproval}
                      markdown={useMarkdown}
                      error={m.error}
                    />
                    {/* 正文之后依次是过程文件（可折叠）与结果文件（可直接下载）：
                        结论优先，过程只占用折叠卡片，结果文件紧邻正文便于点击。 */}
                    {processFiles.length > 0 && (
                      <TurnFilesCard
                        files={processFiles}
                        truncated={m.filesTruncated}
                        onPreview={previewPath}
                        onDownload={downloadPath}
                        downloading={downloadingPath}
                      />
                    )}
                    {(m.resultFiles?.length ?? 0) > 0 && (
                      <ResultFilesCard
                        files={m.resultFiles ?? []}
                        onPreview={previewPath}
                        onDownload={downloadPath}
                        downloading={downloadingPath}
                      />
                    )}
                    {m.filesDerived &&
                      (processFiles.length > 0 || (m.resultFiles?.length ?? 0) > 0) && (
                        <div className="files-derived muted">根据本轮工具轨迹整理，仅供参考</div>
                      )}
                  </>
                ) : (
                  <>
                    {/* 内容为空（只发附件）时不渲染空气泡，避免多出一块空白。 */}
                    {m.content ? (
                      <div className={`bubble ${m.error ? "error" : ""}`}>{m.content}</div>
                    ) : null}
                    {/* 气泡下方渲染本轮附件芯片：实时发送用本地状态，历史回放来自 attachments_ready 事件。 */}
                    <AttachmentChips items={m.attachments ?? []} />
                  </>
                )}
              </div>
            );
          })}

          {dragging ? (
            <div className="drop-overlay" aria-hidden="true">
              <PaperclipIcon size={22} />
              <strong>松开即可上传</strong>
              <span>Word / Excel / 文本，最多 {MAX_FILES} 个</span>
            </div>
          ) : null}
        </div>

        {error && (
          <div className="toast" role="alert">
            <AlertIcon size={17} />
            <span className="toast-text">{error}</span>
            <button
              type="button"
              className="toast-close"
              title="关闭提示"
              aria-label="关闭提示"
              onClick={() => setError("")}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        )}

        <div
          className={`composer-dock${dragging ? " drag-active" : ""}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <AttachmentChips
            items={attachments}
            onRemove={removeAttachment}
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
              onChange={handleFileInputChange}
            />
            <textarea
              ref={textareaRef}
              value={input}
              rows={1}
              placeholder={
                streaming
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
                  void handleSend();
                }
              }}
              disabled={streaming}
            />
            <div className="composer-toolbar">
              <div className="composer-left">
                <button
                  type="button"
                  className="icon-btn attach-btn"
                  title={`上传附件（最多 ${MAX_FILES} 个：Word / Excel / TXT 等），也可以直接把文件拖进页面`}
                  aria-label="上传附件"
                  disabled={attachmentsLocked}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <PaperclipIcon size={16} />
                  <span>{uploading ? "解析中…" : "附件"}</span>
                </button>
                <span className="composer-hint">
                  {pendingQuestion
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
                    onClick={() => void handleInterrupt()}
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
                    onClick={() => void handleSend()}
                  >
                    <ArrowUpIcon size={18} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </main>

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
              onClick={() => void handleCopyDir()}
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
                      onClick={() => void previewPath(f.path)}
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
                          onClick={() => void previewPath(f.path)}
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
                        onClick={() => void downloadPath(f.path)}
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

      {preview && (
        <div className="preview-mask" onClick={() => setPreview(null)}>
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
                onClick={() => setPreview(null)}
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
      )}
    </div>
  );
}
