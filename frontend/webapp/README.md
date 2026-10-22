# qaapp 参考前端

基于 Vite + React + TypeScript 的参考 Web 前端，展示与 qaapp 对话 API 的完整交互：

- 对话列表 / 新建对话 / 搜索对话
- SSE 流式问答
- 多 Agent 路由结果展示（消息头上的 Agent 徽标）
- 工具执行过程（中间执行轨迹）折叠展示
- 高危操作审批（允许 / 总是允许 / 拒绝），编辑审批展示 diff
- **选项式澄清**：执行中遇到不明确点，模型给 2–6 个选项；用户选择或「其他」输入后确认，任务才继续
- 中断当前回答
- **附件上传**（Word / Excel / TXT，最多 2 个，选中即解析）与**会话文件面板**
- **过程文件 / 结果文件卡片**：每轮正文下方展示「已编辑 N 个文件 +X -Y」与
  `outputs/` 下的交付物；结果文件点击卡片或「下载」直接下载，文本类可预览
- 助手回答默认按 Markdown 渲染（表格 / 代码块 / 列表 / 引用），可看源码并一键复制；代码块可复制
- **免手工 token**：启动时自动建立浏览器会话（HttpOnly Cookie），页面不再显示 Bearer Token 输入框
- **国网绿 + Codex 风格界面**：浅色中性画布、细边框卡片、居中对话列、浮动输入区；
  移动端侧边栏/文件抽屉、自动增高输入、流式自动跟随、错误 toast、加载骨架

## 视觉规范

| 项 | 约定 |
| --- | --- |
| 主色 | 国网绿 `#006656`（Pantone 3292 C 近似），只用于主按钮、激活态、品牌标识、焦点环、选中选项 |
| 画布 | `#F5F7F6`；卡片/侧边栏 `#FFFFFF`；细边框 `#E4EAE8` |
| 字体 | 系统中文字体栈；路径、turn id、代码用等宽字体 |
| 层级 | 侧边栏 272px；对话列最大 860px；顶栏 64px；卡片 12–16px 圆角 |
| 响应式 | 1100px 以下文件抽屉覆盖；900px 以下侧边栏抽屉化；700/480px 压缩顶栏与选项 |
| 动效 | 150–180ms ease；`prefers-reduced-motion` 下关闭 |

设计 token 集中在 `src/styles.css` 的 `:root`。若客户 VI 有精确国网绿色值，只需替换
`--brand-600` 及其同色系变量。

## 选项式澄清

模型在口径、时间范围、统计维度、输出格式等会改变结果、且无法从上下文推断时，会调用
`ask_user_question` 并传入 `options`（`label` / `description` / `recommended`）。前端：

- 用原生 radio 渲染选项，推荐项只显示「推荐」徽标、不默认选中；
- 始终提供「其他（自行输入）」；
- 用户点「确认并继续」后才回传答案，任务不提前继续；
- 支持数字键 1–N 快速选择、Enter 确认；
- 提交失败时保留可操作态并提示，不会把卡片永久标红；
- 已回答的问题由 `approval_resolved` 轨迹事件还原为「已确认」，刷新后不再弹按钮。

验收案例见 [`docs/qaapp/ASK_USER_OPTIONS.zh-CN.md`](../../docs/qaapp/ASK_USER_OPTIONS.zh-CN.md)。

## 鉴权与会话（无需手填 token）

页面启动时调用一次 `GET /api/v1/bootstrap`：

- 后端读取 `webapp.json:api_tokens`，签发 HttpOnly 会话 Cookie；
- 后续请求由浏览器自动携带 Cookie；token 不进入 localStorage / JS；
- 服务重启后旧 Cookie 失效，前端收到 401 会自动重建一次并重试；
- 只有服务端显式关闭 `browser_auth.enabled` 时，才需要构建时注入
  `VITE_API_TOKEN`（或由反向代理 / SSO 注入 `Authorization`）。

> `VITE_API_TOKEN` 会被打进前端产物，任何访问者都能从 JS 读到；公网部署优先用网关认证，
> 不要依赖它。

## 附件上传与会话文件

输入框工具条的「附件」按钮，或把文件**拖拽**到消息区 / 输入框即可上传（单次最多 2 个；
允许 `.txt/.md/.csv/.docx/.xlsx/.xls/.json/.log/.yaml/.yml/.xml`，旧版 `.doc` 请另存为 `.docx`）。

| 行为 | 说明 |
| --- | --- |
| 选中即上传 | 立即 `POST /attachments`，chip 显示「解析中… / 已解析 N 字 / 解析失败：原因」；没有会话时先自动建会话（附件必须挂在某个会话下） |
| 只发文件不写字 | 合法：只要有一个附件就绪，发送按钮即可用；后端会补一句默认提问 |
| 发送 | body 带 `attachment_ids`；用户气泡下方渲染附件芯片（本地状态） |
| 历史回放 | 附件芯片来自轨迹里的 `attachments_ready` 事件（会话快照里只有文本，这是唯一的还原来源） |
| 右侧「会话文件」抽屉 | 按 `uploads/extracted/scripts/tmp/outputs` 分组列出会话目录文件，支持下载、文本预览（前 2000 字符）、复制会话目录绝对路径；**自动同步刷新**：抽屉打开时写文件类工具完成即静默刷新（600ms 防抖），任务结束 / 中断时也会刷新，无需手动关闭重开 |
| 下载鉴权 | 走 `fetch` + `Blob`（下载接口要 Cookie 或 Bearer，`<a href>` 带不了请求头，token 拼进 URL 又会泄漏） |
| 上传/回答期间 | 禁止再次选择 / 拖拽 / 删除附件，避免附件被挂到错误的轮次 |

纯函数（`src/attachments.ts`：后缀白名单、`formatBytes`、上限筛选、状态文案）带零依赖自检：

```bash
npm run check:attachments
```

## 过程文件 / 结果文件卡片

每轮助手消息按「正文 → 过程文件 → 结果文件」的顺序展示：

| 卡片 | 数据来源 | 交互 |
| --- | --- | --- |
| **已编辑 N 个文件** | `turn_files.changed` 中**排除结果文件**后的部分（服务端对会话目录做轮次前后 diff；旧轨迹由 `src/turnFiles.ts` 从 `write_file` / `edit_file` / `run_script` / `write_report_docx` 输出推导） | 标题显示文件数与 `+X -Y`；默认展开前 5 个，可「全部 N 个文件 / 收起」；文本类可预览，所有文件可下载 |
| **结果文件** | `turn_files.results`：`outputs/` 下本轮新增/修改的**成果性文档**，只接受 Word / PDF / PPT / Excel / Markdown，最多 8 个 | 整卡点击直接下载；Markdown 额外提供「预览」。docx / xlsx / pdf 等不提供在线预览，点击即下载 |

- **分区规则**：csv、py、txt、json、图片等属于中间过程产物，只在过程区展示，不进入
  结果区；结果区只放综合处理后的总结性 / 成果性文件。
- **展示时机**：两组卡片都在 `turn_complete`（任务全部结束）后一次性出现，任务进行中
  不展示，避免文件列表随工具调用反复增减；中断 / 断流时在流结束时补一次。
- 事件写入轨迹：刷新页面后靠 `turn_files` 还原；事件缺失（旧会话）时自动降级为
  工具输出推导，并在卡片下方标注「根据本轮工具轨迹整理」。
- `uploads/` 与 `extracted/` 不计入「已编辑」：前者是用户上传，后者是附件解析产物。
- 下载复用与右侧抽屉相同的鉴权 fetch，不会把 token 拼进 URL。

纯函数自检（`src/turnFiles.ts`：事件归约、结果筛选、路径归一化、工具输出推导）：

```bash
npm run check:turn-files
```

## Markdown 渲染

助手回答**默认按 Markdown 渲染**（实现见 `src/markdown.tsx`）。几个刻意的取舍：

| 行为 | 原因 |
| --- | --- |
| 启用 `remark-gfm` + `remark-breaks` | 表格等 GFM 语法可用；源码里的单换行也换行，符合聊天场景（否则相邻两行会被 CommonMark 合成一段） |
| `.bubble.md` 必须 `white-space: normal` | `.bubble` 为纯文本保留换行使用 `pre-wrap`；Markdown 已由块级元素排版，若继续 `pre-wrap` 会把渲染 HTML 里的缩进/换行当文本，列表行距会被撑得很大 |
| **不启用** `rehype-raw` | 模型与外部 Agent 的产出都属于不可信数据，原始 HTML 一律转义，杜绝注入 |
| 只放行 http(s) / mailto / # 链接 | `javascript:`、`data:` 链接渲染成纯文本 |
| 远程图片不自动加载 | `![](http://…)` 会在打开页面时向任意地址发请求（追踪像素 / 内网探测），改为可点击链接 |
| 整体被 ` ```markdown ` 包住时自动解包 | 模型常把整篇回答塞进一个围栏，否则表格只会显示成代码块；真正的代码围栏（python/bash…）保持原样 |
| 流式期间节流渲染（~150ms） | 每个 SSE 增量都重解析整篇 Markdown 会卡死页面；末次内容一定生效 |

顶栏「Markdown」开关是全局默认（记在 localStorage）；每条助手消息悬停时还能单独切换
「源码 / 预览」，并复制 Markdown 原文（非安全上下文自动回退 `execCommand` —— 局域网
`http://<ip>:8787` 用不了 `navigator.clipboard`）。

渲染逻辑带自检脚本（不需要测试框架）：

```bash
npm run check:render      # 解包 / 表格 / 代码块 / node 属性泄漏 / 危险链接 / 远程图片
npm run check:questions   # 选项规范化 / 已确认回放 / 超时 / 权限审批标题
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
