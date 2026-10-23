# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

### Changed

- **`.env` 密码 / Token 编码**：新增 `scripts/env_crypt.py`（`ENC[...]` 编解码，兼容既有 `DB_PASSWORD=ENC[...]` 格式），`qaapp` / `abc` / `abcag` 读取 `.env` 时自动还原；覆盖 `DB_PASSWORD` / `REDIS_PASSWORD` / `*_API_KEY` / `*_API_TOKENS` 等变量，空值跳过，无需密钥文件。
- **配置合并（单文件）**：删除 `config/backend/settings.json`，模型 / 凭据 / 品牌 / 运行参数全部收敛到 `config/backend/.env`；新增 `OPENHARNESS_AUTH_SOURCE`、`OPENHARNESS_API_KEY`、`OPENHARNESS_ALLOW_PROJECT_PLUGINS`、`OPENHARNESS_PROJECT_SKILL_DIRS` 等环境变量支持，并修复 env 覆盖会被 `materialize_active_profile()` 回退的问题。`abc` CLI 与 Web 服务现在共用同一份 `.env`；高级结构化配置（权限 / 沙箱 / MCP / hooks）仍可通过用户级 `~/.openharness/settings.json` 维护。
- **鉴权错误不再终止服务**：单轮执行里的 `SystemExit`（如模型凭据未配置）降级为 SSE `error` 事件 + `turn_complete`；鉴权报错会提示 `auth_source` 的正确写法并对密钥脱敏。

- **全仓结构与配置重构**：配置集中到 `config/`（`backend/{base,dev,prod,local}` + `frontend`），支持 `QAAPP_ENV=dev|prod` 分层合并与 `${VAR}` 占位符；删除 `.openharness/settings.json`、`qaapp/examples/` 与旧扁平配置，新增 `config/README.md`。
- **超大文件拆分**：`src/openharness` / `ohmo` / `qaapp` 下生产 Python 文件全部 ≤800 行；`frontend/webapp` 的 `App.tsx` / `styles.css` 拆为组件、hooks 与分职责样式；新增 800 行体积守卫测试。重点拆分包括 `commands/registry`、`cli`、`autopilot/service`、`services/compact`、`channels/impl/feishu`、`channels/impl/mochat`、`ohmo/gateway/runtime`、`swarm/permission_sync`、`config/settings`、`engine/query`、`coordinator/agent_definitions`、`ui/backend_host`、`swarm/team_lifecycle` 及 qaapp 的 `server/api`、`server/runtime`、`workspace/files`、`tools/report`。
- **清理**：删除无生产引用的旧 Textual TUI / prompt_toolkit 模块、重复手工 E2E 脚本与 `docs/autopilot/` 构建产物；移除 `textual` / `prompt-toolkit` 直接依赖；文档中的绝对路径/内部 IP/明文密钥说明同步清理。

### Added

- **临时账户登录（TEMP-AUTH）**：新增登录页与 `/api/v1/auth/login|logout|me|password`；账户存 `<workspace>/users.json`（scrypt 加盐摘要、`0600`、首次启动生成 `admin` / `viewer` 两个默认账户）；会话为服务端随机 token + HttpOnly/SameSite=Lax（HTTPS 下 Secure），绝对 12h / 空闲 3h；用户名 5 次、同 IP 20 次失败锁定 5 分钟；`viewer` 只读，`/api/*` 默认拒绝并在 Cookie 写请求上校验 Origin。顶栏账户菜单支持改密 / 退出，改密吊销全部会话。正式部署接 OSS/SSO 时按 [`docs/qaapp/AUTH.zh-CN.md`](docs/qaapp/AUTH.zh-CN.md) 删除临时模块。
- **qaapp 前端视觉重构（国网绿 + Codex 风格）**：`frontend/webapp` 重做设计 token 与布局——国网绿 `#006656` 主色、浅色中性画布、272px 侧边栏、860px 居中对话列、细边框卡片、浮动输入区、移动端侧边栏/文件抽屉、会话搜索、自动增高输入、流式自动跟随、错误 toast、加载骨架、代码块复制、编辑审批 diff 预览、权限审批参数展开；新增 `icons.tsx` / `EmptyState.tsx` / `clipboard.ts`，主要 chrome 不再使用 emoji。自检 `npm run typecheck` / `check:render` / `check:attachments` / `check:turn-files` / `check:questions` / `build` 全部通过。
- **执行中选项式澄清**：`ask_user_question` 新增 `options`（2–6 个 `label` / `description` / `recommended`，兼容字符串和缺失 label 的弱模型调用）；qaapp 审批载荷下发 `options` / `allow_other`，前端渲染原生 radio +「其他」自由输入 +「确认并继续」，用户确认后工具返回选项 label 或自定义文本，模型继续执行。不新增计划模式或计划页面，复用现有 `ask_user_question` + 审批通道。新增 `approval_resolved` SSE 事件并写入轨迹，刷新后已回答的问题显示为「已确认」；新增 `npm run check:questions`、`tests/test_tools/test_ask_user_question.py` 与验收文档 `docs/qaapp/ASK_USER_OPTIONS.zh-CN.md`。
- **qaapp 交互优化：过程文件 / 结果文件卡片**：新增 `qaapp/workspace/turns.py`（会话目录轮次前后快照与 diff、结果文件筛选）与 SSE 事件 `turn_files`（`changed` + `results`，自动写轨迹）；每轮正文下方展示「已编辑 N 个文件 +X -Y」的过程文件卡片与 `outputs/` 结果文件卡片，结果文件点击卡片或「下载」即可直接下载（Markdown 可预览）。**分区规则**：结果区只收 Word / PDF / PPT / Excel / Markdown 等成果性文档，csv / py / txt / json / 图片只作为过程文件展示；过程区会剔除结果文件，避免同一文件出现两次。**展示时机**：两组卡片在 `turn_complete`（任务全部结束）后一次性出现，进行中不展示，中断/断流时在流结束时补一次。旧轨迹缺少事件时，前端 `src/turnFiles.ts` 从 `write_file` / `edit_file` / `run_script` / `write_report_docx` 输出推导，保证历史会话同样可见；新增 `npm run check:turn-files` 自检与 `tests/test_qaapp/test_turn_files.py`。右侧「会话文件」抽屉同步：任务进行中、抽屉打开时每 5 秒静默同步一次，任务结束/中断时立即刷新一次；只有文件清单（路径/大小/修改时间）变化才重渲染，避免写文件工具密集触发时列表持续闪动。
- **业务库直连（qaapp）**：新增 `db_query` 工具与 `qaapp/data/mysql.py` / `data_catalog.py` / `data_query.py` / `data_quality.py`，按 `docs/qaapp/database/` 的 A1/A2/B1/B2/T/四率逻辑查询客户 MySQL；以 `ai_data_struct` 实际可用性优先、层级表/`_ssx` 回退，处理月末日期、周期/机构/维度回退、无数据诊断、恒定序列/陈旧/异常/空值/单位等质量问题。新增 `docs/qaapp/database/IMPLEMENTATION.zh-CN.md` 记录实测库结构与文档差异。
- **数据源配置集中管理**：新增唯一 `datasources.json`（`${DB_HOST}` 等占位符 + 环境变量/就近 `.env`）与唯一加载器 `qaapp/data/datasources.py`；MySQL/Redis 连接信息不再散落在 `webapp.json` 或代码里。新增 `abcqa datasource show/check`，MySQL 只读/连接池/超时/行数上限，Redis 只读 db0 + 前缀白名单。
- **多智能体灵活编排**：新增 `consult_agents` 工具，可一次并行/顺序调用 1–4 个外部智能体，支持单次超时、502/会话失败重试、部分失败、原文落 `outputs/agents/` 与截断预览；能力清单改为“可组合、不要只选一个”，并按 `AgentEntry.role` 动态生成取数/分析/知识组合矩阵。
- **业务分析场景与 Word 报告**：新增 10 类业务场景模板（`qaapp/agents/scenarios.py`）、`business-analysis` / `database-query` / `multi-agent-orchestration` 技能（统一放仓库根 `skills/`，随 git 维护，`.openharness/settings.json` 的 `project_skill_dirs` 已包含 `skills`），以及 `write_report_docx`（封面/目录域/标题层级/表格/图片/页眉页脚页码/来源与数据质量附录）。通用助手仍是默认 Agent、工具仍为 `*`；四阶段分析只在业务分析任务中按需加载，不改变开放问答/写作/代码等原有能力。
- `pyproject.toml` 新增 `pymysql` / `redis` / `matplotlib` 依赖。

- qaapp 新增**浏览器免手工 token**：前端启动时调用 `GET /api/v1/bootstrap`，服务端读取 `webapp.json` 的 `api_tokens` 并签发 HttpOnly 会话 Cookie（值是进程内随机串，不是 token 本身），后续请求凭 Cookie 鉴权。页面移除 Bearer Token 输入框，token 不再进入 localStorage / JS；`webapp.json:browser_auth` 可关闭并回退到严格 Bearer（`VITE_API_TOKEN` 或反向代理注入）。API 客户端仍支持 `Authorization: Bearer`。
- qaapp 新增**动态能力清单**（`qaapp/agents/capabilities.py`）：每轮根据 `agents.json` 生成「可调度能力 + 决策顺序」注入 harness 系统提示，新增 Agent 不需要再改通用助手的 `system_prompt`；清单只列本轮实际可用的工具，`external_agent` / `run_script` 被裁掉时对应能力不会出现。通用助手提示词同步改为「直接回答 / 专业智能体 / `run_script` 执行 / 查证 / 澄清」的自主决策原则。
- qaapp 的 `agents.json` **`tools` / `disallowed_tools` 现在真正生效**（`qaapp/tools/policy.py`）：支持 `*` 与 fnmatch 通配，`disallowed_tools` 优先；被裁掉的工具不会出现在模型工具表。`QueryEngine` 新增 `set_tool_registry()` 供宿主按 Agent 切换能力边界。
- 新增架构与扩展规范文档 [`docs/qaapp/ARCHITECTURE.zh-CN.md`](docs/qaapp/ARCHITECTURE.zh-CN.md)：分层架构、请求生命周期、能力矩阵、外部智能体/harness/工具/传输/路由/配置/API 的扩展契约、自主编排决策链、安全边界与 DoD 清单。
- 外部 Agent 工具描述改为**由配置驱动**：移除写死的 `power-grid-analysis` / `smart-data-query` 判例，路由规则只按 `description` 的领域关键词匹配；新增智能体无需改插件代码。
- qaapp 新增统一的**工作区清理模块** `qaapp/workspace/maintenance.py` 与 `abcqa cleanup`：一次扫描会话目录、对应的 `session-*.json` / `latest-<hash>.json` / `traces/*.jsonl` 以及会话目录已删除的孤儿 trace；**默认 dry-run**，`--apply` 才删除，`--older-than-days` / `--keep-latest` 可覆盖配置默认值。删除前做路径白名单与符号链接校验；只处理 qaapp 的 12 位会话 ID，不误删 ohmo 个人助手会话。`conversation.max_active_bundles` / `idle_timeout_seconds` 从“预留字段”变为实际保留策略。
- Hooks now support a `priority` field (default `0`). Within an event, hooks run highest-priority first, and hooks sharing a priority keep their registration order. This lets users order, for example, a security-check hook ahead of a logging hook regardless of where each is declared in settings or contributed by plugins.
- `edit_file` and `write_file` in the React TUI now preview a unified diff before applying file changes, let users approve once or for the rest of the session, and skip the extra prompt automatically in `full_auto` mode.
- qaapp 支持**对话框上传附件**：`POST /api/v1/conversations/{id}/attachments`（multipart，单次最多 2 个），服务端立即解析 `.docx` / `.xlsx` / `.xls` / `.txt` / `.md` / `.csv` / `.json` / `.log` / `.yaml` / `.xml` 并把内容注入模型上下文；`POST /messages` 通过 `attachment_ids` 引用附件，新增 SSE 事件 `attachments_ready`（写入轨迹，刷新页面后仍能还原附件芯片）。依赖新增 `python-docx` / `openpyxl` / `xlrd` / `pandas` / `python-multipart`。
- qaapp 支持**每会话独立临时目录**：`~/.ohmo/conversations/<conversation_id>/{uploads,extracted,scripts,tmp,outputs}`（`conversation_workspace.root` 可覆盖），配套 `GET /workspace`（列出文件）与 `GET /files/{相对路径}`（下载，越界 404）；目录内 `write_file` / `edit_file` / `notebook_edit` 免审批（敏感路径与硬拒绝仍优先，可用 `conversation_workspace.auto_allow_write_tools` 关闭）。
- qaapp 新增内置工具 **`run_script`**：把模型生成的脚本按会话写入 `scripts/`，用共享 conda 环境（自带 pandas/openpyxl/python-docx）的 `python` 或 `node` 执行，工作目录为会话目录根，产物约定写到 `outputs/`；默认走危险操作门禁审批，可用 `{"tool": "run_script", "action": "allow"}` 放开。参考前端同步补齐上传按钮 / 拖拽 / 附件芯片 / 会话文件抽屉与 `npm run check:attachments` 自检。
- 所有带 `{conversation_id}` 的 HTTP/WebSocket 路由新增 ID 格式白名单校验（`^[0-9a-f]{12}$`），修掉「用 `../` 构造会话 ID 造成快照 / 会话目录路径穿越」的隐患。
- qaapp 配置改为**可见路径优先**：按 `--config-dir` / `QAAPP_CONFIG_DIR` → **`<cwd>/config/<name>`（项目内、可提交 git）** → `<ohmo workspace>/<name>` 解析三份配置，并在启动日志与 `abcqa config`（新增 `source` 字段）中显示实际生效文件。修掉「配置放在隐藏目录 `~/.ohmo/`、服务器 `git pull` 更新不到、导致 external Agent 报 `Unknown external agent ... (none configured)`」的部署缺陷；仓库新增可见的 `config/agents.json`，`abcqa init --config-dir ./config` 可生成示例。另新增启动自检：`kind=external` 只写 `external_ref` 且对应配置缺失时直接打 ERROR 日志。
- qaapp 统一外部 Agent 的会话 ID 映射为「一个 qaapp 会话 = 一个对端会话」：`external_agent` 无论是由外部 Agent 直接派发，还是被 harness 总控当作工具调用，都会固定注入 `conversation_id`（配置为固定值时用该值）。修掉插件在调用方未传 ID 时**每次生成随机 uuid、导致对端每次新建会话**的问题——这会把对端会话存储的抖动放大成「会话创建失败」。
- qaapp 的 Agent 配置**收敛到唯一维护文件 `agents.json`**：`kind=external` 可用新增的 `external` 字段内联 `type` / `conversation_id` / `timeout_seconds` / `response` / `config`（endpoint、method、headers、request_body），qaapp 在会话启动时把这些配置注入 `external_agent` 工具（内存注入，不生成第二份文件）；同名以 `agents.json` 为准，插件原有的 `external-agents.json` 文件发现继续作为兜底。`external.conversation_id` 支持配固定值以复用对端已有会话（对端报「会话创建失败」时可用）。`qaapp/examples/external-agents.json`（过时占位副本）删除，`plugins/external-agents/external-agents.json` 清空为占位并注明已迁移。

### Changed

- **Word 报告固定为国网公文格式**：`write_report_docx` 现在统一输出 A4、上下 35mm/左右 28mm 页边距；标题宋体二号加粗，作者/日期楷体三号加粗，一级标题黑体三号，二级标题楷体三号加粗，三级标题宋体三号加粗，四级标题与正文仿宋三号，固定行距 29 磅，正文首行缩进两字符。新增 `date` 入参；默认标题/单位/日期与正文同页，不再输出“机构/期间/作者/生成日期”标签；目录改为可选（`include_toc`，默认关闭），标题层级支持 1–4 级。
- **项目结构重构（不改变公开契约）**：`qaapp` 从平铺模块收敛为 `config / data / agents / workspace / tools / server` 六层；`data_query.py` 拆为 `periods / formatting / resolver / fetcher / shaping / query`，`service.py` 拆为 `server/{service,session,runtime,logging}` + `server/api`；`ohmo/workspace` 拆为路径/模板/生命周期子模块。`qaapp/service.py`、`qaapp/config.py` 等旧模块路径已移除，公开 CLI / HTTP / 配置 / 磁盘布局保持不变。
- **重复实现收敛**：会话快照统一到 `openharness.services.session_storage`（ohmo 只保留目录与 `app/session_key` 元数据）；个人记忆复用 `MemoryLocation` 生命周期；CLI 交互提示、飞书群策略、diff、路径解析、YAML frontmatter、进程终止、Git 根目录、版本号各只剩一处实现。
- **测试与依赖治理**：4 个真实 API 测试移入 `tests/e2e/`，删除硬编码 Key 与机器相关绝对路径，默认由 `real_api` marker 排除；新增 `tests/test_architecture.py`（分层守护）、路由契约、文档链接、场景一致性、版本一致性测试。`mcp` 固定为 `<2.0.0`（2.x 移除了 `mcp.server.fastmcp`，会让现有 MCP 测试无法导入）。
- **文档与脚本清理**：拆分 1,158 行的 `qaapp/README.md` 为 `qaapp/README.md` + `docs/qaapp/{DEPLOYMENT,API,ARCHITECTURE,DATABASE}`；删除与 `skills/database-query/` 重复的旧 `docs/database_query/SKILL.md` 与导入不存在模块的 `executor.py`；删除与 CHANGELOG 重复的 `RELEASE_NOTES_v0.1.9.md`；`autopilot-dashboard` 归入 `frontend/`；E2E 脚本共用 `_common.py`。
- **Lint 可复现**：`pyproject.toml` 显式声明 ruff `select = ["E4","E7","E9","F","I"]`，避免 ruff 新版本默认规则导致 CI 结果漂移。

- **web_search 默认后端由 DuckDuckGo 改为 Bing**：DDG 的 HTML 端点在受限网络中不可达且域名常被 DNS 污染（污染答案形如 `31.13.94.49` / `2001::6ca0:accc`），已移除其解析与 `/l/` 跳转归一化逻辑。新增 `web.search_url` 设置与 `OPENHARNESS_WEB_SEARCH_URL` 覆盖（默认 `https://cn.bing.com/search`），解析 Bing 的 `b_algo` 结果块并解开 `/ck/a` 跳转链接。实测同一查询 1.9s 返回 5 条结果。
- qaapp 参考前端移除侧边栏「Bearer Token（可选）」输入框：改为启动时自动 bootstrap 浏览器会话，收到 401 时自动重建一次并重试；新增可选的 `VITE_API_TOKEN` 构建变量作为「关闭 browser_auth」时的兜底。
- qaapp 的 LLM 分类路由标注为**预留能力**：`resolve_agent` 仍接受 `llm_classifier`，但服务端未注入；文档不再声称 `routing.mode=llm` 会生效，生产默认推荐 harness 总控 + 动态能力清单。
- 仓库根目录重新整理：修正了误提交的「English Case Converter」README，改为 OpenHarness 的实际入口/目录/验证说明；删除与项目无关的一次性脚本 `date_converter.py`。手工 E2E 脚本统一收进 `scripts/e2e/`，并新增 `scripts/README.md` / `scripts/e2e/README.md` 明确“自动化测试放 `tests/`、脚本放 `scripts/e2e/`”的约定。
- 复用而非复制：会话 ID 白名单收敛到 `qaapp.workspace.files.CONVERSATION_ID_PATTERN`；ohmo 的 `latest-<hash>.json` 路径改为公开的 `get_session_key_latest_path()`，清理逻辑不再重复哈希算法。
- 合并 project memory 与 ohmo personal memory 的**软删除清理逻辑**：`remove_memory_entry` 的扫描、禁用标记、索引摘除统一走 `openharness.memory.manager.soft_delete_memory_entry()`；`next_memory_path` / `effective_memory_signature` 也收敛到同一模块，`ohmo/memory.py` 只保留存储位置与默认类型/分类的差异，去掉了两份几乎相同的实现。
- **Breaking**: the launch commands are unified and renamed — `oh` → `abc` (coding assistant) and `ohmo` → `abcag` (personal agent). The legacy `oh`, `ohmo`, `openh`, and `openharness` console scripts are removed; `python -m openharness` / `python -m ohmo` module entry points remain available for internal process spawning.

### Fixed

- **前端跨端口访问后端被误判为跨站（403 `cross_site`）**：临时登录启用后，`Origin` 与 `Host` 按 `host:port` 全等比较，导致「页面 5174 / API 8787」的开发态以及 Vite/Nginx 代理改写 `Host` 的部署在创建对话、上传附件时直接 403。现在改为：`Origin` 与 `Host` **主机名一致即视为同源**（端口可以不同），分域名前端可用 `webapp.json: cors_origins` 显式放行（`"*"` 不代表放行），`Sec-Fetch-Site: cross-site` 仍只允许显式放行的来源，避免退化成无防护。
- **Word 报告图文混排（重叠、图注前空行过大）**：`write_report_docx` 之前把图片插在内嵌（inline）段落里，而图片段落沿用正文 29pt 固定行距，Word 会按行高裁切图片、让图片压在文字上；后来按图高预留整行又让图注前多出一整张图高的空行。现在图片改写为 `wp:anchor` + `wp:wrapTopAndBottom`（上下环绕、按栏居中、上下各 6pt），图片段落用单倍行距 + 1pt 段落标记并关闭网格对齐，图注以 6pt 间距、单倍行距紧跟图片，并通过 `keepNext` 保证图与图注同页；竖直定位用 `wp:align=top`，避免部分阅读器把图片排到前一段之前。
- **Markdown 答案行距过大**：`.bubble` 的 `white-space: pre-wrap` 在 Markdown 模式下没有关闭，浏览器把渲染 HTML 里的换行/缩进当成可见文本，导致列表项之间出现大段空白。新增 `.bubble.md { white-space: normal; }`，并把 Markdown 正文收紧为 14px / 1.7 行高、段落 0.5em、列表项 0.14em、标题 0.85em，贴近 Codex 的紧凑排版；历史会话页面复测列表行距从约 57px 降到约 25px。
- **qaapp 审批/问题刷新回放**：新增 `approval_resolved` 事件后，已回答的问题与已处理的权限/编辑审批在刷新页面后显示为「已确认 / 已允许」等完成态，不再重新出现操作按钮；超时也会发 `decision: "timeout"` 的解决事件，等待卡不会一直悬挂。前端 `reduceEvent` 兼容孤立 `approval_resolved` 事件。
- 修掉 `web_search failed: ` / `web_fetch failed: ` **错误报文为空**的问题：`httpcore` 把 `TimeoutError` 包成 `ConnectTimeout(TimeoutError())`、`httpx` 再用 `str(exc)` 转发，导致 `str(exc)` 为空串。新增 `utils/error_report.py`，从异常类型、原因链、目标 host:port、耗时、代理状态重建可读报文（`ConnectTimeout｜原始错误信息为空｜目标 …:443｜耗时 20.3s｜代理 未配置｜原因 ConnectTimeout <- TimeoutError()`），并附可执行提示；`web_fetch` 同步接入。
- 新增 `utils/netlog.py`：每次出网向 `<logs>/web.log` 追加一行（`event`、`endpoint`、`q`、`elapsed`、`status`、`type`、`message`、`args`、`cause`、`dns`、`detail`），失败为 WARNING，服务器上可直接下载定位；`dns=` 字段可辅助识别 DNS 污染。
- `web_search` 在解析不到结果时不再标记为错误，改为返回 `No search results found …`（HTTP 状态与端点一并给出）。
- Codex subscription requests now pass reasoning effort separately, enabling `gpt-5.5` with `xhigh` effort instead of treating `gpt-5.5 xhigh` as an unsupported model name.
- Telegram channel now delivers replies again under `abcag init --no-interactive` and other configs that do not write a `reply_to_message` field. `TelegramConfig` declares `reply_to_message: bool = True` so the attribute access in `TelegramChannel.send` no longer raises `AttributeError` and outbound progress/tool-hint/final messages are sent as expected. See issue #243.

## [0.1.9] - 2026-05-07

### Added

- Added a bundled `skill-creator` skill for creating, improving, and verifying OpenHarness/ohmo skills.
- User-invocable skills can now be triggered directly as slash commands, with support for skill-specific arguments and model override metadata.

### Fixed

- `oh setup` can now update the API key for an already-configured API-key provider profile instead of only changing the model.
- `oh provider edit <profile> --api-key <key>` can now replace a saved profile API key, and `oh provider add ... --api-key <key>` can store one during profile creation.

## [0.1.8] - 2026-05-06

### Added

- Built-in `nvidia` provider profile so `oh setup` offers NVIDIA NIM as a first-class OpenAI-compatible provider choice, with `NVIDIA_API_KEY` auth source, `openai/gpt-oss-120b` as the default model, and the NVIDIA NIM endpoint.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- Plugin tool discovery: plugins can now provide `BaseTool` subclasses in a `<plugin>/tools/` directory and they are auto-discovered, instantiated, and registered in the tool registry at runtime. Add `tools_dir` to `plugin.json` (defaults to `"tools"`).
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Built-in `minimax` provider profile so `oh setup` offers MiniMax as a first-class provider choice, with `MINIMAX_API_KEY` auth source, `MiniMax-M2.7` as the default model, and `MiniMax-M2.7-highspeed` in the model picker.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.

### Fixed

- Subprocess teammate spawn (`agent` tool, `task_create`) now works on Windows under Git Bash. `subprocess_backend.spawn` builds a direct-exec `argv` list and passes it through new `argv=` and `env=` kwargs on `BackgroundTaskManager.create_agent_task` / `create_shell_task`; `_start_process` then runs the executable via `asyncio.create_subprocess_exec(*argv)` with no shell in between. Previously the spawn command was a single string interpreted by `bash -lc`, which on Windows could not reliably exec a Windows-pathed Python interpreter (e.g. `C:\Users\...\python.exe`) — Git Bash's escape parser consumed the backslashes from the embedded env-prefix and, even with proper quoting, bash launched via `asyncio.create_subprocess_exec` returned `command not found` for Windows-pathed binaries that worked perfectly when invoked interactively. Bypassing the shell sidesteps the entire class of cross-platform quoting and path-translation hazard. The legacy shell-evaluated `command=` path is preserved for callers (e.g. `BashTool`) that legitimately want shell semantics. See issue #230.
- Bundled skill loader now uses `yaml.safe_load` for SKILL.md frontmatter, matching the user-skill loader. The shared parser is extracted to `openharness.skills._frontmatter` so bundled and user skills handle YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs the same way.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Fixed `glob` and `grep` tools hanging indefinitely when the `rg` subprocess produced enough stderr output to fill the OS pipe buffer. `stderr` is now redirected to `DEVNULL` so it is discarded rather than blocking the child process.
- Fixed `bash_tool` hanging after a timed-out command when the subprocess stdout stream stayed open. `_read_remaining_output` now applies a 2-second `asyncio.wait_for` timeout so the tool always returns promptly.
- Fixed `session_runner` background task deadlock caused by an unread `stderr=PIPE` stream. The subprocess now uses `stderr=STDOUT` so all output merges into the single readable stdout pipe.
- React TUI prompt input now treats the raw DEL byte (`0x7f`) as backward delete while preserving true forward-delete escape sequences, fixing backspace failures seen in some macOS terminal environments.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.

- Built-in `Explore` and `claude-code-guide` agents no longer hard-code `model="haiku"`, which caused them to fail for users on non-Anthropic providers (OpenAI, Bedrock, custom base URLs, etc.). Both agents now use `model="inherit"` so they run with whatever model the parent session is using. `build_inherited_cli_flags` is also fixed to skip the `--model` flag entirely when the value is `"inherit"`, letting the subprocess correctly inherit the parent model via the `OPENHARNESS_MODEL` environment variable instead of receiving the literal string `"inherit"` as a model name.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- ohmo Feishu group routing now supports managed group creation, gateway-scoped provider/model commands, and stricter group mention handling so group conversations only wake ohmo when explicitly addressed.
- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.

## [0.1.7] - 2026-04-18

### Fixed

- Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids overriding Conda-managed shells while preserving global command discovery.
- React TUI prompt now supports `Shift+Enter` for inserting a newline without submitting the current prompt.
- React TUI busy-state animation is less error-prone on Windows terminals: the extra pseudo-animation line was removed, Windows now uses conservative ASCII spinner frames, and the spinner interval was slightly slowed to reduce flashing.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
