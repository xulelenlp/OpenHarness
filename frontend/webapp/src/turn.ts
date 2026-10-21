/**
 * 事件 → 步骤 归约：实时流与历史回放共用同一套逻辑。
 *
 * 抽成独立模块（纯函数、不依赖 React）以便自动化验证与复用。
 */

import type { AttachmentInfo, ChangedFile, ResultFile, Step } from "./types";

export interface TurnAccumulator {
  content: string;
  steps: Step[];
  agentName?: string;
  /**
   * 本轮新增/修改的文件与结果文件。
   *
   * 来源是后端 `turn_files` 事件（文件系统 diff，权威）；旧轨迹没有该事件，
   * 由前端从工具输出推导后写入（见 `turnFiles.ts` 的 `deriveTurnFilesFromSteps`）。
   */
  changedFiles?: ChangedFile[];
  resultFiles?: ResultFile[];
  filesDerived?: boolean;
  filesTruncated?: boolean;
  /** true = 收到过 `turn_complete`；历史回放只对已完成的轮次展示文件卡片。 */
  finished?: boolean;
  /**
   * 本轮携带的附件。
   *
   * 由 `attachments_ready` 事件赋值——历史回放时这是**唯一**能把附件还原到
   * 对应轮次的来源（snapshot.messages 里只有文本）。
   */
  attachments?: AttachmentInfo[];
  seq: number;
  /** 正在执行的工具名 → 对应 step.key，用于 tool_progress / tool_completed 回填。 */
  currentTool: Map<string, string>;
  /** 当前思考步骤的 key；正文/工具开始时置 done 并清空。 */
  reasoningKey: string | null;
  /**
   * 已处理的审批：requestId → 结果文案。
   *
   * onEvent 每次都用 `acc.steps` 整体覆盖消息里的 steps，若这里不记录，
   * 用户点过「允许」的那张卡片会被随后的流式事件"复活"回待处理状态，
   * 按钮重新出现、还能被重复点击。
   */
  resolvedApprovals: Map<string, string>;
}

export function makeAccumulator(): TurnAccumulator {
  return {
    content: "",
    steps: [],
    seq: 0,
    currentTool: new Map(),
    reasoningKey: null,
    resolvedApprovals: new Map(),
  };
}

export function reduceEvent(acc: TurnAccumulator, evt: Record<string, unknown>): void {
  const type = evt.type as string;
  const nextKey = () => `s${acc.seq++}`;
  const finishReasoning = () => {
    if (acc.reasoningKey) {
      const rstep = acc.steps.find((s) => s.key === acc.reasoningKey);
      if (rstep && rstep.status === "running") rstep.status = "done";
      acc.reasoningKey = null;
    }
  };

  switch (type) {
    case "agent_routed":
      acc.agentName = (evt.agent as string) ?? acc.agentName;
      break;

    // 服务端在本轮开始、agent_routed 之前发出；实时流与历史回放都走这里。
    case "attachments_ready":
      acc.attachments = (evt.attachments as AttachmentInfo[] | undefined) ?? acc.attachments;
      break;

    // 本轮文件：服务端按文件系统 diff 生成，是「过程文件 / 结果文件」的权威来源。
    // 事件写在轨迹里，刷新页面后的历史回放同样走这里。
    case "turn_files":
      acc.changedFiles = (evt.changed as ChangedFile[] | undefined) ?? acc.changedFiles;
      acc.resultFiles = (evt.results as ResultFile[] | undefined) ?? acc.resultFiles;
      acc.filesTruncated = Boolean(evt.truncated);
      acc.filesDerived = false;
      break;

    case "status": {
      const text = (evt.text as string) ?? "";
      acc.steps.push({
        key: nextKey(),
        kind: "status",
        status: "done",
        title: text || "状态",
        text,
        ts: evt.ts as number | undefined,
      });
      break;
    }

    case "tool_started": {
      finishReasoning();
      const name = (evt.tool_name as string) ?? "tool";
      const key = nextKey();
      acc.currentTool.set(name, key);
      acc.steps.push({
        key,
        kind: "tool",
        status: "running",
        title: name,
        toolName: name,
        toolInput: evt.tool_input,
        ts: evt.ts as number | undefined,
      });
      break;
    }

    case "tool_progress": {
      const name = (evt.tool_name as string) ?? "";
      const key = acc.currentTool.get(name);
      const text = (evt.text as string) ?? "";
      if (key && text) {
        const step = acc.steps.find((s) => s.key === key);
        if (step) step.toolProgress = (step.toolProgress ?? "") + text;
      }
      break;
    }

    case "tool_completed": {
      const name = (evt.tool_name as string) ?? "";
      const key = acc.currentTool.get(name);
      if (key) {
        const step = acc.steps.find((s) => s.key === key);
        if (step) {
          step.status = evt.is_error ? "error" : "done";
          step.toolOutput = evt.output;
        }
        acc.currentTool.delete(name);
      }
      break;
    }

    case "approval_required": {
      const requestId = evt.request_id as string | undefined;
      // 已处理过的审批（重放/重复事件）直接落成完成态，不再渲染按钮。
      const resolved = requestId ? acc.resolvedApprovals.get(requestId) : undefined;
      acc.steps.push({
        key: nextKey(),
        kind: "approval",
        status: resolved ? "done" : "running",
        title: resolved ?? "需要审批",
        requestId,
        approvalKind: evt.kind as Step["approvalKind"],
        payload: evt.payload as Record<string, unknown> | undefined,
        text: resolved,
        ts: evt.ts as number | undefined,
      });
      break;
    }

    case "error":
      acc.steps.push({
        key: nextKey(),
        kind: "error",
        status: "error",
        title: (evt.text as string) ?? "出错了",
        text: evt.text as string | undefined,
        ts: evt.ts as number | undefined,
      });
      break;

    case "reasoning_delta": {
      const text = (evt.text as string) ?? "";
      if (!text) break;
      if (!acc.reasoningKey) {
        const key = nextKey();
        acc.reasoningKey = key;
        acc.steps.push({
          key,
          kind: "reasoning",
          status: "running",
          title: "思考",
          text,
          ts: evt.ts as number | undefined,
        });
      } else {
        const rstep = acc.steps.find((s) => s.key === acc.reasoningKey);
        if (rstep) rstep.text = (rstep.text ?? "") + text;
      }
      break;
    }

    case "assistant_delta":
      finishReasoning();
      acc.content += (evt.text as string) ?? "";
      break;

    case "assistant_complete":
      acc.content = (evt.text as string) ?? acc.content;
      break;

    case "turn_complete":
      for (const s of acc.steps) if (s.status === "running") s.status = "done";
      acc.finished = true;
      break;
  }
}
