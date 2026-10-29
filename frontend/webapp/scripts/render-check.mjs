/**
 * 助手回答 Markdown 渲染的自检脚本（无测试框架依赖，用 esbuild 现编译现跑）。
 *
 * 校验四件事：
 * 1. 整体被 ```markdown 围栏包住的回答会被解包，并按 GFM 渲染成标题 / 表格；
 * 2. 真正的代码围栏（python/bash…）保持代码块，不被误解包；
 * 3. react-markdown 注入的 `node` 属性不会泄漏到 DOM（曾出现 node="[object Object]"）；
 * 4. `javascript:` 链接被中和，远程图片不自动加载（只渲染成链接）。
 *
 * 用法：npm run check:render
 */

import { build } from "esbuild";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";

const ENTRY = `
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownView, unwrapFencedDocument } from "./src/markdown";
import { reasoningParagraphs, reasoningPreview } from "./src/reasoning";
import { ReasoningBlock } from "./src/components/ReasoningBlock";

const fenced = [
  "\`\`\`markdown",
  "# 标题一",
  "| 项目 | 值 |",
  "| --- | --- |",
  "| 售电量 | **256.08** |",
  "\`\`\`",
].join("\\n");

const code = "\`\`\`python\\nprint(1 < 2)\\n\`\`\`";
const unsafe = "![t](http://tracker.example/x.png) [点我](javascript:alert(1)) [正常](https://a.b/c)";
const reasoningText = "第一段：先看总体走势。\\n\\n第二段：再看地市分化。\\n\\n第三段：最后筛原因。";

export const result = {
  unwrapped: unwrapFencedDocument(fenced),
  document: renderToStaticMarkup(<MarkdownView content={fenced} />),
  code: renderToStaticMarkup(<MarkdownView content={code} />),
  unsafe: renderToStaticMarkup(<MarkdownView content={unsafe} />),
  plain: renderToStaticMarkup(<MarkdownView content={"| a | b |\\n| --- | --- |\\n| 1 | 2 |"} />),
  reasoningParagraphs: reasoningParagraphs(reasoningText),
  reasoningPreview: reasoningPreview("这是一段很长的思考内容，需要在折叠时截断。", 12),
  reasoning: renderToStaticMarkup(<ReasoningBlock text={reasoningText} running={false} />),
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

const outDir = mkdtempSync(path.join(tmpdir(), "qaapp-render-check-"));
try {
  const outfile = path.join(outDir, "bundle.cjs");
  const bundle = await build({
    stdin: { contents: ENTRY, resolveDir: process.cwd(), loader: "tsx", sourcefile: "render-check.tsx" },
    bundle: true,
    platform: "node",
    // 打成 CJS：react-dom/server 是 CJS 且 require 了 stream 等内置模块，
    // 直接以 ESM 方式打包会在运行时报 "Dynamic require of stream is not supported"。
    format: "cjs",
    jsx: "automatic",
    outfile,
    logLevel: "warning",
  });
  if (!bundle) throw new Error("esbuild 构建失败");

  const { result } = createRequire(import.meta.url)(outfile);

  console.log("Markdown 渲染自检：");
  check("整体围栏被解包", result.unwrapped.startsWith("# 标题一"), result.unwrapped.slice(0, 20));
  check("标题渲染为 h1", result.document.includes("<h1>标题一</h1>"));
  check("表格渲染为 GFM 表格", result.document.includes("<table>") && result.document.includes("<th>项目</th>"));
  check("表格套了横向滚动容器", result.document.includes('class="md-table-wrap"'));
  check("粗体渲染为 strong", result.document.includes("<strong>256.08</strong>"));
  check("代码围栏保持代码块", result.code.includes('<code class="language-python">'));
  check("python 围栏未被解包", !result.code.includes("<h1>"));
  check("无 node 属性泄漏", !result.document.includes("node=") && !result.unsafe.includes("node="));
  check("javascript: 链接被中和", !result.unsafe.includes('href="javascript:'), result.unsafe);
  check("远程图片不自动加载", !result.unsafe.includes("<img"), result.unsafe);
  check("未解包的裸表格也能渲染", result.plain.includes("<table>"));

  console.log("思考展示自检：");
  check("思考按空行分段", result.reasoningParagraphs.length === 3, result.reasoningParagraphs.length);
  check("折叠预览会截断", result.reasoningPreview.endsWith("…"), result.reasoningPreview);
  check("思考正文有独立容器", result.reasoning.includes('class="reasoning-body"'));
  check(
    "思考正文按段落渲染",
    (result.reasoning.match(/<p>/g) || []).length === 3,
    result.reasoning,
  );
  check("思考卡片有提示文案", result.reasoning.includes("不影响最终结论"));
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n自检失败 ${failures.length} 项：${failures.join("、")}`);
  process.exit(1);
}
console.log("\n全部通过。");
