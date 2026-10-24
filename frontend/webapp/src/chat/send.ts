/**
 * 一次「发送消息 + 消费 SSE」的完整编排。
 *
 * 从 `App.tsx` 抽出来（同时满足前端 800 行体积守卫）：
 * - 事件先归约进本轮累加器，再按「会话归属」写回消息，切换会话后不会串写；
 * - 断流 / 刷新不会影响服务端回合（服务端由 run_turn 管生命周期）；
 * - 结束（或断流）时把文件卡片一次性落到消息上。
 */

import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { streamMessage } from "../api";
import { applyAccumulatorAt } from "./accumulator";
import { makeAccumulator, reduceEvent } from "../turn";
import type { TurnAccumulator } from "../turn";
import type { ChatMessage } from "../types";

export interface SendTurnOptions {
  conversationId: string;
  content: string;
  agent?: string;
  attachmentIds: string[];
  /** 助手消息在 messages 里的下标（发送时已插入）。 */
  assistantIndex: number;
  controller: AbortController;
  /** 当前页面是否还停在这个会话（切走后只累积、不写 UI）。 */
  isActive: () => boolean;
  accRef: MutableRefObject<TurnAccumulator | null>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  /** 额外副作用：附件芯片回填、错误提示、控制台观测日志。 */
  onEvent?: (event: Record<string, unknown>) => void;
  /** 收到 turn_complete 时回调（刷新文件列表等）。 */
  onTurnComplete?: (conversationId: string) => void;
}

export interface SendTurnResult {
  turnId?: string;
  finished: boolean;
  error?: string;
}

export async function sendTurn({
  conversationId,
  content,
  agent,
  attachmentIds,
  assistantIndex,
  controller,
  isActive,
  accRef,
  setMessages,
  onEvent,
  onTurnComplete,
}: SendTurnOptions): Promise<SendTurnResult> {
  const acc = makeAccumulator();
  accRef.current = acc;
  let turnId: string | undefined;
  let finished = false;
  let errorMessage: string | undefined;

  const apply = (options: { finished: boolean; error?: boolean }) => {
    if (!isActive()) return;
    setMessages((prev) =>
      applyAccumulatorAt(prev, assistantIndex, acc, {
        conversationId,
        turnId,
        finished: options.finished,
        error: options.error,
      }),
    );
  };

  try {
    await streamMessage(
      conversationId,
      content,
      agent,
      (event) => {
        const type = event.type as string;
        if (!turnId && event.turn_id) turnId = String(event.turn_id);
        reduceEvent(acc, event);
        onEvent?.(event);
        if (type === "error") {
          errorMessage = (event.text as string) ?? "出错了";
        }
        apply({ finished: type === "turn_complete", error: type === "error" });
        if (type === "turn_complete") {
          finished = true;
          onTurnComplete?.(conversationId);
        }
      },
      controller.signal,
      attachmentIds.length > 0 ? attachmentIds : undefined,
    );
  } catch (error) {
    if ((error as Error).name !== "AbortError") {
      errorMessage = (error as Error).message;
      // 与旧行为一致：没有正文时给一个可读的失败气泡。
      acc.content = acc.content || `⚠️ ${errorMessage}`;
      apply({ finished: true, error: true });
      finished = true;
    }
  } finally {
    if (!finished) {
      // 断流 / 中断 / 浏览器切走：流已经结束，补一次文件卡片与收尾状态。
      apply({ finished: true });
    }
  }

  return { turnId, finished, error: errorMessage };
}
