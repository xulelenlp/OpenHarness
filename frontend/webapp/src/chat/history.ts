/** 历史轨迹 → 消息列表（实时流与刷新回放共用同一套事件归约）。 */

import { deriveTurnFilesFromSteps, keepResultFiles } from "../turnFiles";
import type { ChatMessage } from "../types";
import { makeAccumulator, reduceEvent } from "../turn";
import { extractText } from "./format";

export interface ConversationDetailShape {
  conversation_id: string;
  messages?: Array<{ role: string; content: unknown }>;
  turns?: Array<{
    turn_id: string;
    message_id?: string;
    events?: Array<Record<string, unknown>>;
  }>;
}

export function buildHistoryMessages(detail: ConversationDetailShape): ChatMessage[] {
  const userTexts: string[] = [];
  for (const message of detail.messages ?? []) {
    if (message.role === "user") userTexts.push(extractText(message.content));
  }

  const rebuilt: ChatMessage[] = [];
  (detail.turns ?? []).forEach((turn, index) => {
    const acc = makeAccumulator();
    for (const event of turn.events ?? []) reduceEvent(acc, event);
    const userText = userTexts[index] ?? "";
    // 只发附件不写文字是合法的一轮，此时 content 为空但必须仍然渲染气泡与芯片。
    if (userText || acc.attachments?.length) {
      rebuilt.push({
        role: "user",
        content: userText,
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
      agentName: acc.agentName,
      turnId: turn.turn_id,
      steps: acc.steps,
      changedFiles: finished ? acc.changedFiles ?? derived?.changed : undefined,
      resultFiles: finished
        ? keepResultFiles(acc.resultFiles ?? derived?.results ?? [])
        : undefined,
      filesDerived: finished && !hasEventFiles && Boolean(derived?.changed.length),
      filesTruncated: finished ? acc.filesTruncated : undefined,
    });
  });
  return rebuilt;
}
