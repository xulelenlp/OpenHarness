/** 会话标题与侧栏搜索过滤（纯函数，便于复用与自检）。 */

import { truncateText } from "./format";
import type { ChatMessage, ConversationSummary } from "../types";

/** 顶栏标题：优先用服务端摘要，其次第一条用户消息，最后「新对话」。 */
export function conversationTitle(
  summary: string | undefined,
  messages: ChatMessage[],
): string {
  const text = summary?.trim();
  if (text) return truncateText(text, 48);
  const firstUser = messages.find(
    (message) => message.role === "user" && message.content.trim(),
  );
  return firstUser ? truncateText(firstUser.content, 48) : "新对话";
}

/** 侧栏搜索：摘要 / 预览 / 会话 id 三处命中即可。 */
export function filterConversations(
  conversations: ConversationSummary[],
  query: string,
): ConversationSummary[] {
  const keyword = query.trim().toLowerCase();
  if (!keyword) return conversations;
  return conversations.filter((conversation) => {
    const text = `${conversation.summary ?? conversation.preview ?? ""} ${conversation.conversation_id}`;
    return text.toLowerCase().includes(keyword);
  });
}
