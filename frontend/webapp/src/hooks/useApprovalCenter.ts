/**
 * 跨会话审批中心：轮询服务端快照 + 合并卡片 + 提交审批。
 *
 * 为什么需要它：审批请求过去只随「发起该轮的 SSE 响应」下发，页面刷新 / 切走 /
 * 网络中断后就再也到不了界面，服务端却还在等 300s。这里改用一条与连接无关的
 * 状态通道（`GET /api/v1/approvals`）恢复卡片，并把提交目标锁定为请求所属会话。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import { listApprovals, resolveApproval } from "../api";
import { mergePendingApprovals, pendingCounts, requestOwner } from "../approvals";
import type { ApprovalAction } from "../components/StepCard";
import { approvalResultLabel, approvalStepTitle } from "../questions";
import type { TurnAccumulator } from "../turn";
import type { ChatMessage, PendingApprovalInfo, RunningTurnInfo } from "../types";

/** 快照轮询间隔：足够让卡片“几乎立刻”出现，又不会给服务端压力。 */
export const APPROVAL_POLL_MS = 4000;

interface Options {
  /** 登录后才轮询。 */
  enabled: boolean;
  activeId: string | null;
  /** 消息列表镜像（避免把 messages 放进事件回调依赖里）。 */
  messagesRef: MutableRefObject<ChatMessage[]>;
  /** 当前轮的累加器（用于保留已处理审批、避免卡片复活）。 */
  accRef: MutableRefObject<TurnAccumulator | null>;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  setError: (message: string) => void;
  /** 当前会话是否还有页面自己的实时 SSE 流（有流时不要误判卡片失效）。 */
  live?: boolean;
}

export interface ApprovalCenter {
  approvals: PendingApprovalInfo[];
  running: RunningTurnInfo[];
  /** 每次成功拉到快照 +1，驱动断线跟进。 */
  tick: number;
  /** 会话 id → 待审批数量（侧栏角标）。 */
  counts: Record<string, number>;
  handleApproval: ApprovalAction;
  /** 立即同步一次（提交审批后、异常时）。 */
  refresh: () => void;
}

export function useApprovalCenter({
  enabled,
  activeId,
  messagesRef,
  accRef,
  setMessages,
  setError,
  live = false,
}: Options): ApprovalCenter {
  const [approvals, setApprovals] = useState<PendingApprovalInfo[]>([]);
  const [running, setRunning] = useState<RunningTurnInfo[]>([]);
  const [tick, setTick] = useState(0);
  const inFlight = useRef(false);
  const stoppedRef = useRef(false);
  const pollRef = useRef<() => void>(() => {});

  const poll = useCallback(async () => {
    if (inFlight.current || stoppedRef.current) return;
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    inFlight.current = true;
    try {
      const data = await listApprovals();
      if (stoppedRef.current) return;
      setApprovals(Array.isArray(data.approvals) ? data.approvals : []);
      setRunning(Array.isArray(data.running) ? data.running : []);
      setTick((value) => value + 1);
    } catch {
      // 静默重试：登录过期由 api 层的 401 统一处理，这里不要每次都弹红条。
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    stoppedRef.current = false;
    pollRef.current = () => void poll();
    return () => {
      stoppedRef.current = true;
    };
  }, [poll]);

  useEffect(() => {
    if (!enabled) return;
    stoppedRef.current = false;
    void poll();
    const timer = window.setInterval(() => void poll(), APPROVAL_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      stoppedRef.current = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [enabled, poll]);

  // 退出登录 / 未登录：清空上一个账号的快照，避免侧栏角标残留。
  useEffect(() => {
    if (enabled) return;
    setApprovals([]);
    setRunning([]);
  }, [enabled]);

  // 把服务端仍挂着的待审批并进当前会话的消息（实时流断了也能看到按钮）。
  useEffect(() => {
    if (!activeId) return;
    const resolved = new Set<string>(
      accRef.current ? Array.from(accRef.current.resolvedApprovals.keys()) : [],
    );
    const isRunning =
      live || running.some((item) => item.conversation_id === activeId);
    setMessages((prev) =>
      mergePendingApprovals(prev, approvals, activeId, {
        running: isRunning,
        resolved,
      }),
    );
  }, [accRef, activeId, approvals, live, running, setMessages, tick]);

  const submitting = useRef<Set<string>>(new Set());

  const handleApproval = useCallback<ApprovalAction>(
    (requestId, decision, reply) => {
      if (!activeId) return;
      // 同步判重：即便按钮还没从界面上消失，也拦住连点造成的重复提交。
      if (submitting.current.has(requestId)) return;
      submitting.current.add(requestId);

      // 关键：审批必须提交到「请求所属的会话」，而不是当前打开的会话。
      // 线上事故里就是因为这里用了 activeId，请求被发到另一个会话并 404。
      const owner = requestOwner(messagesRef.current, requestId) ?? activeId;

      const acc = accRef.current;
      const targetStep = acc?.steps.find(
        (step) => step.kind === "approval" && step.requestId === requestId,
      );
      // 断线跟进时卡片可能是从快照补的（还没进累加器），再从消息里兜底取 kind。
      const messageStep = messagesRef.current
        .flatMap((message) => message.steps)
        .find((step) => step.kind === "approval" && step.requestId === requestId);
      const kind = targetStep?.approvalKind ?? messageStep?.approvalKind;
      const label = approvalResultLabel(kind, decision, reply);
      const answer = kind === "question" ? reply?.trim() || undefined : undefined;

      // 同步改累加器里的这个步骤：随后到达的流式事件用 acc.steps 覆盖消息，
      // 只做 setMessages 乐观更新会被覆盖，卡片会“复活”、按钮重新可点。
      if (acc) {
        const step = acc.steps.find(
          (item) => item.kind === "approval" && item.requestId === requestId,
        );
        if (step) {
          step.status = "done";
          step.title = approvalStepTitle(step.approvalKind, step.payload, label);
          step.text = label;
          if (step.approvalKind === "question") step.answer = answer;
        }
        acc.resolvedApprovals.set(requestId, label);
      }

      // 乐观更新：把对应审批步骤置为完成，并把标题改成「结果 · 目标」。
      setMessages((prev) =>
        prev.map((message) =>
          message.role !== "assistant"
            ? message
            : {
                ...message,
                steps: message.steps.map((step) => {
                  if (
                    step.kind !== "approval" ||
                    step.requestId !== requestId ||
                    step.status !== "running"
                  ) {
                    return step;
                  }
                  const stepLabel = approvalResultLabel(step.approvalKind, decision, reply);
                  return {
                    ...step,
                    status: "done" as const,
                    title: approvalStepTitle(step.approvalKind, step.payload, stepLabel),
                    text: stepLabel,
                    answer:
                      step.approvalKind === "question"
                        ? reply?.trim() || undefined
                        : step.answer,
                  };
                }),
              },
        ),
      );

      void (async () => {
        try {
          await resolveApproval(owner, requestId, decision, reply);
        } catch (error) {
          const message = (error as Error).message;
          const expired = /不存在|已过期|not_found/i.test(message);
          setError(
            expired
              ? "该审批已失效或不属于当前会话，正在同步服务端状态…"
              : message,
          );
          if (expired) pollRef.current();
          const accNow = accRef.current;
          if (kind === "question") {
            // 问题提交失败时保留可操作态，让用户能重新选择/输入。
            accNow?.resolvedApprovals.delete(requestId);
            const step = accNow?.steps.find(
              (item) => item.kind === "approval" && item.requestId === requestId,
            );
            if (step) {
              step.status = "running";
              step.title = approvalStepTitle(step.approvalKind, step.payload);
              step.text = undefined;
              step.answer = undefined;
            }
            setMessages((prev) =>
              prev.map((item) =>
                item.role !== "assistant"
                  ? item
                  : {
                      ...item,
                      steps: item.steps.map((step) =>
                        step.kind === "approval" && step.requestId === requestId
                          ? {
                              ...step,
                              status: "running" as const,
                              title: approvalStepTitle(step.approvalKind, step.payload),
                              text: undefined,
                              answer: undefined,
                            }
                          : step,
                      ),
                    },
              ),
            );
          } else {
            // 权限/编辑审批：标成失败态，按钮不再出现，避免被反复点击。
            const failed = `提交失败：${message}`;
            if (accNow) {
              accNow.resolvedApprovals.set(requestId, failed);
              const step = accNow.steps.find(
                (item) => item.kind === "approval" && item.requestId === requestId,
              );
              if (step) {
                step.status = "error";
                step.title = failed;
                step.text = message;
              }
            }
            setMessages((prev) =>
              prev.map((item) =>
                item.role !== "assistant"
                  ? item
                  : {
                      ...item,
                      steps: item.steps.map((step) =>
                        step.kind === "approval" && step.requestId === requestId
                          ? { ...step, status: "error" as const, title: failed, text: message }
                          : step,
                      ),
                    },
              ),
            );
          }
        } finally {
          submitting.current.delete(requestId);
          pollRef.current();
        }
      })();
    },
    [accRef, activeId, messagesRef, setError, setMessages],
  );

  const counts = useMemo(() => pendingCounts(approvals), [approvals]);

  const refresh = useCallback(() => {
    pollRef.current();
  }, []);

  return {
    approvals,
    running,
    tick,
    counts,
    handleApproval,
    refresh,
  };
}
