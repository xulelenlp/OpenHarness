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

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  agentName?: string;
  turnId?: string;
  /** 该轮的执行轨迹步骤序列（替代旧的 toolEvents）。 */
  steps: Step[];
  approval?: ApprovalEvent | null;
  error?: boolean;
  streaming?: boolean;
}
