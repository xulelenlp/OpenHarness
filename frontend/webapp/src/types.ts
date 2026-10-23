export interface AgentInfo {
  id: string;
  kind: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface AgentListResponse {
  default_agent: string;
  agents: AgentInfo[];
}

export interface ConversationSummary {
  conversation_id: string;
  /** 会话摘要（服务端 snapshot 里的 summary）。 */
  summary?: string;
  message_count?: number;
  model?: string;
  /** epoch 秒。 */
  created_at?: number;
  /** 兼容旧后端 / 旧前端字段。 */
  updated_at?: string;
  turn_count?: number;
  preview?: string;
}

/** 模型给出的一个可选答案。 */
export interface QuestionOption {
  /** 选项文案，也是回传给工具的答案。 */
  label: string;
  /** 可选的一句话说明。 */
  description?: string;
  /** 是否被模型标记为推荐项（只做视觉提示，不默认选中）。 */
  recommended?: boolean;
}

/** 执行轨迹中的单个操作步骤（对应一次工具调用 / 审批 / 状态 / 思考 / 错误）。 */
export type StepKind = "status" | "tool" | "approval" | "error" | "reasoning";

export type StepStatus = "running" | "done" | "error";

export interface Step {
  /** 前端递增唯一键（同一 turn 内不重复）。 */
  key: string;
  kind: StepKind;
  status: StepStatus;
  /** 标题：工具名 / 状态文字 / 错误文案。 */
  title: string;
  ts?: number;
  // tool
  toolName?: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  toolProgress?: string;
  // approval
  requestId?: string;
  approvalKind?: "permission" | "edit" | "question";
  payload?: Record<string, unknown>;
  /** 已回答问题的最终答案（历史回放也保留）。 */
  answer?: string;
  // status / error
  text?: string;
}

export interface ApprovalEvent {
  request_id: string;
  kind: "permission" | "edit" | "question";
  tool?: string;
  payload?: Record<string, unknown>;
}

/**
 * 附件解析状态。
 *
 * `pending` 只作为契约里的兜底值存在：后端在**本轮开始前**就把解析做完，
 * 所以上传响应与 `attachments_ready` 事件通常已经是 `ok` / `failed`。
 */
export type ParseStatus = "ok" | "failed" | "pending";

/** 会话附件（上传接口返回，也会随 `attachments_ready` SSE 事件下发）。 */
export interface AttachmentInfo {
  /** `att_` 前缀，发送消息时用它告诉后端本轮引用了哪些附件。 */
  attachment_id: string;
  /** 原始文件名（展示用）。 */
  name: string;
  /** "word" | "excel" | "text" | "markdown" | "csv" | "json" | "yaml" | "xml"。 */
  kind: string;
  /** 字节数。 */
  size: number;
  /** epoch 秒。 */
  uploaded_at: number;
  parse_status: ParseStatus;
  /** 解析失败原因（中文），仅 `failed` 时有值。 */
  parse_error?: string;
  /** 解析出的字符数，0 表示没解析出正文。 */
  text_chars: number;
  /** 相对会话目录，如 `uploads/报告.docx`。 */
  stored_path: string;
  /** 解析产物相对路径，如 `extracted/报告.docx.txt`。 */
  extracted_path: string;
  meta?: { sheets?: string[]; rows?: number; paragraphs?: number; tables?: number; [k: string]: unknown };
}

/** 会话临时目录里的单个文件（含 Agent 本轮生成的脚本 / 产出）。 */
export interface WorkspaceFile {
  /** 相对会话目录的路径，如 `uploads/报告.docx`。 */
  path: string;
  /** 所属子目录（uploads / extracted / scripts / tmp / outputs，根目录为空串）。 */
  dir: string;
  size: number;
  /** epoch 秒。 */
  mtime: number;
}

/**
 * 文件图标分类（与后端 `qaapp/turn_files.py` 的 `classify_kind` 对齐）。
 *
 * 只用于展示：色块图标 + 类型文案，不参与下载/预览判断。
 */
export type FileKind =
  | "word"
  | "excel"
  | "pptx"
  | "pdf"
  | "markdown"
  | "image"
  | "script"
  | "data"
  | "archive"
  | "text"
  | "other";

/** 本轮新增/修改的一个文件（`turn_files` 事件 / 前端兜底推导共用）。 */
export interface ChangedFile {
  /** 相对会话目录，如 `scripts/analyze.py`。 */
  path: string;
  /** 所属目录（scripts / tmp / outputs 等，根目录为空串）。 */
  dir: string;
  action: "created" | "modified";
  size: number;
  mtime: number;
  /** 新增/删除行数；二进制或过大的文件为 null。 */
  added: number | null;
  removed: number | null;
  kind: FileKind;
}

/** `outputs/` 下的一份交付物，可直接下载。 */
export interface ResultFile {
  path: string;
  name: string;
  /** 去掉扩展名的标题（展示用）。 */
  title: string;
  kind: FileKind;
  size: number;
  mtime: number;
  /** 后端拼好的下载路径；前端实际用 `path` + fetch 下载（避免中文未编码）。 */
  download_path: string;
}

/** `GET /conversations/{id}/workspace` 的响应。 */
export interface ConversationWorkspace {
  conversation_id: string;
  /** 会话目录绝对路径，供用户复制后在本地打开。 */
  dir: string;
  files: WorkspaceFile[];
  total_bytes: number;
  /** true = 文件过多被截断，界面要提示"未列全"。 */
  truncated: boolean;
}

export interface AuthUser {
  username: string;
  /** admin = 可读写；viewer = 只读（GET）。 */
  role: "admin" | "viewer";
  display_name: string;
}

export type AuthState =
  | { status: "checking" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: AuthUser };

export interface SessionInfo {
  auth_enabled: boolean;
  user: AuthUser | null;
  /** 是否仍有账户在用默认密码（登录页 / 顶栏提示）。 */
  default_password?: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  agentName?: string;
  turnId?: string;
  /** 该轮的执行轨迹步骤序列（替代旧的 toolEvents）。 */
  steps: Step[];
  approval?: ApprovalEvent | null;
  /** 本轮用户消息携带的附件；历史回放时由 `attachments_ready` 事件还原。 */
  attachments?: AttachmentInfo[];
  /** 本轮新增/修改的文件（中间过程文件卡片）。 */
  changedFiles?: ChangedFile[];
  /** 本轮产出的交付物（结果文件卡片，可直接下载）。 */
  resultFiles?: ResultFile[];
  /** true = 卡片来自旧轨迹的工具输出推导（没有 `turn_files` 事件）。 */
  filesDerived?: boolean;
  /** true = 本轮文件过多，仅展示了前 N 个。 */
  filesTruncated?: boolean;
  error?: boolean;
  streaming?: boolean;
}
