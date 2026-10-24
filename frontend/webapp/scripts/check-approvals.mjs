/**
 * 审批状态同步自检（仿 check-questions.mjs：esbuild 现编译现跑）。
 *
 * 校验：
 * 1. 服务端待审批能补成可点击卡片，重复轮询不重复追加；
 * 2. 会话已结束且请求不在待审批里 → 残留卡片标为「已失效（未处理）」；
 * 3. 审批提交目标按 request_id 归属会话（不再用当前 activeId）；
 * 4. 累加器写回只落在同一会话的助手消息上（不会串写别的会话）；
 * 5. 历史回放带 conversationId / turnId 和轨迹游标；
 * 6. 权限审批超时显示「已超时（按拒绝处理）」，不再显示「已允许」。
 *
 * 用法：npm run check:approvals
 */

import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const ENTRY = `
import {
  mergePendingApprovals,
  pendingCounts,
  requestOwner,
  runningTurnOf,
} from "./src/approvals";
import { applyAccumulatorAt, findTurnMessageIndex } from "./src/chat/accumulator";
import { buildHistory } from "./src/chat/history";
import { makeAccumulator, reduceEvent } from "./src/turn";
import { approvalResultLabel, approvalStepTitle } from "./src/questions";

const base = [
  { role: "user", content: "分析", conversationId: "c1", turnId: "t1", steps: [] },
  { role: "assistant", content: "进行中", conversationId: "c1", turnId: "t1", steps: [] },
];
const pending = [
  { conversation_id: "c1", request_id: "r1", kind: "permission",
    payload: { tool: "write_report_docx" }, turn_id: "t1" },
  { conversation_id: "c2", request_id: "r2", kind: "permission",
    payload: { tool: "bash" }, turn_id: "t2" },
];

const merged = mergePendingApprovals(base, pending, "c1", { running: true });
const mergedStep = merged[1].steps[0] ?? {};
const mergedAgain = mergePendingApprovals(merged, pending, "c1", { running: true });
const expired = mergePendingApprovals(merged, [], "c1", { running: false });
const expiredStep = expired[1].steps[0] ?? {};
const keptWhileRunning = mergePendingApprovals(merged, [], "c1", { running: true });

const otherConversation = [
  { role: "user", content: "别的会话", conversationId: "c2", turnId: "t9", steps: [] },
  { role: "assistant", content: "别的会话回答", conversationId: "c2", turnId: "t9", steps: [] },
];

const acc = makeAccumulator();
reduceEvent(acc, {
  type: "approval_required",
  request_id: "r9",
  kind: "permission",
  payload: { tool: "bash" },
});
reduceEvent(acc, {
  type: "approval_resolved",
  request_id: "r9",
  kind: "permission",
  decision: "timeout",
});
const timeoutStep = acc.steps[0] ?? {};

const appended = applyAccumulatorAt(base, 99, acc, {
  conversationId: "c1",
  turnId: "t1",
  finished: false,
});
const replaced = applyAccumulatorAt(base, 1, acc, {
  conversationId: "c1",
  turnId: "t1",
  finished: false,
});
const crossConversation = applyAccumulatorAt(otherConversation, 1, acc, {
  conversationId: "c1",
  turnId: "t1",
  finished: false,
});

const detail = {
  conversation_id: "c1",
  messages: [{ role: "user", content: "分析" }],
  turns: [
    {
      turn_id: "t1",
      events: [
        { type: "agent_routed", agent: "business-analysis" },
        { type: "assistant_complete", text: "结论如下" },
        { type: "turn_complete" },
      ],
    },
  ],
};
const built = buildHistory(detail);
const builtLast = built.lastTurn ?? {};

export const result = {
  merged: {
    added: merged.length,
    step: {
      kind: mergedStep.kind,
      status: mergedStep.status,
      title: mergedStep.title,
      tool: mergedStep.payload?.tool,
    },
    noDuplicate: mergedAgain === merged,
  },
  stale: {
    status: expiredStep.status,
    title: expiredStep.title,
    text: expiredStep.text,
    unchangedWhenRunning: keptWhileRunning === merged,
  },
  ownership: {
    owner: requestOwner(merged, "r1"),
    unknown: requestOwner(merged, "r2"),
    counts: pendingCounts(pending),
    running: runningTurnOf(
      [{ conversation_id: "c1", turn_id: "t1" }, { conversation_id: "c2" }],
      "c1",
    )?.turn_id ?? null,
    runningMissing: runningTurnOf([], "c1"),
  },
  timeout: {
    label: approvalResultLabel("permission", "timeout"),
    title: approvalStepTitle(
      "permission",
      { tool: "write_report_docx" },
      approvalResultLabel("permission", "timeout"),
    ),
    eventTitle: timeoutStep.title,
    eventText: timeoutStep.text,
  },
  apply: {
    appendedLength: appended.length,
    appendedOwner: appended[2]?.conversationId,
    appendedRequest: appended[2]?.steps?.[0]?.requestId,
    replacedLength: replaced.length,
    replacedContent: replaced[1]?.content,
    crossLength: crossConversation.length,
    crossKept: crossConversation[1]?.content,
    crossAppendedOwner: crossConversation[2]?.conversationId,
    index: findTurnMessageIndex(base, "c1", "t1"),
    wrongIndex: findTurnMessageIndex(base, "c2", "t1"),
  },
  history: {
    length: built.messages.length,
    assistantOwner: built.messages[1]?.conversationId,
    assistantTurn: built.messages[1]?.turnId,
    content: built.messages[1]?.content,
    turnId: builtLast.turnId,
    eventCount: builtLast.eventCount,
  },
};
`;

const failures = [];

function check(name, condition, detail = "") {
  if (condition) {
    console.log(`  ✓ ${name}`);
  } else {
    failures.push(name);
    console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

const outDir = mkdtempSync(path.join(tmpdir(), "qaapp-approval-check-"));
try {
  const outfile = path.join(outDir, "bundle.cjs");
  const bundle = await build({
    stdin: {
      contents: ENTRY,
      resolveDir: process.cwd(),
      loader: "tsx",
      sourcefile: "check-approvals.tsx",
    },
    bundle: true,
    platform: "node",
    format: "cjs",
    jsx: "automatic",
    outfile,
    logLevel: "warning",
  });
  if (!bundle) throw new Error("esbuild 构建失败");

  const { result } = createRequire(import.meta.url)(outfile);

  console.log("审批状态同步自检：");

  check("缺失的待审批补成一张卡片", result.merged.added === 2 && result.merged.step.status === "running");
  check("卡片标题按工具名生成", result.merged.step.title === "需要授权", result.merged.step.title);
  check("卡片保留完整载荷（工具名）", result.merged.step.tool === "write_report_docx");
  check("重复轮询不重复追加", result.merged.noDuplicate === true);

  check("会话结束后残留卡片标为已失效", result.stale.status === "done" && result.stale.title.includes("已失效"), result.stale.title);
  check("运行中不误判失效", result.stale.unchangedWhenRunning === true);

  check("提交目标按 request 归属会话", result.ownership.owner === "c1", String(result.ownership.owner));
  check("未知 request 不猜归属", result.ownership.unknown === null);
  check("侧栏角标按会话计数", result.ownership.counts.c1 === 1 && result.ownership.counts.c2 === 1);
  check("运行中回合能定位", result.ownership.running === "t1");
  check("空闲会话没有运行回合", result.ownership.runningMissing === null);

  check("权限审批超时不再显示已允许", result.timeout.label === "已超时（按拒绝处理）", result.timeout.label);
  check("超时标题带工具名", result.timeout.title.includes("工具「write_report_docx」"), result.timeout.title);
  check("轨迹归约后的超时文案一致", result.timeout.eventText === "已超时（按拒绝处理）", result.timeout.eventText);
  check("轨迹归约后的超时标题一致", result.timeout.eventTitle === "已超时（按拒绝处理） · 工具「bash」", result.timeout.eventTitle);

  check("下标无效时追加而不是覆盖", result.apply.appendedLength === 3 && result.apply.appendedOwner === "c1");
  check("追加的消息带上 request", result.apply.appendedRequest === "r9");
  check("下标有效时原地更新", result.apply.replacedLength === 2 && result.apply.replacedContent === "");
  check("跨会话不串写", result.apply.crossLength === 3 && result.apply.crossKept === "别的会话回答");
  check("跨会话时追加到目标会话", result.apply.crossAppendedOwner === "c1");
  check("能按会话 + 回合定位消息", result.apply.index === 1);
  check("会话不匹配时不定位", result.apply.wrongIndex === -1);

  check("历史回放带会话归属", result.history.assistantOwner === "c1");
  check("历史回放带回合 id", result.history.assistantTurn === "t1" && result.history.turnId === "t1");
  check("历史回放保留正文", result.history.content === "结论如下");
  check("历史回放给出轨迹游标", result.history.eventCount === 3, String(result.history.eventCount));
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n自检失败 ${failures.length} 项：${failures.join("、")}`);
  process.exit(1);
}
console.log("\n全部通过。");
