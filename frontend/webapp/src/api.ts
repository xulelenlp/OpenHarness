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
  reply?: string,
): Promise<unknown> {
  return request(`/api/v1/conversations/${conversationId}/approvals/${requestId}`, {
    method: "POST",
    body: JSON.stringify({ decision, reply }),
  });
}

export function interruptConversation(conversationId: string): Promise<unknown> {
  return request(`/api/v1/conversations/${conversationId}/interrupt`, { method: "POST" });
}

/** SSE 帧分隔符：SSE 规范允许 \r\n / \n / \r，sse-starlette 默认输出 \r\n。 */
const SSE_FRAME_SEP = /\r\n\r\n|\n\n|\r\r/;

/** 解析单个 SSE 帧（`event:` 与 `data:` 两种字段行）。 */
function dispatchSseFrame(
  frame: string,
  onEvent: (event: Record<string, unknown>) => void,
): void {
  let eventName = "message";
  for (const line of frame.split(/\r\n|\n|\r/)) {
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
}

/**
 * 从 SSE 缓冲区里取出所有**完整帧**并派发，返回尚未消费的残留缓冲。
 *
 * 必须用正则匹配三种换行组合：若只按 `"\n\n"` 分帧，而服务端（sse-starlette
 * 默认 sep=`"\r\n"`）输出的帧以 `\r\n\r\n` 结尾，则永远匹配不到，全部事件会
 * 积压在缓冲区里、直到整个响应结束才一次性显示——表现为长时间停在"思考中…"，
 * 审批请求也因此无法及时送达用户。
 *
 * 导出以便单测/自检。
 */
export function consumeSseBuffer(
  buffer: string,
  onEvent: (event: Record<string, unknown>) => void,
): string {
  for (;;) {
    const match = SSE_FRAME_SEP.exec(buffer);
    if (!match) break;
    const frame = buffer.slice(0, match.index);
    buffer = buffer.slice(match.index + match[0].length);
    if (frame.trim()) dispatchSseFrame(frame, onEvent);
  }
  return buffer;
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

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = consumeSseBuffer(buffer, onEvent);
  }
  // 兜底：流结束时若仍有未闭合的残留帧，补一个分隔符强制解析。
  if (buffer.trim()) consumeSseBuffer(`${buffer}\n\n`, onEvent);
}
