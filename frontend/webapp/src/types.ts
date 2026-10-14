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

export interface ToolCallEvent {
  tool_name?: string;
  input?: unknown;
  output?: unknown;
  is_read_only?: boolean;
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
  toolEvents?: ToolCallEvent[];
  approval?: ApprovalEvent | null;
  error?: boolean;
  streaming?: boolean;
}
