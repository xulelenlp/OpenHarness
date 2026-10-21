/**
 * 「中间过程文件 / 结果文件」纯函数自检（仿 check-attachments.mjs：
 * esbuild 现编译现跑，无测试框架依赖）。
 *
 * 校验：
 * 1. `turn_files` 事件经 `reduceEvent` 归约到累加器；
 * 2. `pickResultFiles` 文档类优先、无文档时退化为图片、最多 8 个；
 * 3. 旧轨迹兜底能从 write_file / edit_file / run_script / write_report_docx
 *    的输出里推导出会话相对路径、行数与结果文件；
 * 4. 路径归一化会拒绝仓库文件与 `..`；
 * 5. 行数汇总忽略二进制文件（null）。
 *
 * 用法：npm run check:turn-files
 */

import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const ENTRY = `
import { makeAccumulator, reduceEvent } from "./src/turn";
import {
  deriveTurnFilesFromSteps,
  kindLabel,
  kindTag,
  normalizeConversationPath,
  pickResultFiles,
  stripAnsi,
  summarizeLines,
} from "./src/turnFiles";

const changed = [
  { path: "scripts/analyze.py", dir: "scripts", action: "created", size: 100,
    mtime: 1, added: 20, removed: 0, kind: "script" },
  { path: "outputs/report.docx", dir: "outputs", action: "created", size: 200,
    mtime: 2, added: null, removed: null, kind: "word" },
  { path: "outputs/report.md", dir: "outputs", action: "created", size: 50,
    mtime: 3, added: 5, removed: 0, kind: "markdown" },
  { path: "outputs/chart.png", dir: "outputs", action: "created", size: 80,
    mtime: 4, added: null, removed: null, kind: "image" },
];

const acc = makeAccumulator();
reduceEvent(acc, {
  type: "turn_files",
  changed,
  results: [{ path: "outputs/report.docx", name: "report.docx", title: "report",
    kind: "word", size: 200, mtime: 2, download_path: "/x" }],
  truncated: true,
});

const nineDocs = Array.from({ length: 9 }, (_, i) => ({
  path: "outputs/r" + i + ".docx", dir: "outputs", action: "created", size: 1,
  mtime: i, added: 1, removed: 0, kind: "word",
}));

const onlyAssets = [
  { path: "outputs/chart.png", dir: "outputs", action: "created", size: 1,
    mtime: 1, added: null, removed: null, kind: "image" },
  { path: "outputs/data.csv", dir: "outputs", action: "created", size: 1,
    mtime: 2, added: 1, removed: 0, kind: "data" },
];

const markdownOnly = [
  { path: "outputs/plan.md", dir: "outputs", action: "created", size: 10,
    mtime: 1, added: 3, removed: 0, kind: "markdown" },
];

const steps = [
  { key: "s1", kind: "tool", status: "done", title: "write_file",
    toolName: "write_file", ts: 1,
    toolInput: { path: "/Users/x/.ohmo/conversations/abc/outputs/reports/a.md" },
    toolOutput: "Wrote /Users/x/.ohmo/conversations/abc/outputs/reports/a.md  (\\u001b[32m+12\\u001b[0m \\u001b[31m-3\\u001b[0m)" },
  { key: "s2", kind: "tool", status: "done", title: "edit_file",
    toolName: "edit_file", ts: 2,
    toolInput: { path: "scripts/x.py" },
    toolOutput: "Updated scripts/x.py  (+2 -1)" },
  { key: "s3", kind: "tool", status: "done", title: "run_script",
    toolName: "run_script", ts: 3,
    toolInput: { name: "analyze.py" },
    toolOutput: "[run_script] scripts/analyze.py（本次新建）\\n退出码：0\\n本次运行后 outputs/ 下的文件：outputs/report.docx、outputs/chart.png" },
  { key: "s4", kind: "tool", status: "done", title: "write_report_docx",
    toolName: "write_report_docx", ts: 4,
    toolInput: {},
    toolOutput: "已生成 Word 报告：outputs/reports/福建报告.docx\\n- 大小：123456 字节" },
  { key: "s5", kind: "tool", status: "done", title: "write_file",
    toolName: "write_file", ts: 5,
    toolInput: { path: "case_converter.py" }, toolOutput: "Wrote case_converter.py" },
];

const derived = deriveTurnFilesFromSteps(steps);
const derivedPaths = derived.changed.map((f) => f.path);
const report = derived.changed.find((f) => f.path === "outputs/reports/福建报告.docx");

export const result = {
  acc: {
    changed: acc.changedFiles.length,
    results: acc.resultFiles.length,
    derivedFlag: acc.filesDerived,
    truncated: acc.filesTruncated,
  },
  picked: {
    docs: pickResultFiles(changed).map((f) => f.path),
    assets: pickResultFiles(onlyAssets).map((f) => f.path),
    markdown: pickResultFiles(markdownOnly).map((f) => f.path),
    capped: pickResultFiles(nineDocs).length,
  },
  derived: {
    paths: derivedPaths,
    results: derived.results.map((f) => f.path),
    wroteStats: (() => {
      const item = derived.changed.find((f) => f.path === "outputs/reports/a.md");
      return item ? { added: item.added, removed: item.removed } : null;
    })(),
    reportSize: report ? report.size : null,
    repoFileIgnored: !derivedPaths.includes("case_converter.py"),
  },
  normalized: {
    absolute: normalizeConversationPath("/Users/x/.ohmo/conversations/abc/outputs/r.docx"),
    relative: normalizeConversationPath("scripts/a.py"),
    repo: normalizeConversationPath("case_converter.py"),
    traversal: normalizeConversationPath("../outputs/x.docx"),
    windows: normalizeConversationPath("C:\\\\tmp\\\\outputs\\\\r.docx"),
  },
  lines: {
    summary: summarizeLines(changed),
    allBinary: summarizeLines([changed[1], changed[3]]),
  },
  labels: {
    tag: kindTag("word"),
    label: kindLabel("markdown"),
    stripped: stripAnsi("\\u001b[32m+1\\u001b[0m"),
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

const outDir = mkdtempSync(path.join(tmpdir(), "qaapp-turn-files-check-"));
try {
  const outfile = path.join(outDir, "bundle.cjs");
  const bundle = await build({
    stdin: {
      contents: ENTRY,
      resolveDir: process.cwd(),
      loader: "ts",
      sourcefile: "check-turn-files.ts",
    },
    bundle: true,
    platform: "node",
    format: "cjs",
    outfile,
    logLevel: "warning",
  });
  if (!bundle) throw new Error("esbuild 构建失败");

  const { result } = createRequire(import.meta.url)(outfile);

  console.log("轮次文件卡片自检：");
  check("turn_files 事件写入 changed", result.acc.changed === 4, String(result.acc.changed));
  check("turn_files 事件写入 results", result.acc.results === 1, String(result.acc.results));
  check("事件来源不是推导", result.acc.derivedFlag === false);
  check("truncated 透传", result.acc.truncated === true);

  check(
    "有正式文档时只返回正式文档",
    JSON.stringify(result.picked.docs) === JSON.stringify(["outputs/report.docx"]),
    JSON.stringify(result.picked.docs),
  );
  check(
    "无正式文档时返回 Markdown",
    JSON.stringify(result.picked.markdown) === JSON.stringify(["outputs/plan.md"]),
    JSON.stringify(result.picked.markdown),
  );
  check(
    "无文档时退化为资产",
    JSON.stringify(result.picked.assets) === JSON.stringify(["outputs/chart.png", "outputs/data.csv"]),
    JSON.stringify(result.picked.assets),
  );
  check("结果文件最多 8 个", result.picked.capped === 8, String(result.picked.capped));

  check(
    "write_file 绝对路径归一化",
    result.derived.paths.includes("outputs/reports/a.md"),
    JSON.stringify(result.derived.paths),
  );
  check(
    "write_file 行数解析（含 ANSI）",
    result.derived.wroteStats?.added === 12 && result.derived.wroteStats?.removed === 3,
    JSON.stringify(result.derived.wroteStats),
  );
  check("edit_file 相对路径", result.derived.paths.includes("scripts/x.py"));
  check("run_script 脚本与产物", result.derived.paths.includes("scripts/analyze.py") &&
    result.derived.paths.includes("outputs/report.docx") &&
    result.derived.paths.includes("outputs/chart.png"), JSON.stringify(result.derived.paths));
  check("write_report_docx 大小", result.derived.reportSize === 123456, String(result.derived.reportSize));
  check(
    "推导结果只含正式文档",
    JSON.stringify(result.derived.results) ===
      JSON.stringify([
        "outputs/reports/福建报告.docx",
        "outputs/report.docx",
      ]),
    JSON.stringify(result.derived.results),
  );
  check("仓库文件被忽略", result.derived.repoFileIgnored);
  check("绝对路径归一化", result.normalized.absolute === "outputs/r.docx", String(result.normalized.absolute));
  check("相对路径保留", result.normalized.relative === "scripts/a.py");
  check("仓库路径返回 null", result.normalized.repo === null);
  check(".. 穿越返回 null", result.normalized.traversal === null);
  check("Windows 路径归一化", result.normalized.windows === "outputs/r.docx", String(result.normalized.windows));

  check(
    "行数汇总忽略二进制",
    result.lines.summary.added === 25 &&
      result.lines.summary.removed === 0 &&
      result.lines.summary.known === true,
    JSON.stringify(result.lines.summary),
  );
  check("全二进制时 known=false", result.lines.allBinary.known === false);
  check("类型标签", result.labels.tag === "W" && result.labels.label === "Markdown");
  check("ANSI 去掉", result.labels.stripped === "+1", result.labels.stripped);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n自检失败 ${failures.length} 项：${failures.join("、")}`);
  process.exit(1);
}
console.log("\n全部通过。");
