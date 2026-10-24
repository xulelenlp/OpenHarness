/**
 * 断线跟进：页面刷新 / 切走会话后，服务端的回合还在跑。
 *
 * 这里用「轨迹游标」按需增量拉取该轮事件（`GET /turns/{turn_id}?after=N`），
 * 把审批卡片、工具进度和正文持续叠回消息里，直到 `finished`。
 * 有本地实时 SSE 流时（`live=true`）不启用，避免重复应用事件。
 */

import { useEffect, useRef } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { getTurnEvents } from "../api";
import { applyAccumulatorAt, findTurnMessageIndex } from "../chat/accumulator";
import { makeAccumulator, reduceEvent } from "../turn";
import type { TurnAccumulator } from "../turn";
import type { ChatMessage, RunningTurnInfo } from "../types";

interface Attached {
  conversationId: string;
  turnId: string;
  cursor: number;
  acc: TurnAccumulator;
}

interface Options {
  enabled: boolean;
  activeId: string | null;
  activeIdRef: MutableRefObject<string | null>;
  /** 服务端报告「当前会话仍在执行」的那一轮（没有则为 null）。 */
  runningTurn: RunningTurnInfo | null;
  /** 审批快照的 tick：每次轮询后推进一步。 */
  tick: number;
  /** 当前会话有本地实时 SSE 流。 */
  live: boolean;
  accRef: MutableRefObject<TurnAccumulator | null>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setStreaming: (value: boolean) => void;
  onFinished: (conversationId: string) => void;
}

export function useTurnResume({
  enabled,
  activeId,
  activeIdRef,
  runningTurn,
  tick,
  live,
  accRef,
  setMessages,
  setStreaming,
  onFinished,
}: Options): void {
  const attachedRef = useRef<Attached | null>(null);

  useEffect(() => {
    if (!enabled || !activeId) return;
    if (live) {
      // 有实时流：让给 SSE，别重复应用同一批事件。
      attachedRef.current = null;
      return;
    }
    if (attachedRef.current && attachedRef.current.conversationId !== activeId) {
      attachedRef.current = null;
    }

    let cancelled = false;

    const attach = async (turnId: string) => {
      const data = await getTurnEvents(activeId, turnId, 0);
      if (cancelled || activeIdRef.current !== activeId || !data) return;
      if (!data.total) return;
      const acc = makeAccumulator();
      for (const event of data.events) reduceEvent(acc, event);
      attachedRef.current = { conversationId: activeId, turnId, cursor: data.total, acc };
      accRef.current = acc;
      setStreaming(!data.finished);
      setMessages((prev) =>
        applyAccumulatorAt(prev, findTurnMessageIndex(prev, activeId, turnId), acc, {
          conversationId: activeId,
          turnId,
          finished: data.finished,
        }),
      );
      if (data.finished) {
        attachedRef.current = null;
        if (activeIdRef.current === activeId) setStreaming(false);
        onFinished(activeId);
      }
    };

    const pollTail = async (attached: Attached) => {
      const data = await getTurnEvents(activeId, attached.turnId, attached.cursor);
      if (cancelled || activeIdRef.current !== activeId || attachedRef.current !== attached) {
        return;
      }
      if (data.reset) {
        // 轨迹被清理 / 轮次重建：下一次 tick 重新全量 attach。
        attachedRef.current = null;
        return;
      }
      for (const event of data.events) reduceEvent(attached.acc, event);
      attached.cursor = data.total;
      setMessages((prev) =>
        applyAccumulatorAt(
          prev,
          findTurnMessageIndex(prev, activeId, attached.turnId),
          attached.acc,
          {
            conversationId: activeId,
            turnId: attached.turnId,
            finished: data.finished,
          },
        ),
      );
      if (!data.finished) return;
      attachedRef.current = null;
      if (activeIdRef.current === activeId) setStreaming(false);
      onFinished(activeId);
    };

    const runningId =
      runningTurn && runningTurn.conversation_id === activeId
        ? runningTurn.turn_id ?? null
        : null;
    const current = attachedRef.current;

    if (!current) {
      if (runningId) {
        void attach(runningId).catch(() => {});
      } else if (activeIdRef.current === activeId) {
        // 打开会话时按 detail.busy 预置了「执行中」，但回合可能在两次轮询之间
        // 已经结束：这里负责把输入区解锁，别让它一直卡住。
        setStreaming(false);
      }
    } else if (runningId && runningId !== current.turnId) {
      attachedRef.current = null;
      void attach(runningId).catch(() => {});
    } else {
      // runningId 为空 = 服务端报告已结束：补最后一轮（含 turn_complete）再收尾。
      void pollTail(current).catch(() => {});
    }

    return () => {
      cancelled = true;
    };
  }, [
    accRef,
    activeId,
    activeIdRef,
    enabled,
    live,
    onFinished,
    runningTurn?.conversation_id,
    runningTurn?.turn_id,
    setMessages,
    setStreaming,
    tick,
  ]);
}
