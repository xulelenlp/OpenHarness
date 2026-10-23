import type {
  AgentListResponse,
  AttachmentInfo,
  AuthUser,
  ConversationSummary,
  ConversationWorkspace,
  SessionInfo,
} from "./types";

/**
 * 后端基址，按部署环境可配置（见 config/frontend/.env）：
 *
 * - **留空（默认）= 同源**
 *   · 开发态：由 Vite dev server 把 `/api`、`/healthz` 代理到 `VITE_API_TARGET`
 *     （默认 `http://127.0.0.1:8787`）
 *   · 生产态：前端由 qaapp 的 `static_dir` 一同托管，天然同源
 * - **非空 = 浏览器直连该地址**，用于前后端分离部署，
 *   例如 `VITE_API_BASE_URL=https://api.example.com`（需后端 `cors_origins` 放行）
 *
 * 配置方式：写进 `config/frontend/.env`（已被 gitignore 忽略），
 * 或启动/构建时注入环境变量。
 */
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

/**
 * 构建时写死的 Bearer token（可选，仅用于「严格 Bearer」部署）。
 *
 * 默认不需要：页面启动时调用 `GET /api/v1/bootstrap`，后端读取 `webapp.json`
 * 的 `api_tokens` 并签发 HttpOnly 会话 Cookie，用户无需在页面里填写 token。
 * 只有 `browser_auth.enabled=false`（公网/SSO 场景）时才需要在构建时注入本变量，
 * 例如 `VITE_API_TOKEN=xxx npm run build`。
 */
const STATIC_TOKEN = (import.meta.env.VITE_API_TOKEN ?? "").trim();

export interface BootstrapInfo {
  /** 服务端是否要求鉴权（token 或登录）。 */
  auth_required: boolean;
  /** 是否启用临时账户登录（TEMP-AUTH）。 */
  login_required?: boolean;
  /** 登录是否启用（与 login_required 同义，供前端判断展示登录页）。 */
  auth_enabled?: boolean;
  /** 是否仍有账户使用默认密码。 */
  default_credentials?: boolean;
  /** 服务端是否已签发浏览器快捷会话 Cookie（登录模式下恒为 false）。 */
  browser_session: boolean;
  /** 会话 Cookie 有效期（秒），0 表示未签发。 */
  session_ttl_seconds?: number;
}

let bootstrapPromise: Promise<BootstrapInfo> | null = null;

export function hasStaticToken(): boolean {
  return Boolean(STATIC_TOKEN);
}

/**
 * 建立浏览器会话：服务端从项目配置读取 token，签发 HttpOnly Cookie。
 * 幂等且带缓存；失败后清空缓存，允许用户刷新/重试。
 */
export function bootstrap(): Promise<BootstrapInfo> {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    const resp = await fetch(`${BASE}/api/v1/bootstrap`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (!resp.ok) throw await errorFrom(resp);
    return (await resp.json()) as BootstrapInfo;
  })().catch((err: unknown) => {
    bootstrapPromise = null;
    throw err;
  });
  return bootstrapPromise;
}

function authHeaders(): Record<string, string> {
  return STATIC_TOKEN ? { Authorization: `Bearer ${STATIC_TOKEN}` } : {};
}

function notifyUnauthorized(): void {
  window.dispatchEvent(new CustomEvent("qaapp:unauthorized"));
}

async function request<T>(path: string, init?: RequestInit, retry = true): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (resp.status === 401) {
    // 旧的 bootstrap 快捷会话会在这里补一次 Cookie；启用登录后不再自动续期，
    // 直接通知界面回到登录页（SameSite/HttpOnly，前端拿不到 Cookie 内容）。
    if (retry && !STATIC_TOKEN) {
      const info = await bootstrap().catch(() => null);
      if (!info?.login_required && !info?.auth_enabled) {
        return request<T>(path, init, false);
      }
    }
    notifyUnauthorized();
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error((body as { message?: string }).message || `HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// 临时账户登录（TEMP-AUTH）：正式接入 OSS 时替换这一段与 LoginPage。
// ---------------------------------------------------------------------------

/** 当前登录用户；未登录（401）返回 null，不弹全局错误。 */
export async function getSession(): Promise<SessionInfo | null> {
  const resp = await fetch(`${BASE}/api/v1/auth/me`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (resp.status === 401) return null;
  if (!resp.ok) throw await errorFrom(resp);
  return (await resp.json()) as SessionInfo;
}

export interface LoginResult {
  user: AuthUser;
  expires_in: number;
  default_password: boolean;
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const resp = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) throw await errorFrom(resp);
  return (await resp.json()) as LoginResult;
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/api/v1/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: authHeaders(),
  });
}

export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<void> {
  const resp = await fetch(`${BASE}/api/v1/auth/password`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (!resp.ok) throw await errorFrom(resp);
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

// ---------------------------------------------------------------------------
// 会话附件 / 会话文件
// ---------------------------------------------------------------------------

/** 统一的错误提取：后端错误体是 `{ status, code, message }`。 */
async function errorFrom(resp: Response): Promise<Error> {
  const body = await resp.json().catch(() => ({}));
  return new Error((body as { message?: string }).message || `HTTP ${resp.status}`);
}

/**
 * 上传附件（1..2 个）。
 *
 * 不能用上面的 `request()`：它会强制 `Content-Type: application/json`，
 * 而 multipart 必须让浏览器自己带上 `boundary`（手写会丢掉 boundary，后端直接解析失败）。
 * 所以这里另写 fetch：带 Cookie（默认浏览器会话）或可选的静态 Bearer。
 */
export async function uploadAttachments(
  conversationId: string,
  files: File[],
): Promise<{ conversation_id: string; attachments: AttachmentInfo[] }> {
  const form = new FormData();
  // 字段名固定 files，可重复；顺序即用户选择顺序。
  for (const file of files) form.append("files", file);

  const resp = await fetch(`${BASE}/api/v1/conversations/${conversationId}/attachments`, {
    method: "POST",
    credentials: "include",
    headers: { ...authHeaders() },
    body: form,
  });
  if (resp.status === 401) notifyUnauthorized();
  if (!resp.ok) throw await errorFrom(resp);
  return (await resp.json()) as { conversation_id: string; attachments: AttachmentInfo[] };
}

/** 列出会话临时目录里的文件（含 Agent 生成的脚本 / 产出）。 */
export function listConversationFiles(conversationId: string): Promise<ConversationWorkspace> {
  return request<ConversationWorkspace>(`/api/v1/conversations/${conversationId}/workspace`);
}

/** 相对路径按 `/` 分段编码：文件名里的空格 / 中文 / `#` 都不会破坏 URL。 */
function encodePath(relativePath: string): string {
  return relativePath
    .split("/")
    .filter((seg) => seg.length > 0)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
}

/**
 * 从 `Content-Disposition` 尽力解析文件名。
 *
 * 优先 RFC 5987 的 `filename*=UTF-8''%E6%8A%A5%E5%91%8A.docx`（后端返回中文名时
 * 通常用这种写法，纯 `filename=` 放中文会变乱码），解析不到就用路径末段。
 */
function filenameFromDisposition(header: string | null, relativePath: string): string {
  const fallback = relativePath.split("/").filter(Boolean).pop() ?? relativePath;
  if (!header) return fallback;

  const encoded = /filename\*\s*=\s*([^;]+)/i.exec(header);
  if (encoded) {
    let value = encoded[1].trim().replace(/^"|"$/g, "");
    // 形如 UTF-8''%E6%8A%A5%E5%91%8A.docx，前面两段是 charset 与 language
    const m = /^[^']*'[^']*'(.*)$/.exec(value);
    if (m) value = m[1];
    try {
      const decoded = decodeURIComponent(value);
      if (decoded) return decoded;
    } catch {
      if (value) return value;
    }
  }

  const plain = /filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)/i.exec(header);
  const raw = (plain?.[1] ?? plain?.[2] ?? "").trim();
  return raw || fallback;
}

/**
 * 下载会话文件。
 *
 * 必须走 fetch + Blob：下载接口要求鉴权（Cookie 或 Bearer），而 `<a href>` /
 * `window.open` 带不了请求头，把 token 拼进 URL 又会泄漏到历史记录与日志里。
 */
export async function fetchConversationFile(
  conversationId: string,
  relativePath: string,
): Promise<{ blob: Blob; filename: string }> {
  const resp = await fetch(
    `${BASE}/api/v1/conversations/${conversationId}/files/${encodePath(relativePath)}`,
    { credentials: "include", headers: { Accept: "*/*", ...authHeaders() } },
  );
  if (!resp.ok) throw await errorFrom(resp);
  return {
    blob: await resp.blob(),
    filename: filenameFromDisposition(resp.headers.get("Content-Disposition"), relativePath),
  };
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
 *
 * `attachmentIds` 是后加的第 6 个可选参数（前 5 个签名保持不变，老调用方不受影响）：
 * 非空时 body 里带上 `attachment_ids`，告诉后端本轮引用了哪些已上传附件。
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  agent: string | undefined,
  onEvent: (event: Record<string, unknown>) => void,
  signal?: AbortSignal,
  attachmentIds?: string[],
): Promise<void> {
  const doFetch = (): Promise<Response> =>
    fetch(`${BASE}/api/v1/conversations/${conversationId}/messages`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...authHeaders(),
      },
      body: JSON.stringify({
        content,
        agent,
        stream: true,
        ...(attachmentIds && attachmentIds.length ? { attachment_ids: attachmentIds } : {}),
      }),
      signal,
    });

  let resp = await doFetch();
  // 旧部署：服务重启后快捷会话密钥会变，重建一次再重试；
  // 启用登录后：401 表示会话过期，直接回到登录页。
  if (resp.status === 401 && !STATIC_TOKEN) {
    const info = await bootstrap().catch(() => null);
    if (!info?.login_required && !info?.auth_enabled) {
      bootstrapPromise = null;
      await bootstrap();
      resp = await doFetch();
    } else {
      notifyUnauthorized();
    }
  }

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
