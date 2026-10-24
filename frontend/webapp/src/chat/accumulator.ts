/**
 * 把一轮的累加器写回消息。
 *
 * 实时 SSE、刷新/切走后的轨迹跟进、以及审批卡片的合并都汇到这里，
 * 保证「在线看到的」与「断线恢复后的」完全一致。
 */

import { keepResultFiles, deriveTurnFilesFromSteps } from "../turnFiles";
import type { TurnAccumulator } from "../turn";
import type { ChatMessage, Step } from "../types";

export interface ApplyAccumulatorOptions {
  conversationId: string;
  turnId?: string | null;
  /** true = 该轮已结束（落文件卡片、去掉流式光标）。 */
  finished?: boolean;
  /** 事件里出现过 error。 */
  error?: boolean;
}

/** 找出某个会话某一轮对应的助手消息下标（没有则 -1）。 */
export function findTurnMessageIndex(
  messages: ChatMessage[],
  conversationId: string,
  turnId: string | null | undefined,
): number {
  if (!turnId) return -1;
  return messages.findIndex(
    (message) =>
      message.role === "assistant" &&
      message.conversationId === conversationId &&
      message.turnId === turnId,
  );
}

export function applyAccumulatorAt(
  messages: ChatMessage[],
  index: number,
  acc: TurnAccumulator,
  options: ApplyAccumulatorOptions,
): ChatMessage[] {
  const candidate = index >= 0 ? messages[index] : undefined;
  // 只复用「同一会话的助手消息」；否则宁可追加，也不要串写到别的会话里。
  const reusable =
    candidate !== undefined &&
    candidate.role === "assistant" &&
    candidate.conversationId === options.conversationId;

  const base: ChatMessage = reusable
    ? candidate
    : {
        role: "assistant",
        content: "",
        steps: [],
        conversationId: options.conversationId,
        turnId: options.turnId ?? undefined,
      };

  const next: ChatMessage = {
    ...base,
    conversationId: options.conversationId,
    turnId: base.turnId ?? options.turnId ?? undefined,
    content: acc.content,
    agentName: acc.agentName ?? base.agentName,
    steps: JSON.parse(JSON.stringify(acc.steps)) as Step[],
    error: base.error || options.error,
    streaming: !options.finished,
  };

  if (options.finished) {
    const hasEventFiles =
      (acc.changedFiles?.length ?? 0) > 0 || (acc.resultFiles?.length ?? 0) > 0;
    const derived = hasEventFiles ? null : deriveTurnFilesFromSteps(acc.steps);
    next.changedFiles = acc.changedFiles ?? derived?.changed ?? base.changedFiles;
    next.resultFiles = keepResultFiles(acc.resultFiles ?? derived?.results ?? []);
    next.filesDerived = hasEventFiles ? false : Boolean(derived?.changed.length);
    next.filesTruncated = acc.filesTruncated ?? base.filesTruncated;
  }

  if (reusable) {
    return messages.map((message, current) => (current === index ? next : message));
  }
  return [...messages, next];
}
