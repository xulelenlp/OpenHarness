/**
 * 审批状态同步的纯函数。
 *
 * 背景：审批请求过去只通过发起该轮的 SSE 下发。页面刷新 / 切换会话后连接断了，
 * 卡片就再也到不了界面，只能等 300s 超时；而刷新后历史回放又能看到它，于是
 * 表现为「刷新后才出现审批按钮」。这里把「服务端仍然挂着的待审批」合并进消息，
 * 与具体连接无关，便于实时流、断线跟进和历史回放共用，也便于自检脚本覆盖。
 */

import { approvalStepTitle } from "./questions";
import type {
  ChatMessage,
  PendingApprovalInfo,
  RunningTurnInfo,
  Step,
} from "./types";

/** 待审批卡片的 key：request_id 全局唯一，不会和轨迹步骤的 `sN` 冲突。 */
function approvalStepKey(requestId: string): string {
  return `approval-${requestId}`;
}

/** 找出某个 request_id 属于哪个会话（审批提交必须发往这里）。 */
export function requestOwner(
  messages: ChatMessage[],
  requestId: string,
): string | null {
  for (const message of messages) {
    const found = message.steps.some(
      (step) => step.kind === "approval" && step.requestId === requestId,
    );
    if (found) return message.conversationId ?? null;
  }
  return null;
}

/** 每个会话的待审批数量（侧栏角标）。 */
export function pendingCounts(
  approvals: PendingApprovalInfo[],
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const approval of approvals) {
    if (!approval.conversation_id) continue;
    counts[approval.conversation_id] = (counts[approval.conversation_id] ?? 0) + 1;
  }
  return counts;
}

/** 当前打开的会话是否仍在执行（用于「断线后跟进」与输入区状态）。 */
export function runningTurnOf(
  running: RunningTurnInfo[],
  conversationId: string | null,
): RunningTurnInfo | null {
  if (!conversationId) return null;
  return (
    running.find(
      (item) => item.conversation_id === conversationId && Boolean(item.turn_id),
    ) ?? null
  );
}

function hasApprovalStep(messages: ChatMessage[], requestId: string): boolean {
  return messages.some((message) =>
    message.steps.some(
      (step) => step.kind === "approval" && step.requestId === requestId,
    ),
  );
}

function targetMessageIndex(
  messages: ChatMessage[],
  conversationId: string,
  turnId: string | null | undefined,
): number {
  if (turnId) {
    const matched = messages.findIndex(
      (message) =>
        message.role === "assistant" &&
        message.conversationId === conversationId &&
        message.turnId === turnId,
    );
    if (matched >= 0) return matched;
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.conversationId === conversationId) {
      return index;
    }
  }
  return -1;
}

export interface MergeApprovalsOptions {
  /** 该会话在服务端是否仍在执行（决定「残留卡片」是否判定为已失效）。 */
  running: boolean;
  /** 已经在前端处理过（乐观更新 / 轨迹已解决）的 request_id。 */
  resolved?: ReadonlySet<string>;
}

/**
 * 把服务端的待审批合并进消息列表。
 *
 * - 缺失的卡片补上（`status="running"`，可点击）；
 * - 已有卡片保持不动，避免每条轮询都重渲染；
 * - 服务端既不在执行、也不在待审批里的残留卡片标为「已失效（未处理）」，
 *   避免服务重启后永远留着一个点不动的按钮。
 */
export function mergePendingApprovals(
  messages: ChatMessage[],
  approvals: PendingApprovalInfo[],
  conversationId: string | null,
  options: MergeApprovalsOptions,
): ChatMessage[] {
  if (!conversationId) return messages;
  const resolved = options.resolved ?? new Set<string>();
  const pending = approvals.filter(
    (approval) =>
      approval.conversation_id === conversationId &&
      Boolean(approval.request_id) &&
      !resolved.has(approval.request_id),
  );
  const pendingIds = new Set(pending.map((approval) => approval.request_id));

  let changed = false;
  let next = messages.map((message) => {
    if (message.role !== "assistant") return message;
    let stepsChanged = false;
    const steps = message.steps.map((step) => {
      if (step.kind !== "approval" || step.status !== "running" || !step.requestId) {
        return step;
      }
      if (pendingIds.has(step.requestId) || options.running) return step;
      stepsChanged = true;
      const expired = "已失效（未处理）";
      return {
        ...step,
        status: "done" as const,
        title: approvalStepTitle(step.approvalKind, step.payload, expired),
        text: expired,
      };
    });
    if (!stepsChanged) return message;
    changed = true;
    return { ...message, steps };
  });

  for (const approval of pending) {
    if (hasApprovalStep(next, approval.request_id)) continue;
    const step: Step = {
      key: approvalStepKey(approval.request_id),
      kind: "approval",
      status: "running",
      title: approvalStepTitle(approval.kind, approval.payload),
      requestId: approval.request_id,
      approvalKind: approval.kind,
      payload: approval.payload,
    };
    const index = targetMessageIndex(next, conversationId, approval.turn_id);
    changed = true;
    if (index >= 0) {
      next = next.map((message, current) =>
        current === index ? { ...message, steps: [...message.steps, step] } : message,
      );
    } else {
      next = [
        ...next,
        {
          role: "assistant",
          conversationId,
          turnId: approval.turn_id ?? undefined,
          content: "",
          steps: [step],
        },
      ];
    }
  }

  return changed ? next : messages;
}
