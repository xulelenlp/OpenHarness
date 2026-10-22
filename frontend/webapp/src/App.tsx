import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  bootstrap,
  createConversation,
  getConversation,
  hasStaticToken,
  interruptConversation,
  listAgents,
  listConversations,
  resolveApproval,
  streamMessage,
} from "./api";
import { makeAccumulator, reduceEvent } from "./turn";
import type { TurnAccumulator } from "./turn";
import { deriveTurnFilesFromSteps, keepResultFiles } from "./turnFiles";
import { copyText } from "./clipboard";
import { Composer } from "./components/Composer";
import { MessageList } from "./components/MessageList";
import { PreviewModal } from "./components/PreviewModal";
import { Sidebar } from "./components/Sidebar";
import type { ApprovalAction } from "./components/StepCard";
import { TopBar } from "./components/TopBar";
import { WorkspaceDrawer } from "./components/WorkspaceDrawer";
import { FILE_WRITING_TOOLS } from "./chat/constants";
import { buildHistoryMessages, type ConversationDetailShape } from "./chat/history";
import { useAttachments } from "./hooks/useAttachments";
import { useWorkspaceFiles } from "./hooks/useWorkspaceFiles";
import { approvalResultLabel, approvalStepTitle, isPendingQuestion } from "./questions";
import { truncateText } from "./chat/format";
import {
  AlertIcon,
  CloseIcon,
} from "./icons";
import type {
  AgentInfo,
  AttachmentInfo,
  ChatMessage,
  ConversationSummary,
  Step,
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
  const [stepOpen, setStepOpen] = useState<Record<string, boolean>>({});
  /** 助手回答是否按 Markdown 渲染（全局默认，存本地）。 */
  const [markdownDefault, setMarkdownDefault] = useState<boolean>(
    () => localStorage.getItem("qaapp_render_markdown") !== "0",
  );
  /** 单条消息的渲染模式覆盖：true = 强制 Markdown，false = 强制源码。 */
  const [renderOverride, setRenderOverride] = useState<Record<number, boolean>>({});
  /** 刚复制过的消息下标（用于按钮上的短暂反馈）。 */
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

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
  const assistantIndexRef = useRef<number>(-1);
  /** 最新 activeId：任务结束时的异步刷新用它判断用户是否已经切走。 */
  const activeIdRef = useRef<string | null>(null);
  /** 本轮流式事件的累加器；供审批处理同步修改。 */
  const accRef = useRef<TurnAccumulator | null>(null);
  /** 正在提交的审批 id（同步判重）。 */
  const submittingApprovalsRef = useRef<Set<string>>(new Set());

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

  const sessionTitle = useMemo(() => {
    const summary = activeSummary?.summary?.trim();
    if (summary) return truncateText(summary, 48);
    const firstUser = messages.find(
      (message) => message.role === "user" && message.content.trim(),
    );
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

  useEffect(() => {
    // 先向服务端建立浏览器会话：服务端读取配置并签发 HttpOnly Cookie。
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
      await Promise.all([refreshAgents(), refreshConversations()]);
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshAgents, refreshConversations]);

  const workspaceFiles = useWorkspaceFiles({
    activeId,
    activeIdRef,
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
    scheduleWorkspaceRefresh,
    flushWorkspaceRefresh,
    handleCopyDir,
    downloadPath,
    previewPath,
  } = workspaceFiles;

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
      setMessages(buildHistoryMessages(detail));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingConversation(false);
    }
  }, [setAttachments, setWorkspace]);

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
  }, [refreshConversations, setAttachments, setDrawerOpen, setWorkspace]);

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
        flushWorkspaceRefresh(conversationId);
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
        flushWorkspaceRefresh(conversationId);
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
    flushWorkspaceRefresh,
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
          onApproval={handleApproval}
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
    </div>
  );
}
