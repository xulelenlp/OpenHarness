# qaapp 参考前端

基于 Vite + React + TypeScript 的参考 Web 前端，展示与 qaapp 对话 API 的完整交互：

- 对话列表 / 新建对话
- SSE 流式问答
- 多 Agent 路由结果展示（消息头上的 Agent 徽标）
- 工具执行过程（中间执行轨迹）折叠展示
- 高危操作审批（允许 / 总是允许 / 拒绝）
- 中断当前回答
- 助手回答默认按 Markdown 渲染（表格 / 代码块 / 列表 / 引用），可看源码并一键复制

## Markdown 渲染

助手回答**默认按 Markdown 渲染**（实现见 `src/markdown.tsx`）。几个刻意的取舍：

| 行为 | 原因 |
| --- | --- |
| 启用 `remark-gfm` + `remark-breaks` | 表格等 GFM 语法可用；源码里的单换行也换行，符合聊天场景（否则相邻两行会被 CommonMark 合成一段） |
| **不启用** `rehype-raw` | 模型与外部 Agent 的产出都属于不可信数据，原始 HTML 一律转义，杜绝注入 |
| 只放行 http(s) / mailto / # 链接 | `javascript:`、`data:` 链接渲染成纯文本 |
| 远程图片不自动加载 | `![](http://…)` 会在打开页面时向任意地址发请求（追踪像素 / 内网探测），改为可点击链接 |
| 整体被 ` ```markdown ` 包住时自动解包 | 模型常把整篇回答塞进一个围栏，否则表格只会显示成代码块；真正的代码围栏（python/bash…）保持原样 |
| 流式期间节流渲染（~150ms） | 每个 SSE 增量都重解析整篇 Markdown 会卡死页面；末次内容一定生效 |

顶栏「Markdown 渲染」是全局默认（记在 localStorage）；每条助手消息悬停时还能单独切换
「源码 / 预览」，并复制 Markdown 原文（非安全上下文自动回退 `execCommand` —— 局域网
`http://<ip>:8787` 用不了 `navigator.clipboard`）。

渲染逻辑带自检脚本（不需要测试框架）：

```bash
npm run check:render   # 解包 / 表格 / 代码块 / node 属性泄漏 / 危险链接 / 远程图片
```

## 开发

```bash
cd frontend/webapp
npm install
npm run dev      # http://localhost:5173，/api 与 /healthz 代理到 127.0.0.1:8787
```

启动后端：

```bash
abcqa init          # 首次写入示例配置
abcqa webapp run    # 127.0.0.1:8787
```

## 生产

```bash
npm run build       # 产物输出到 dist/
```

将 `dist/` 目录路径填入 `~/.ohmo/webapp.json` 的 `static_dir`，
重启 `abcqa webapp run`，即可由同一端口托管前端与 API。
