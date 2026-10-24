import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createConversation,
  getConversation,
  interruptConversation,
  listAgents,
  listConversations,
  logout as logoutRequest,
  resolveApproval,
} from "./api";
import { mergePendingApprovals, runningTurnOf } from "./approvals";
import type { TurnAccumulator } from "./turn";
import { copyText } from "./clipboard";
import { Composer } from "./components/Composer";
import { LoginPage } from "./components/LoginPage";
import { MessageList } from "./components/MessageList";
import { PasswordDialog } from "./components/PasswordDialog";
import { PreviewModal } from "./components/PreviewModal";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { WorkspaceDrawer } from "./components/WorkspaceDrawer";
import { buildHistory, type ConversationDetailShape } from "./chat/history";
import { conversationTitle, filterConversations } from "./chat/titles";
import { sendTurn } from "./chat/send";
import { useAuthSession } from "./hooks/useAuthSession";
import { useApprovalCenter } from "./hooks/useApprovalCenter";
import { useAttachments } from "./hooks/useAttachments";
import { useTurnResume } from "./hooks/useTurnResume";
import { useWorkspaceFiles } from "./hooks/useWorkspaceFiles";
import { isPendingQuestion } from "./questions";
import { shortId, truncateText } from "./chat/format";
import {
  AlertIcon,
  CloseIcon,
  InfoIcon,
} from "./icons";
import type {
  AgentInfo,
  AttachmentInfo,
  AuthUser,
  ChatMessage,
  ConversationSummary,
} from "./types";

interface AgentListResponseShape {
  agents: AgentInfo[];
  default_agent?: string;
}

interface ConversationListShape {
  conversations: ConversationSummary[];
}

export default function App() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string>("");
  /** 当前页面自己发起、仍在消费 SSE 的会话（此时不需要断线跟进）。 */
  const [liveConversationId, setLiveConversationId] = useState<string | null>(null);
  /** 其他会话出现待审批时的轻提示。 */
  const [approvalNotice, setApprovalNotice] = useState<{
    conversationId: string;
    text: string;
  } | null>(null);
  const [stepOpen, setStepOpen] = useState<Record<string, boolean>>({});
  /** 助手回答是否按 Markdown 渲染（全局默认，存本地）。 */
  const [markdownDefault, setMarkdownDefault] = useState<boolean>(
    () => localStorage.getItem("qaapp_render_markdown") !== "0",
  );
  /** 单条消息的渲染模式覆盖：true = 强制 Markdown，false = 强制源码。 */
  const [renderOverride, setRenderOverride] = useState<Record<number, boolean>>({});
  /** 刚复制过的消息下标（用于按钮上的短暂反馈）。 */
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  // ---- 登录状态（TEMP-AUTH）----
  const [authNotice, setAuthNotice] = useState("");
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);

  // ---- 布局与加载状态 ----
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [conversationFilter, setConversationFilter] = useState("");
  const [loadingConversation, setLoadingConversation] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  /** 用户是否停在底部；只有停在底部时才随流式输出自动跟随。 */
  const stickToBottomRef = useRef(true);
  /** 上一个待确认请求 id：新请求出现时强制滚到可见位置。 */
  const pendingApprovalRef = useRef<string | null>(null);
  /** 消息列表镜像：审批中心判断请求归属时用，避免把 messages 塞进回调依赖。 */
  const chatMessagesRef = useRef<ChatMessage[]>([]);
  /** 会话加载序号：快速切换时丢弃过期响应，避免把上一个会话渲染到当前会话下。 */
  const openSeqRef = useRef(0);
  /** 流式代次：切换会话 / 重新发送后，旧的 finally 不再改 streaming 状态。 */
  const streamTokenRef = useRef(0);
  /** 最新 activeId：任务结束时的异步刷新用它判断用户是否已经切走。 */
  const activeIdRef = useRef<string | null>(null);
  /** 本轮流式事件的累加器；供审批处理同步修改。 */
  const accRef = useRef<TurnAccumulator | null>(null);

  useEffect(() => {
    localStorage.setItem("qaapp_render_markdown", markdownDefault ? "1" : "0");
  }, [markdownDefault]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

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

  const sessionTitle = useMemo(
    () => conversationTitle(activeSummary?.summary, messages),
    [activeSummary?.summary, messages],
  );

  const filteredConversations = useMemo(
    () => filterConversations(conversations, conversationFilter),
    [conversationFilter, conversations],
  );

  // 流式输出 / 新消息 / 待确认操作出现时，只要用户还在底部就跟随。
  useEffect(() => {
    if (stickToBottomRef.current) scrollToBottom("auto");
  }, [messages, pendingApproval, scrollToBottom, streaming]);

  // 新的待确认请求出现时，强制滚到底部。
  useEffect(() => {
    const requestId = pendingApproval?.requestId ?? null;
    if (requestId && requestId !== pendingApprovalRef.current) {
      stickToBottomRef.current = true;
      window.requestAnimationFrame(() => scrollToBottom("auto"));
    }
    pendingApprovalRef.current = requestId;
  }, [pendingApproval?.requestId, scrollToBottom]);

  const handleCopyMessage = useCallback(async (index: number, text: string) => {
    if (await copyText(text)) {
      setCopiedIndex(index);
      window.setTimeout(
        () => setCopiedIndex((prev) => (prev === index ? null : prev)),
        1500,
      );
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

  const {
    auth,
    setAuth,
    loginRequired,
    defaultCredentials,
    setDefaultCredentials,
  } = useAuthSession({ setError });

  // 登录后才加载会话列表与智能体；退出登录时清空，避免看到上一个账号的数据。
  useEffect(() => {
    if (auth.status !== "authenticated") return;
    void Promise.all([refreshAgents(), refreshConversations()]);
  }, [auth.status, refreshAgents, refreshConversations]);

  // 会话过期 / 被服务端吊销时回到登录页（旧部署没有登录页，保持原行为）。
  useEffect(() => {
    if (!loginRequired) return;
    const onUnauthorized = () => {
      // 会话过期 / 被吊销：清掉上一个账号的对话内容，避免下次登录看到别人的数据。
      setMessages([]);
      setConversations([]);
      setActiveId(null);
      activeIdRef.current = null;
      setAuth({ status: "anonymous" });
    };
    window.addEventListener("qaapp:unauthorized", onUnauthorized);
    return () => window.removeEventListener("qaapp:unauthorized", onUnauthorized);
  }, [loginRequired]);

  const workspaceFiles = useWorkspaceFiles({
    activeId,
    activeIdRef,
    streaming,
    onError: (message) => setError(message),
  });
  const {
    drawerOpen,
    setDrawerOpen,
    workspace,
    setWorkspace,
    workspaceLoading,
    preview,
    setPreview,
    copiedDir,
    downloadingPath,
    fileGroups,
    flushWorkspaceRefresh,
    handleCopyDir,
    downloadPath,
    previewPath,
  } = workspaceFiles;

  // 消息镜像：审批中心在提交时用它确认 request 属于哪个会话。
  useEffect(() => {
    chatMessagesRef.current = messages;
  }, [messages]);

  const approvalCenter = useApprovalCenter({
    enabled: auth.status === "authenticated",
    activeId,
    messagesRef: chatMessagesRef,
    accRef,
    setMessages,
    setError,
    live: liveConversationId === activeId,
  });

  // 断线跟进收尾：刷新文件列表与会话列表（审批/工具事件已经由游标写进消息）。
  const handleResumeFinished = useCallback(
    (conversationId: string) => {
      flushWorkspaceRefresh(conversationId);
      void refreshConversations();
    },
    [flushWorkspaceRefresh, refreshConversations],
  );

  useTurnResume({
    enabled: auth.status === "authenticated",
    activeId,
    activeIdRef,
    runningTurn: runningTurnOf(approvalCenter.running, activeId),
    tick: approvalCenter.tick,
    live: liveConversationId === activeId,
    accRef,
    setMessages,
    setStreaming,
    onFinished: handleResumeFinished,
  });

  // 其他会话出现待审批：轻提示一次，点击即可跳过去处理。
  const notifiedApprovalsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (auth.status !== "authenticated") return;
    for (const approval of approvalCenter.approvals) {
      if (approval.conversation_id === activeId) continue;
      if (notifiedApprovalsRef.current.has(approval.request_id)) continue;
      notifiedApprovalsRef.current.add(approval.request_id);
      const summary = conversations.find(
        (item) => item.conversation_id === approval.conversation_id,
      );
      const label =
        String(approval.payload?.tool ?? approval.payload?.question ?? "").trim() ||
        "审批请求";
      const title = truncateText(
        summary?.summary?.trim() || shortId(approval.conversation_id),
        20,
      );
      setApprovalNotice({
        conversationId: approval.conversation_id,
        text: `「${title}」正在等待审批：${label}`,
      });
    }
  }, [activeId, approvalCenter.approvals, auth.status, conversations]);

  const handleLoggedIn = useCallback((user: AuthUser, usingDefaultPassword: boolean) => {
    setAuth({ status: "authenticated", user });
    setDefaultCredentials(usingDefaultPassword);
    setAuthNotice("");
    setError("");
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // 服务端会话可能已失效；前端仍然回到登录页。
    }
    setPasswordDialogOpen(false);
    setMessages([]);
    setConversations([]);
    setActiveId(null);
    activeIdRef.current = null;
    setWorkspace(null);
    setDrawerOpen(false);
    setPreview(null);
    setAuth({ status: "anonymous" });
    setAuthNotice("已退出登录");
    setError("");
  }, [setDrawerOpen, setPreview, setWorkspace]);

  const handlePasswordChanged = useCallback((message: string) => {
    setPasswordDialogOpen(false);
    setMessages([]);
    setConversations([]);
    setActiveId(null);
    activeIdRef.current = null;
    setWorkspace(null);
    setDrawerOpen(false);
    setPreview(null);
    setAuth({ status: "anonymous" });
    setAuthNotice(message);
    setError("");
  }, [setDrawerOpen, setPreview, setWorkspace]);


  const attachmentState = useAttachments({
    activeId,
    activeIdRef,
    setActiveId,
    refreshConversations,
    streaming,
    onError: (message) => setError(message),
  });
  const {
    attachments,
    setAttachments,
    uploading,
    dragging,
    fileInputRef,
    removeAttachment,
    handleFileInputChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
  } = attachmentState;

  const updateMessage = useCallback(
    (index: number, updater: (m: ChatMessage) => ChatMessage) => {
      setMessages((prev) => prev.map((m, i) => (i === index ? updater(m) : m)));
    },
    [],
  );

  const openConversation = useCallback(async (id: string) => {
    // 切会话：作废在途的加载与流式代次，避免旧响应/旧事件写进新会话。
    const openSeq = openSeqRef.current + 1;
    openSeqRef.current = openSeq;
    streamTokenRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setLiveConversationId(null);
    setApprovalNotice(null);

    activeIdRef.current = id;
    setActiveId(id);
    setSidebarOpen(false);
    setMessages([]);
    setStepOpen({});
    setLoadingConversation(true);
    stickToBottomRef.current = true;
    // 附件挂在会话上，切会话必须清空。
    setAttachments([]);
    setWorkspace(null);
    try {
      const detail = (await getConversation(id)) as ConversationDetailShape;
      // 过期响应：用户已经切到别的会话，丢掉这次结果（曾导致跨会话串写 + 审批 404）。
      if (openSeq !== openSeqRef.current || activeIdRef.current !== id) return;
      const built = buildHistory(detail, id);
      accRef.current = built.lastTurn?.acc ?? null;
      const pending = (detail.pending_approvals ?? []).map((item) => ({
        conversation_id: id,
        request_id: item.request_id,
        kind: item.kind,
        payload: item.payload ?? {},
        turn_id: detail.turn_id ?? built.lastTurn?.turnId ?? null,
      }));
      setMessages(
        mergePendingApprovals(built.messages, pending, id, {
          running: Boolean(detail.busy),
        }),
      );
      // 服务端仍在跑：立即进入「执行中」，随后由断线跟进按轨迹游标续上。
      setStreaming(Boolean(detail.busy));
      // 打开会话后立刻同步一次审批状态，不必等下一个 4s 轮询。
      approvalCenter.refresh();
    } catch (e) {
      if (openSeq === openSeqRef.current) setError((e as Error).message);
    } finally {
      if (openSeq === openSeqRef.current) setLoadingConversation(false);
    }
  }, [approvalCenter.refresh, setAttachments, setWorkspace]);

  const handleNewConversation = useCallback(async () => {
    try {
      const { conversation_id } = await createConversation();
      await refreshConversations();
      openSeqRef.current += 1;
      streamTokenRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      setLiveConversationId(null);
      setApprovalNotice(null);
      activeIdRef.current = conversation_id;
      setActiveId(conversation_id);
      setSidebarOpen(false);
      setDrawerOpen(false);
      setLoadingConversation(false);
      setStreaming(false);
      setMessages([]);
      setInput("");
      setAttachments([]);
      setWorkspace(null);
      stickToBottomRef.current = true;
    } catch (e) {
      setError((e as Error).message);
    }
  }, [refreshConversations, setAttachments, setDrawerOpen, setWorkspace]);

  const handleSend = useCallback(async () => {
    const content = input.trim();
    // 已就绪（非 pending）的附件可以独立支撑一次发送：只传文件、不写文字是合法的。
    const ready = attachments.filter((a) => a.parse_status !== "pending");
    // 上传还没结束就发：此时 attachment_id 还没拿到，发出去会丢掉附件。
    if (auth.status !== "authenticated" || auth.user.role === "viewer") return;
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
    setApprovalNotice(null);
    setStreaming(true);
    setSidebarOpen(false);
    stickToBottomRef.current = true;
    if (textareaRef.current) textareaRef.current.style.height = "auto";

    const userMsg: ChatMessage = {
      role: "user",
      content,
      conversationId,
      steps: [],
      // 本地先挂上附件：气泡立刻出现芯片，无需等服务端事件回传。
      attachments: sending.length > 0 ? sending : undefined,
    };
    const assistantMsg: ChatMessage = {
      role: "assistant",
      content: "",
      conversationId,
      streaming: true,
      steps: [],
    };
    // 用局部下标而不是 ref：切会话后旧流的回调不会再改到别的消息上。
    const assistantIndex = messages.length + 1;
    const userIndex = assistantIndex - 1;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;
    const token = streamTokenRef.current;
    setLiveConversationId(conversationId);

    const result = await sendTurn({
      conversationId,
      content,
      agent: selectedAgent || undefined,
      attachmentIds: sending.map((item) => item.attachment_id),
      assistantIndex,
      controller,
      isActive: () => activeIdRef.current === conversationId,
      accRef,
      setMessages,
      onEvent: (event) => {
        // 观测日志：浏览器控制台查看收到的每个 SSE 事件，便于排查断点。
        // eslint-disable-next-line no-console
        console.log(
          "[sse]",
          event.type,
          event.type === "assistant_delta" || event.type === "reasoning_delta"
            ? `len=${((event.text as string) ?? "").length}`
            : event.tool_name
              ? `tool=${event.tool_name}`
              : "",
        );
        if (event.type === "error") setError((event.text as string) ?? "出错了");
        // 服务端回传的解析结果更权威（本地挂的是上传时的快照），用它刷新用户气泡。
        if (event.type === "attachments_ready") {
          const list = event.attachments as AttachmentInfo[] | undefined;
          if (list?.length) {
            updateMessage(userIndex, (message) =>
              message.role === "user" ? { ...message, attachments: list } : message,
            );
          }
        }
      },
      onTurnComplete: (id) => {
        // 任务结束：立即刷新一次「会话文件」列表，抽屉没打开也更新缓存。
        flushWorkspaceRefresh(id);
      },
    });

    if (result.error) setError(result.error);
    // 只让「当前这一代」的流收尾，避免切会话后旧 finally 把新一轮的 streaming 关掉。
    if (streamTokenRef.current === token) {
      setStreaming(false);
      setLiveConversationId((current) => (current === conversationId ? null : current));
    }
    if (abortRef.current === controller) abortRef.current = null;
    // 断流 / 中断时可能没收到 turn_complete：这里再刷新一次文件列表。
    flushWorkspaceRefresh(conversationId);
    void refreshConversations();
  }, [
    accRef,
    activeId,
    attachments,
    auth,
    flushWorkspaceRefresh,
    input,
    messages.length,
    refreshConversations,
    selectedAgent,
    setMessages,
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

  const toggleStep = useCallback((key: string) => {
    setStepOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const readOnly = auth.status === "authenticated" && auth.user.role === "viewer";
  // 有文字，或至少有一个"已就绪"的附件，即可发送。
  const canSend =
    !readOnly &&
    !streaming &&
    !uploading &&
    (input.trim().length > 0 || attachments.some((a) => a.parse_status !== "pending"));
  // 流式回答期间（以及上传中）禁止增删附件：附件必须属于下一轮，中途变更会挂错轮次。
  const attachmentsLocked = streaming || uploading || readOnly;

  if (auth.status === "checking") {
    return (
      <div className="auth-loading" role="status">
        正在检查登录状态…
      </div>
    );
  }

  if (auth.status === "anonymous") {
    return (
      <LoginPage
        defaultCredentials={defaultCredentials}
        notice={authNotice}
        onSuccess={handleLoggedIn}
      />
    );
  }

  return (
    <div className={`layout${sidebarOpen ? " sidebar-open" : ""}`}>
      <Sidebar
        sidebarOpen={sidebarOpen}
        setSidebarOpen={setSidebarOpen}
        conversations={conversations}
        filteredConversations={filteredConversations}
        activeId={activeId}
        conversationFilter={conversationFilter}
        setConversationFilter={setConversationFilter}
        onNewConversation={() => void handleNewConversation()}
        onOpenConversation={(id) => void openConversation(id)}
        pendingApprovalCounts={approvalCenter.counts}
        readOnly={readOnly}
      />

      <main className="main">
        <TopBar
          sessionTitle={sessionTitle}
          pendingApproval={Boolean(pendingApproval)}
          streaming={streaming}
          activeSummary={activeSummary}
          selectedAgent={selectedAgent}
          setSelectedAgent={setSelectedAgent}
          agents={agents}
          markdownDefault={markdownDefault}
          setMarkdownDefault={setMarkdownDefault}
          setRenderOverride={setRenderOverride}
          drawerOpen={drawerOpen}
          sidebarOpen={sidebarOpen}
          onOpenSidebar={() => setSidebarOpen(true)}
          onToggleDrawer={() => setDrawerOpen((v) => !v)}
          user={auth.user.username ? auth.user : null}
          onLogout={() => void handleLogout()}
          onChangePassword={() => setPasswordDialogOpen(true)}
        />
        <MessageList
          messages={messages}
          agents={agents}
          renderOverride={renderOverride}
          setRenderOverride={setRenderOverride}
          markdownDefault={markdownDefault}
          copiedIndex={copiedIndex}
          onCopyMessage={(index, text) => void handleCopyMessage(index, text)}
          stepOpen={stepOpen}
          onToggleStep={toggleStep}
          onApproval={approvalCenter.handleApproval}
          onPreview={(path) => void previewPath(path)}
          onDownload={(path) => void downloadPath(path)}
          downloadingPath={downloadingPath}
          loadingConversation={loadingConversation}
          onPickPrompt={(prompt) => {
            setInput(prompt);
            window.requestAnimationFrame(() => textareaRef.current?.focus());
          }}
          dragging={dragging}
          messagesRef={messagesRef}
          onScroll={handleMessagesScroll}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        />

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
        {approvalNotice && (
          <div className="toast approval" role="status">
            <InfoIcon size={17} />
            <span className="toast-text">{approvalNotice.text}</span>
            <button
              type="button"
              className="toast-action"
              onClick={() => {
                const target = approvalNotice.conversationId;
                setApprovalNotice(null);
                void openConversation(target);
              }}
            >
              去处理
            </button>
            <button
              type="button"
              className="toast-close"
              title="关闭提示"
              aria-label="关闭提示"
              onClick={() => setApprovalNotice(null)}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        )}
      <Composer
        attachments={attachments}
        onRemoveAttachment={removeAttachment}
        attachmentsLocked={attachmentsLocked}
        uploading={uploading}
        dragging={dragging}
        input={input}
        setInput={setInput}
        onSend={() => void handleSend()}
        onInterrupt={() => void handleInterrupt()}
        canSend={canSend}
        streaming={streaming}
        pendingQuestion={Boolean(pendingQuestion)}
        pendingApproval={Boolean(pendingApproval)}
        readOnly={readOnly}
        textareaRef={textareaRef}
        fileInputRef={fileInputRef}
        onFileInputChange={handleFileInputChange}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      />
      </main>

    <WorkspaceDrawer
      drawerOpen={drawerOpen}
      setDrawerOpen={setDrawerOpen}
      activeId={activeId}
      workspace={workspace}
      workspaceLoading={workspaceLoading}
      copiedDir={copiedDir}
      downloadingPath={downloadingPath}
      fileGroups={fileGroups}
      onCopyDir={() => void handleCopyDir()}
      onPreview={(path) => void previewPath(path)}
      onDownload={(path) => void downloadPath(path)}
    />

      <PreviewModal preview={preview} onClose={() => setPreview(null)} />

      {passwordDialogOpen ? (
        <PasswordDialog
          onClose={() => setPasswordDialogOpen(false)}
          onChanged={handlePasswordChanged}
        />
      ) : null}
    </div>
  );
}
