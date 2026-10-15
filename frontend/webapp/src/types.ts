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
  updated_at?: string;
  turn_count?: number;
  preview?: string;
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
  error?: boolean;
  streaming?: boolean;
}
