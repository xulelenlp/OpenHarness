/** 历史轨迹 → 消息列表（实时流、断线跟进与刷新回放共用同一套事件归约）。 */

import { deriveTurnFilesFromSteps, keepResultFiles } from "../turnFiles";
import type { ChatMessage } from "../types";
import { makeAccumulator, reduceEvent } from "../turn";
import type { TurnAccumulator } from "../turn";
import { extractText } from "./format";

export interface ConversationDetailShape {
  conversation_id: string;
  messages?: Array<{ role: string; content: unknown }>;
  turns?: Array<{
    turn_id: string;
    message_id?: string;
    events?: Array<Record<string, unknown>>;
  }>;
  /** 服务端此刻是否仍在执行该会话（决定要不要做断线跟进）。 */
  busy?: boolean;
  /** 正在执行的轮次 id（busy=true 时有效）。 */
  turn_id?: string | null;
  /** 服务端内存里仍然挂着的待审批（与 SSE 连接无关）。 */
  pending_approvals?: Array<{
    request_id: string;
    kind: "permission" | "edit" | "question";
    payload: Record<string, unknown>;
  }>;
}

/** 最后一轮的累积状态：断线跟进以它为起点继续叠加事件。 */
export interface HistoryLastTurn {
  turnId: string;
  index: number;
  /** 该轮已有的事件条数，等价于服务端轨迹游标的 `total`。 */
  eventCount: number;
  acc: TurnAccumulator;
}

export interface HistoryResult {
  messages: ChatMessage[];
  lastTurn: HistoryLastTurn | null;
}

export function buildHistory(
  detail: ConversationDetailShape,
  conversationId?: string,
): HistoryResult {
  const owner =
    conversationId ??
    (detail.conversation_id as string | undefined) ??
    "";
  const userTexts: string[] = [];
  for (const message of detail.messages ?? []) {
    if (message.role === "user") userTexts.push(extractText(message.content));
  }

  const rebuilt: ChatMessage[] = [];
  let lastTurn: HistoryLastTurn | null = null;
  (detail.turns ?? []).forEach((turn, index) => {
    const events = turn.events ?? [];
    const acc = makeAccumulator();
    for (const event of events) reduceEvent(acc, event);
    const userText = userTexts[index] ?? "";
    // 只发附件不写文字是合法的一轮，此时 content 为空但必须仍然渲染气泡与芯片。
    if (userText || acc.attachments?.length) {
      rebuilt.push({
        role: "user",
        content: userText,
        conversationId: owner,
        turnId: turn.turn_id,
        steps: [],
        attachments: acc.attachments,
      });
    }
    const finished = Boolean(acc.finished);
    const hasEventFiles =
      (acc.changedFiles?.length ?? 0) > 0 || (acc.resultFiles?.length ?? 0) > 0;
    const derived = finished && !hasEventFiles ? deriveTurnFilesFromSteps(acc.steps) : null;
    rebuilt.push({
      role: "assistant",
      content: acc.content,
      conversationId: owner,
      agentName: acc.agentName,
      turnId: turn.turn_id,
      steps: acc.steps,
      changedFiles: finished ? acc.changedFiles ?? derived?.changed : undefined,
      resultFiles: finished
        ? keepResultFiles(acc.resultFiles ?? derived?.results ?? [])
        : undefined,
      filesDerived: finished && !hasEventFiles && Boolean(derived?.changed.length),
      filesTruncated: finished ? acc.filesTruncated : undefined,
      streaming: false,
    });
    lastTurn = {
      turnId: turn.turn_id,
      index: rebuilt.length - 1,
      eventCount: events.length,
      acc,
    };
  });
  return { messages: rebuilt, lastTurn };
}

export function buildHistoryMessages(
  detail: ConversationDetailShape,
  conversationId?: string,
): ChatMessage[] {
  return buildHistory(detail, conversationId).messages;
}
