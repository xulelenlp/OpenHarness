import type { AgentListResponse, ConversationSummary } from "./types";

// 默认走同源：开发态由 Vite 代理到 :8787，生产态由 qaapp static_dir 托管。
const BASE = "";

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("qaapp_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export function listAgents(): Promise<AgentListResponse> {
  return request<AgentListResponse>("/api/v1/agents");
}

export function createConversation(): Promise<{ conversation_id: string }> {
  return request<{ conversation_id: string }>("/api/v1/conversations", { method: "POST" });
}

export function listConversations(): Promise<{ conversations: ConversationSummary[] }> {
  return request<{ conversations: ConversationSummary[] }>("/api/v1/conversations");
}

export function getConversation(conversationId: string): Promise<unknown> {
  return request(`/api/v1/conversations/${conversationId}`);
}

export function resolveApproval(
  conversationId: string,
  requestId: string,
  decision: "approve" | "deny" | "always",
): Promise<unknown> {
  return request(`/api/v1/conversations/${conversationId}/approvals/${requestId}`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}

export function interruptConversation(conversationId: string): Promise<unknown> {
  return request(`/api/v1/conversations/${conversationId}/interrupt`, { method: "POST" });
}

/**
 * 发送消息并以 SSE 流式读取。
 * 返回一个可取消的句柄；`onEvent` 收到服务端推送的每个事件对象。
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  agent: string | undefined,
  onEvent: (event: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(`${BASE}/api/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    body: JSON.stringify({ content, agent, stream: true }),
    signal,
  });

  if (!resp.ok || !resp.body) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // 解析 SSE 帧（event: xxx\ndata: {...}\n\n）
  const dispatch = (chunk: string) => {
    let eventName = "message";
    for (const line of chunk.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const raw = line.slice(5).trim();
        if (!raw) continue;
        try {
          onEvent({ type: eventName, ...(JSON.parse(raw) as Record<string, unknown>) });
        } catch {
          // 忽略无法解析的数据行
        }
      }
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) dispatch(frame);
  }
  if (buffer.trim()) dispatch(buffer);
}
