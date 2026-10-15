/**
 * 附件纯函数的自检脚本（仿 scripts/render-check.mjs：esbuild 现编译现跑，无测试框架）。
 *
 * 校验：
 * 1. `isSupportedFile` 的白名单行为（.docx/.xlsx/.txt 通过，.doc/.exe 拒绝）；
 * 2. `pickWithinLimit` 的数量上限与拒绝原因（limit / type）；
 * 3. `formatBytes` 的 B / KB / MB 三档；
 * 4. `parseStatusLabel` 的解析中 / 已解析 / 解析失败三种文案。
 *
 * 用法：npm run check:attachments
 */

import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

/**
 * 被检代码入口。
 *
 * `attachments.ts` 只在类型位置用到 `File`，esbuild 直接擦除，所以这里用普通对象
 * 冒充 File 即可（只需要 name 字段）。
 */
const ENTRY = `
import {
  MAX_FILES,
  SUPPORTED_SUFFIXES,
  formatBytes,
  isPreviewableText,
  isSupportedFile,
  parseStatusLabel,
  pickWithinLimit,
  workspaceLabel,
} from "./src/attachments";

const fake = (name) => ({ name, size: 1 });

const three = pickWithinLimit(0, [fake("a.docx"), fake("b.xlsx"), fake("c.txt")]);
const withExisting = pickWithinLimit(1, [fake("a.docx"), fake("b.xlsx")]);
const badType = pickWithinLimit(0, [fake("x.exe"), fake("y.docx")]);
const both = pickWithinLimit(1, [fake("x.exe"), fake("y.docx"), fake("z.txt")]);

const att = (o) => o;

export const result = {
  MAX_FILES,
  SUPPORTED_SUFFIXES,
  supported: {
    docx: isSupportedFile("报告.docx"),
    xlsx: isSupportedFile("BOOK.XLSX"),
    txt: isSupportedFile("notes.txt"),
    doc: isSupportedFile("旧版.doc"),
    exe: isSupportedFile("run.exe"),
    noSuffix: isSupportedFile("Makefile"),
    pathLike: isSupportedFile("uploads/附件.docx"),
  },
  previewable: {
    txt: isPreviewableText("a.txt"),
    docx: isPreviewableText("a.docx"),
    csv: isPreviewableText("a.csv"),
  },
  picked: {
    threeAccepted: three.accepted.length,
    threeNames: three.accepted.map((f) => f.name),
    threeRejected: three.rejected,
    withExistingAccepted: withExisting.accepted.length,
    withExistingRejected: withExisting.rejected,
    badTypeAccepted: badType.accepted.map((f) => f.name),
    badTypeRejected: badType.rejected,
    bothAccepted: both.accepted.map((f) => f.name),
    bothRejected: both.rejected,
  },
  bytes: {
    b: formatBytes(512),
    kb: formatBytes(1234),
    mb: formatBytes(3617587),
    zero: formatBytes(0),
  },
  labels: {
    pending: parseStatusLabel(att({ parse_status: "pending", text_chars: 0 })),
    ok: parseStatusLabel(att({ parse_status: "ok", text_chars: 128 })),
    failed: parseStatusLabel(att({ parse_status: "failed", parse_error: "文档已加密" })),
    failedNoReason: parseStatusLabel(att({ parse_status: "failed" })),
  },
  workspace: workspaceLabel({ path: "uploads/报告.docx", dir: "uploads", size: 1234, mtime: 1760500000 }),
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

const outDir = mkdtempSync(path.join(tmpdir(), "qaapp-attach-check-"));
try {
  const outfile = path.join(outDir, "bundle.cjs");
  const bundle = await build({
    stdin: { contents: ENTRY, resolveDir: process.cwd(), loader: "ts", sourcefile: "check-attachments.ts" },
    bundle: true,
    platform: "node",
    // 打成 CJS：与 render-check 保持一致，直接用 createRequire 取执行结果。
    format: "cjs",
    outfile,
    logLevel: "warning",
  });
  if (!bundle) throw new Error("esbuild 构建失败");

  const { result } = createRequire(import.meta.url)(outfile);

  console.log("附件工具自检：");

  check("MAX_FILES 为 2", result.MAX_FILES === 2, String(result.MAX_FILES));
  check(
    "后缀白名单含 .docx/.xlsx/.txt",
    [".docx", ".xlsx", ".txt"].every((s) => result.SUPPORTED_SUFFIXES.includes(s)),
    result.SUPPORTED_SUFFIXES.join(","),
  );

  check("isSupportedFile 接受 .docx", result.supported.docx === true);
  check("isSupportedFile 接受大写 .XLSX", result.supported.xlsx === true);
  check("isSupportedFile 接受 .txt", result.supported.txt === true);
  check("isSupportedFile 拒绝 .doc", result.supported.doc === false);
  check("isSupportedFile 拒绝 .exe", result.supported.exe === false);
  check("isSupportedFile 拒绝无后缀文件", result.supported.noSuffix === false);
  check("isSupportedFile 接受带目录的路径", result.supported.pathLike === true);
  check("isPreviewableText 只放行文本类", result.previewable.txt && !result.previewable.docx && result.previewable.csv);

  check(
    "pickWithinLimit(0,[a,b,c]) 只接受 2 个",
    result.picked.threeAccepted === 2,
    `accepted=${result.picked.threeAccepted}`,
  );
  check(
    "第三个文件的 reason 为 limit",
    result.picked.threeRejected.length === 1 &&
      result.picked.threeRejected[0].reason === "limit" &&
      result.picked.threeRejected[0].name === "c.txt",
    JSON.stringify(result.picked.threeRejected),
  );
  check(
    "已有 1 个时再选 2 个只接受 1 个",
    result.picked.withExistingAccepted === 1 && result.picked.withExistingRejected[0].reason === "limit",
    JSON.stringify(result.picked.withExistingRejected),
  );
  check(
    "类型不支持时 reason 为 type",
    result.picked.badTypeAccepted.join(",") === "y.docx" &&
      result.picked.badTypeRejected[0].reason === "type" &&
      result.picked.badTypeRejected[0].name === "x.exe",
    JSON.stringify(result.picked.badTypeRejected),
  );
  check(
    "上限优先于类型判定",
    result.picked.bothAccepted.join(",") === "y.docx" &&
      result.picked.bothRejected.map((r) => r.reason).join(",") === "type,limit",
    JSON.stringify(result.picked.bothRejected),
  );

  check("formatBytes(512) = 512B", result.bytes.b === "512B", result.bytes.b);
  check("formatBytes(1234) = 1.2KB", result.bytes.kb === "1.2KB", result.bytes.kb);
  check("formatBytes(3617587) = 3.45MB", result.bytes.mb === "3.45MB", result.bytes.mb);
  check("formatBytes(0) = 0B", result.bytes.zero === "0B", result.bytes.zero);

  check("pending 文案为「解析中…」", result.labels.pending === "解析中…", result.labels.pending);
  check("ok 文案为「已解析 N 字」", result.labels.ok === "已解析 128 字", result.labels.ok);
  check(
    "failed 文案带原因",
    result.labels.failed === "解析失败：文档已加密",
    result.labels.failed,
  );
  check(
    "failed 无原因时有兜底文案",
    result.labels.failedNoReason === "解析失败：未知原因",
    result.labels.failedNoReason,
  );

  check(
    "workspaceLabel 含目录 / 大小 / 时间",
    result.workspace.startsWith("uploads · 1.2KB · ") && result.workspace.length > "uploads · 1.2KB · ".length,
    result.workspace,
  );
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n自检失败 ${failures.length} 项：${failures.join("、")}`);
  process.exitCode = 1;
} else {
  console.log("\n全部通过。");
}
