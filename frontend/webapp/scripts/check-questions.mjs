/**
 * 选项式澄清纯函数自检（仿 check-turn-files.mjs：esbuild 现编译现跑）。
 *
 * 校验：
 * 1. `normalizeQuestionOptions` 的兼容/去重/上限行为；
 * 2. 用户答案取值与审批完成文案；
 * 3. `approval_required` / `approval_resolved` 的实时与历史回放归约；
 * 4. 回答后刷新不会再把问题显示成待处理。
 *
 * 用法：npm run check:questions
 */

import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const ENTRY = `
import { makeAccumulator, reduceEvent } from "./src/turn";
import {
  approvalResultLabel,
  approvalStepTitle,
  isPendingQuestion,
  normalizeQuestionOptions,
  questionAnswer,
} from "./src/questions";

const normalized = normalizeQuestionOptions({
  options: [
    { label: " 按供电单位 ", description: "适合管理汇报", recommended: true },
    { label: "按供电单位" },
    { label: "" },
    { label: "按行业分类", description: "适合结构分析" },
    "按电压等级",
    { label: "方案 D" },
    { label: "方案 E" },
    { label: "方案 F" },
    { label: "方案 G" },
  ],
});

const malformed = normalizeQuestionOptions({ options: [null, 42, { description: "无 label" }] });
const noOptions = normalizeQuestionOptions({});

const acc = makeAccumulator();
reduceEvent(acc, {
  type: "approval_required",
  request_id: "req-1",
  kind: "question",
  payload: {
    question: "报告按什么口径生成？",
    options: [
      { label: "按供电单位", recommended: true },
      { label: "按行业分类" },
    ],
  },
});
const running = acc.steps[0];
const runningSnapshot = {
  pending: isPendingQuestion(running),
  title: running.title,
  status: running.status,
};

reduceEvent(acc, {
  type: "approval_resolved",
  request_id: "req-1",
  kind: "question",
  decision: "approve",
  reply: "按行业分类",
});
const resolved = acc.steps[0];

const replay = makeAccumulator();
reduceEvent(replay, {
  type: "approval_required",
  request_id: "req-2",
  kind: "permission",
  payload: { tool: "bash" },
});
reduceEvent(replay, {
  type: "approval_resolved",
  request_id: "req-2",
  kind: "permission",
  decision: "always",
});
const permission = replay.steps[0];

const orphan = makeAccumulator();
reduceEvent(orphan, {
  type: "approval_resolved",
  request_id: "req-3",
  kind: "question",
  decision: "timeout",
  reply: "",
});
const timeoutStep = orphan.steps[0];

export const result = {
  normalized,
  malformedCount: malformed.length,
  noOptionsCount: noOptions.length,
  running: runningSnapshot,
  resolved: {
    pending: isPendingQuestion(resolved),
    title: resolved.title,
    status: resolved.status,
    text: resolved.text,
    answer: resolved.answer,
  },
  permission: {
    status: permission.status,
    title: permission.title,
    text: permission.text,
  },
  timeout: {
    status: timeoutStep.status,
    title: timeoutStep.title,
    text: timeoutStep.text,
  },
  answer: {
    option: questionAnswer("按行业分类", "ignored"),
    custom: questionAnswer(null, "  按日粒度，最近 7 天  "),
    empty: questionAnswer("", "   "),
  },
  labels: {
    question: approvalResultLabel("question", "approve", "按行业分类"),
    denied: approvalResultLabel("permission", "deny"),
    always: approvalResultLabel("permission", "always"),
    timeout: approvalResultLabel("question", "timeout"),
  },
  titles: {
    question: approvalStepTitle("question", {}),
    questionDone: approvalStepTitle("question", {}, "已确认：按行业分类"),
    permissionDone: approvalStepTitle("permission", { tool: "bash" }, "已总是允许"),
    editDone: approvalStepTitle("edit", { path: "a/b/报告.docx" }, "已允许修改"),
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

const outDir = mkdtempSync(path.join(tmpdir(), "qaapp-question-check-"));
try {
  const outfile = path.join(outDir, "bundle.cjs");
  const bundle = await build({
    stdin: {
      contents: ENTRY,
      resolveDir: process.cwd(),
      loader: "tsx",
      sourcefile: "check-questions.tsx",
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

  console.log("选项式澄清自检：");
  check("选项去空白、去重并保留推荐标记", result.normalized[0].label === "按供电单位" && result.normalized[0].recommended === true);
  check("字符串选项被兼容", result.normalized.some((item) => item.label === "按电压等级"));
  check("最多展示 6 个选项", result.normalized.length === 6, `实际 ${result.normalized.length}`);
  check("空/非法选项被过滤", result.malformedCount === 0);
  check("无 options 时返回空数组", result.noOptionsCount === 0);

  check("问题初始为等待态", result.running.pending === true && result.running.status === "running");
  check("问题初始标题正确", result.running.title === "需要你确认", result.running.title);
  check("回答后不再是等待态", result.resolved.pending === false && result.resolved.status === "done");
  check("回答后标题为已确认", result.resolved.title === "已确认", result.resolved.title);
  check("回答后展示具体答案", result.resolved.text === "已确认：按行业分类", result.resolved.text);
  check("回答后保留 answer 字段", result.resolved.answer === "按行业分类", String(result.resolved.answer));

  check("权限审批历史回放为完成态", result.permission.status === "done");
  check("权限审批标题含工具名", result.permission.title.includes("工具「bash」"), result.permission.title);
  check("权限审批结果文案正确", result.permission.text === "已总是允许", result.permission.text);

  check("孤立 resolved 事件也能生成完成态", result.timeout.status === "done");
  check("超时标题为已超时", result.timeout.title === "已超时", result.timeout.title);
  check("超时文案明确", result.timeout.text === "已超时，未回答", result.timeout.text);

  check("选项答案优先于自定义输入", result.answer.option === "按行业分类");
  check("自定义输入会去空白", result.answer.custom === "按日粒度，最近 7 天", result.answer.custom);
  check("空答案返回空串", result.answer.empty === "");

  check("问题结果文案正确", result.labels.question === "已确认：按行业分类", result.labels.question);
  check("拒绝文案正确", result.labels.denied === "已拒绝");
  check("总是允许文案正确", result.labels.always === "已总是允许");
  check("超时文案正确", result.labels.timeout === "已超时，未回答");

  check("等待问题标题正确", result.titles.question === "需要你确认");
  check("完成问题标题正确", result.titles.questionDone === "已确认");
  check("权限完成标题含工具", result.titles.permissionDone.includes("工具「bash」"));
  check("编辑完成标题含文件名", result.titles.editDone.includes("文件「报告.docx」"), result.titles.editDone);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n自检失败 ${failures.length} 项：${failures.join("、")}`);
  process.exit(1);
}
console.log("\n全部通过。");
