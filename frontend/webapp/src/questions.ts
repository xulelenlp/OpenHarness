/**
 * 问题选项 / 审批文案的纯函数。
 *
 * 这些逻辑同时被实时 SSE、历史轨迹回放和自检脚本使用；抽离出来可以保证
 * 「用户刚点完」与「刷新后还原」显示一致。
 */

import type { QuestionOption, Step } from "./types";

/** 一个问题最多展示几个选项（与服务端 schema 的 maxItems 对齐）。 */
export const MAX_QUESTION_OPTIONS = 6;

function cleanText(value: unknown): string {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * 把 `approval_required.payload.options` 归一化成可安全渲染的选项。
 *
 * 兼容：字符串数组、缺失字段、重复 label、超长/空 label。任何异常输入都会被
 * 过滤掉而不是让页面崩溃；过滤后为空则自动退回纯文本提问。
 */
export function normalizeQuestionOptions(
  payload: Record<string, unknown> | undefined,
): QuestionOption[] {
  const raw = payload?.options;
  if (!Array.isArray(raw)) return [];

  const options: QuestionOption[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    let label = "";
    let description = "";
    let recommended = false;
    if (typeof item === "string") {
      label = item;
    } else if (item && typeof item === "object") {
      const record = item as Record<string, unknown>;
      label = cleanText(record.label);
      description = cleanText(record.description);
      recommended = record.recommended === true;
    }
    label = cleanText(label);
    if (!label) continue;
    const key = label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    options.push({
      label,
      ...(description ? { description } : {}),
      ...(recommended ? { recommended: true } : {}),
    });
    if (options.length >= MAX_QUESTION_OPTIONS) break;
  }
  return options;
}

/** 该步骤是否是等待用户回答的问题。 */
export function isPendingQuestion(step: Step): boolean {
  return (
    step.kind === "approval" &&
    step.approvalKind === "question" &&
    step.status === "running"
  );
}

/** 从步骤里取问题正文（模型产出属于不可信数据，只按纯文本渲染）。 */
export function questionText(step: Step): string {
  return cleanText(step.payload?.question) || "请补充信息";
}

/** 用户最终提交的答案：优先选中的选项，其次「其他」输入。 */
export function questionAnswer(
  selectedLabel: string | null | undefined,
  customText: string,
): string {
  const selected = cleanText(selectedLabel);
  if (selected) return selected;
  return cleanText(customText);
}

/** 审批/问题完成后的结果文案（实时更新与历史回放共用）。 */
export function approvalResultLabel(
  kind: Step["approvalKind"] | string | undefined,
  decision: string,
  reply?: string | null,
): string {
  const answer = cleanText(reply);
  if (kind === "question") {
    if (decision === "timeout") return "已超时，未回答";
    if (decision === "deny") return "已拒绝回答";
    return answer ? `已确认：${answer}` : "已确认";
  }
  if (kind === "permission") {
    // 超时是「按拒绝处理」，绝不能再落到默认分支显示成「已允许」——
    // 线上就出现过卡片写「已允许」、下面却跟着「审批超时，已按拒绝处理」。
    if (decision === "timeout") return "已超时（按拒绝处理）";
    if (decision === "deny") return "已拒绝";
    if (decision === "always") return "已总是允许";
    return "已允许";
  }
  if (kind === "edit") {
    if (decision === "timeout") return "已超时（按拒绝处理）";
    if (decision === "deny") return "已拒绝修改";
    if (decision === "always") return "已允许修改（本会话）";
    return "已允许修改";
  }
  return answer || "已处理";
}

function payloadTool(payload: Record<string, unknown> | undefined): string {
  return cleanText(payload?.tool);
}

function payloadFileName(payload: Record<string, unknown> | undefined): string {
  const path = cleanText(payload?.path);
  if (!path) return "";
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

/** 审批步骤的标题；传入 resolvedLabel（已处理）时生成完成态标题。 */
export function approvalStepTitle(
  kind: Step["approvalKind"] | string | undefined,
  payload: Record<string, unknown> | undefined,
  resolvedLabel?: string,
): string {
  if (resolvedLabel) {
    if (kind === "question") {
      if (resolvedLabel === "已超时，未回答") return "已超时";
      if (resolvedLabel === "已拒绝回答") return "已拒绝";
      return "已确认";
    }
    if (kind === "permission") {
      const tool = payloadTool(payload);
      return tool ? `${resolvedLabel} · 工具「${tool}」` : resolvedLabel;
    }    if (kind === "edit") {
      const name = payloadFileName(payload);
      return name ? `${resolvedLabel} · 文件「${name}」` : resolvedLabel;
    }
    return resolvedLabel;
  }
  if (kind === "question") return "需要你确认";
  if (kind === "permission") return "需要授权";
  if (kind === "edit") return "需要确认修改";
  return "需要审批";
}
