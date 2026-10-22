# qaapp 架构、能力与扩展规范

> 面向后续开发者的**单一事实来源**：先看本文理解「现在有什么、边界在哪、新增能力怎么接」，
> 再看 [`qaapp/README.md`](../../qaapp/README.md) 的启动/部署细节。
>
> 本文描述的是 qaapp（`abcqa`）这一层；底层模型调用、工具循环、插件、MCP、子智能体
> 仍由 OpenHarness 提供，不在本文重复。

---

## 1. 产品目标与设计原则

### 1.1 最终目标

用户只描述任务，系统自主决定完成方式：

1. **直接回答**：通用知识、写作、代码、推理、简单换算；
2. **调用专业智能体**：业务指标分析、数据查询、政策知识等；
3. **在服务器执行**：用 Python/Node 处理上传文件、做真实计算、生成图表/报告/新文件；
4. **查证外部事实**：搜索、抓取网页；
5. **必要时澄清**：只问一个最小问题，不把配置问题转嫁给用户。

### 1.2 三条设计原则

| 原则 | 含义 | 落地方式 |
| --- | --- | --- |
| **能力保留、风险受控** | 不因为“危险”删掉工具，而是在运行时用策略拦一道 | `DangerousActionGate` + 审批 + 会话目录边界 |
| **配置驱动、代码稳定** | 加一个智能体/端点不应该改 Python | `agents.json` 唯一维护文件；动态能力清单；工具白名单 |
| **可观测、可回放** | 每一步都能定位、刷新后还能还原 | SSE 事件 + `turns/{turn_id}` 轨迹 + 会话快照 |

> **反模式**：把 agent id / 业务端点 / 触发关键词写进通用助手的 `system_prompt`。
> 这类信息一旦复制到代码或提示词里，新增能力就会漏改、配置与行为漂移。

---

## 2. 分层架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 客户端：frontend/webapp（React + Vite）                                   │
│  - 启动时 GET /api/v1/bootstrap → 建立 HttpOnly 浏览器会话（无需手填 token）│
│  - SSE 流式渲染 / 附件上传 / 审批卡片 / 会话文件抽屉 / Markdown           │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ REST / SSE / WebSocket
┌───────────────────────────────▼──────────────────────────────────────────┐
│ 接入层：qaapp/server/api/app.py（Starlette）                                   │
│  - 路由、鉴权、会话池（每 conversation 一个 ConversationSession）          │
│  - 审批 future、SSE 订阅者、附件解析、会话目录                             │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ 每轮：路由 → 选 Agent → 执行
┌───────────────────────────────▼──────────────────────────────────────────┐
│ 编排层                                                                   │
│  qaapp/agents/router.py     显式 / 关键词 / 默认 Agent                     │
│  qaapp/agents/capabilities.py     由 agents.json 生成「可调度能力 + 决策顺序」   │
│  qaapp/tools/policy.py      tools / disallowed_tools 真正生效              │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│ 执行层（OpenHarness RuntimeBundle / QueryEngine）                         │
│  - 模型循环、工具调用、上下文压缩、hooks、plugins、MCP、skills            │
│  - ToolRegistry：read_file/write_file/bash/web_search/run_script/...      │
│  - PermissionChecker：settings + dangerous-actions + 会话目录免确认       │
└───────────────┬───────────────────────────────┬──────────────────────────┘
                │                               │
┌───────────────▼──────────────┐  ┌─────────────▼──────────────────────────┐
│ 专业智能体（外部 HTTP/stdio）│  │ 本机会话工作区                          │
│ plugins/external-agents      │  │ <conversation>/{uploads,extracted,     │
│ - external_agent 工具        │  │   scripts,tmp,outputs}                 │
│ - endpoint/请求体/响应解析    │  │ - 附件解析文本、run_script、产物        │
└──────────────────────────────┘  └────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────────┐
│ 持久化：ohmo session 快照 + trace JSONL + 会话目录（磁盘）                │
│ 进程内：SSE 订阅、待审批、always_approved（重启丢失，见 [部署文档](DEPLOYMENT.zh-CN.md)）      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.1 关键文件职责

| 文件 | 职责 | 扩展时是否常改 |
| --- | --- | --- |
| `qaapp/server/service.py` | HTTP/SSE/WS、会话池、审批、每轮编排 | 少改（新增 API 才改） |
| `qaapp/config/` | 配置路径/模型加载 | 新增配置项时改 |
| `qaapp/data/datasources.py` | 数据源唯一加载器/运行时 | 数据源变化时改 |
| `qaapp/data/catalog.py` / `data/query.py` + `resolver.py` / `fetcher.py` / `shaping.py` | 业务库元数据/路由/查询/质量 | 取数规则变化时改 |
| `qaapp/tools/db_query.py` | `db_query` 工具 | 查询能力变化时改 |
| `qaapp/tools/consult.py` | `consult_agents` 多智能体批量调用 | 编排策略变化时改 |
| `qaapp/tools/report.py` | `write_report_docx` Word 报告（固定国网公文格式） | 报告版式变化时改 |
| `qaapp/agents/scenarios.py` | 10 类业务场景模板 | 新增场景时改 |
| `qaapp/agents/router.py` | 显式/关键词/默认路由 | 新增路由策略时改 |
| `qaapp/agents/capabilities.py` | 动态能力清单与决策顺序 | **一般不用改**（配置自动生成） |
| `qaapp/tools/policy.py` | 按 Agent 裁剪工具 | 一般不用改 |
| `qaapp/agents/external.py` | 把 `agents.json` 内联配置注入工具 | 一般不用改 |
| `qaapp/tools/dangerous.py` | 高危操作策略 + 权限检查器 | 策略变化用 JSON，不改代码 |
| `qaapp/tools/script.py` | `run_script`（会话目录内执行 Python/Node） | 少改 |
| `qaapp/workspace/files.py` | 会话目录布局、附件索引、上下文块 | 少改 |
| `qaapp/workspace/documents.py` | 上传文档解析 | 新增文件类型时改 |
| `config/agents.json` | **所有 Agent 的唯一维护文件** | **新增能力主要改这里** |
| `config/` 或 `~/.ohmo/webapp.json` | 服务、鉴权、附件、会话目录配置 | 部署时改 |
| `frontend/webapp/src/` | 参考前端 | 新增交互时改 |

---

## 3. 一次请求的完整生命周期

以「分析我上传的售电量报表」为例：

1. **前端**启动时 `GET /api/v1/bootstrap`，服务端读取 `webapp.json:api_tokens`，
   签发 HttpOnly 会话 Cookie；用户无需在页面填 token。
2. **上传**：`POST /conversations/{id}/attachments`，服务端落 `uploads/`、解析到
   `extracted/`、写 `attachments.json`，返回 `attachment_id`。
3. **发消息**：`POST /conversations/{id}/messages`，body 带 `attachment_ids`。
4. **路由**：`resolve_agent` 按「显式 → 关键词 → 默认」选 Agent；默认通常是
   `general`（harness）。
5. **执行前**：
   - 用 `build_agent_tool_registry` 按 `tools`/`disallowed_tools` 裁工具；
   - 用 `build_capability_catalog` 生成能力清单（只列本轮可用能力）；
   - 拼系统提示 = Agent 自身提示 + 能力清单 + 会话工作区上下文；
   - 构造权限检查器（危险操作门禁 + 会话目录免确认 + Agent 权限模式）。
6. **模型循环**：模型直接回答，或调用 `external_agent` / `run_script` / 其他工具；
   工具执行前经过权限检查，需要确认时通过 SSE 推 `approval_required`。
7. **事件**：`agent_routed` / `reasoning_delta` / `assistant_delta` / `tool_started` /
   `tool_completed` / `approval_required` / `approval_resolved` / `turn_files` /
   `turn_complete`。
8. **持久化**：会话快照 + 轨迹 JSONL；刷新页面后按轨迹还原附件、步骤、审批结果与
   过程/结果文件卡片（`approval_resolved` 保证已回答的问题不再显示为待处理）。

---

## 4. 当前能力矩阵

| 能力 | 状态 | 入口/配置 | 说明 |
| --- | --- | --- | --- |
| 通用问答（harness） | ✅ | `agents.json` `kind=harness` | 完整工具能力，默认总控 |
| 外部智能体（HTTP/stdio） | ✅ | `kind=external` + `external` 内联 | 模型经 `external_agent` 调用，也可前端显式指定 |
| 子智能体 | ✅ | `kind=subagent` + `subagent_type` | 复用 OpenHarness AgentDefinition |
| 显式路由 | ✅ | 请求 `agent` 字段 | 前端下拉选 Agent |
| 关键词路由 | ✅ | `routing.rules[]` | priority 降序、子串匹配 |
| 默认 Agent | ✅ | `default_agent` | 兜底 |
| LLM 分类路由 | ⚠️ 预留 | `routing.mode=llm` | 路由函数支持注入 classifier，但 qaapp 目前未注入；生产推荐 harness 总控 |
| 自主编排 | ✅ | `general` + `external_agent` | 模型按能力清单自行选智能体 |
| 动态能力清单 | ✅ | 运行时生成 | 新增 Agent 无需改提示词 |
| 选项式澄清 | ✅ | `ask_user_question` | 执行中遇不明确点给 2–6 个选项 + 「其他」，确认后再继续；不新增计划模式 |
| 工具白名单 | ✅ | `tools` / `disallowed_tools` | 支持 `*` 与 fnmatch |
| 本机 Python 执行 | ✅ | `run_script` | 会话目录 `scripts/`，共享 conda 环境 |
| 上传解析 | ✅ | `attachments` | docx/xlsx/xls/txt/csv/json/... |
| 会话工作区 | ✅ | `conversation_workspace` | uploads/extracted/scripts/tmp/outputs |
| 会话清理 | ✅ | `abcqa cleanup` | dry-run 默认；按 `idle_timeout_seconds` / `max_active_bundles` 清理目录、快照、轨迹 |
| 高危操作门禁 | ✅ | `dangerous-actions.json` | allow/confirm/deny + 命令级覆盖 |
| 审批记忆 | ✅ | 运行时 | 本轮一次、会话「总是允许」、超时拒绝 |
| 浏览器免手工 token | ✅ | `webapp.json:browser_auth` | HttpOnly 会话 Cookie，前端不接触 token |
| Bearer API 鉴权 | ✅ | `webapp.json:api_tokens` | 供 APP / curl / 反向代理使用 |
| SSE / WebSocket | ✅ | `/messages`、`/stream` | 流式回答与中间过程 |
| 历史与轨迹 | ✅ | `GET /conversations/{id}`、`/turns/{id}` | 快照 + JSONL |
| 插件 / MCP / Skills | ✅ | OpenHarness 配置 | 复用底层能力 |
| 多实例横向扩展 | ⚠️ 未支持 | — | 单进程会话池；需共享存储 + 消息总线 |

---

## 5. 配置体系与优先级

三层配置，**可见路径优先**：

| 层级 | 文件 | 解析顺序 | 内容 |
| --- | --- | --- | --- |
| ① OpenHarness 主配置 | `settings.json` | `OPENHARNESS_CONFIG_DIR` → `<cwd>/.openharness/` → `~/.openharness/` | 模型、API Key、权限、沙箱、MCP |
| ② qaapp 配置 | `webapp.json` / `agents.json` / `dangerous-actions.json` / `datasources.json` | `--config-dir` / `QAAPP_CONFIG_DIR` → `<cwd>/config/` → `<workspace>/` | 服务、Agent 注册表、高危策略、数据源 |
| ③ 外部 Agent 访问配置 | **内联在 `agents.json`** | 同 ② | endpoint / 请求体 / 响应解析 |

### 5.1 `agents.json` 是唯一 Agent 维护文件

- `kind=external` 推荐直接用 `external` 内联完整访问配置；
- `external_ref` 仅用于兼容旧的 `external-agents.json`；
- `name` / `description` 是**给模型看的路由依据**，不是注释；
- 新增 Agent 后重启服务即可（配置在启动时加载）。

### 5.2 `description` 的写法（决定路由质量）

推荐模板：

```text
【做什么】售电量、线损、供电量等电网经营指标的分析与报告生成。
【什么时候用】用户问趋势、同比环比、影响因素、异常波动，或要求“分析/评估/研判”时。
【不适用】只想要一个具体数值或明细时，用问数智能体；通用知识直接回答。
```

差的写法：`"电网分析"`（模型不知道触发场景，容易漏调或误调）。

---

## 6. 扩展规范（核心）

> 目标：**新增一种能力，只改配置或只加一个边界清晰的模块；不改通用提示词，不改主流程。**

### 6.1 新增一个外部智能体（最常见，纯配置）

在 `config/agents.json` 的 `agents[]` 追加：

```jsonc
{
  "id": "my-agent",                    // 全局唯一，模型用它当 agent 参数
  "kind": "external",
  "name": "我的专业智能体",
  "description": "【做什么】...【什么时候用】...【不适用】...",
  "external": {
    "type": "http",                    // http | stdio
    "conversation_id": "auto",         // auto = 复用 qaapp 会话 ID
    "timeout_seconds": 300,
    "response": {
      "mode": "auto",                  // auto | sse | ndjson | text
      "extract_tags": ["answer"]       // 只保留最终结果标签
    },
    "config": {
      "endpoint": "http://host/path",
      "method": "POST",
      "headers": { "content-type": "application/json" },
      "request_body": {
        "message": "{message}",        // 用户原话（含附件内容时在前面拼接）
        "conversation_id": "{conversation_id}"
      }
    }
  }
}
```

**必做检查**：

1. `id` 不与现有 Agent 重复；
2. `description` 写清触发场景（否则模型不会选它）；
3. `request_body` 占位符只使用 `{message}` / `{conversation_id}`；
4. 流式响应只取 `<answer>` 等最终标签，避免把推理日志推给前端；
5. 用 mock HTTP 服务跑一次 `pytest tests/test_qaapp -q`（参考 `_MockAgentHandler`）；
6. 重启服务，`abcqa agents validate` 通过。

**不需要**：改 `system_prompt`、改 `capabilities.py`、加关键词规则。动态能力清单会自动列出它；
如果希望确定性优先，可再加 `routing.rules`（关键词 → 该 agent）。

### 6.2 新增一个 harness 智能体（需要独立人设/模型/工具边界）

```jsonc
{
  "id": "data-analyst",
  "kind": "harness",
  "name": "数据分析助手",
  "description": "在服务器上做数据清洗、统计与图表生成",
  "system_prompt": "你是数据分析助手。优先用 run_script 执行可复现的 Python 计算……",
  "tools": ["run_script", "read_file", "write_file", "web_search"],
  "disallowed_tools": ["bash"],
  "model": "",                        // 空 = 继承 settings
  "max_turns": 30,
  "permission_mode": "default"        // default / full_auto / plan
}
```

约定：

- `tools` 是**硬边界**：未列出的工具不会出现在模型工具表里；
- `tools: ["*"]` 或省略 = 全部；`tools: []` = 不允许任何工具；
- `disallowed_tools` 优先于 `tools`，支持 `mcp_*` 等通配；
- 人设提示词只写**角色、工作原则、输出格式**，不要写具体 agent id / 端点 / 关键词；
- 具体可调度能力由运行时清单注入。

### 6.3 新增一个工具（代码扩展）

**方式 A：qaapp 会话级工具**（推荐，和会话目录强相关）

1. 新建 `qaapp/xxx_tool.py`，继承 `BaseTool`：
   - `name` / `description` / `input_model`；
   - `execute(arguments, context)` 返回 `ToolResult`；
   - 需要会话目录时从 `context.metadata[CONVERSATION_DIR_KEY]` 取；
   - 明确 `is_read_only()`：只读工具会自动放行，写/执行类走门禁。
2. 在 `QaWebService.ensure_bundle` 里 `bundle.tool_registry.register(...)`；
3. 如果要给模型上下文，把元数据写进 `bundle.engine.tool_metadata`；
4. 在 `dangerous-actions.json` 里为它声明默认策略（allow/confirm/deny）；
5. 加单测：参数校验、成功路径、超时/错误、权限交互；
6. 更新 [部署文档](DEPLOYMENT.zh-CN.md) 的工具名清单。

**方式 B：OpenHarness 插件工具**（跨产品复用）

在 `plugins/<name>/tools/*.py` 放 `BaseTool` 子类，插件加载时自动注册；
适合与 qaapp 无关的通用能力。

**工具设计清单**：

- [ ] 输入模型有 `description`，模型能看懂参数；
- [ ] 输出有边界（字符上限 / 截断标注），不把巨量日志塞回模型；
- [ ] 失败返回 `ToolResult(is_error=True)`，不要把异常抛穿到 SSE；
- [ ] 不把绝对路径 / 宿主机细节泄露给前端（除非确实需要）；
- [ ] `is_read_only` 判断准确，避免误触发审批或误放行；
- [ ] 有超时与取消处理。

### 6.4 新增一种传输协议（插件扩展）

`plugins/external-agents` 的 `AgentTransport` 是注册表模式：

1. 继承 `AgentTransport`，实现 `invoke(...)`；
2. 在 `TRANSPORT_REGISTRY` 注册新的 `type`；
3. `agents.json` 里 `external.type` 使用新值；
4. 补 mock 传输测试；
5. 不需要改 qaapp 或工具描述。

### 6.5 新增路由策略

当前 `resolve_agent` 支持：

```text
显式指定 → 关键词规则 → （可选）LLM 分类 → 默认 Agent
```

如果要加新策略（例如按用户/租户、按附件类型）：

1. 在 `qaapp/agents/router.py` 增加纯函数/分支，保持「显式优先」；
2. 在 `RoutingConfig` 增加配置字段（带默认值，保证旧配置兼容）；
3. 加单测覆盖：命中、未命中、目标停用、无默认 Agent；
4. 在 README 路由顺序里补充；
5. **不要**把业务关键词硬编码进 Python。

> LLM 分类目前是**预留能力**：`resolve_agent` 接受 `llm_classifier`，但服务端尚未注入。
> 生产推荐「默认 harness + 动态能力清单」，比每次先做一次分类调用更省成本、更可解释。

### 6.6 新增配置项

1. 在 `qaapp/config/` 的对应 `BaseModel` 加字段，**必须有默认值**；
2. 字段加注释（`#:`）说明单位和风险；
3. 在 `validate_*` / `config_status` 里确认非法值能报错；
4. 更新 `qaapp/examples/*.json` 与 README 配置表；
5. 加 `tests/test_qaapp/test_config.py` 用例；
6. 向后兼容：旧配置文件不写新字段时应能正常启动。

### 6.7 新增 API / SSE 事件

1. `qaapp/server/schemas.py` 增加请求/响应模型；
2. `qaapp/server/service.py` 注册路由，注意鉴权与 `conversation_id` 白名单校验；
3. 事件统一走 `_emit()`，自动写轨迹；前端 `turn.ts` / `types.ts` 增加处理；
4. 更新 [API 文档](API.zh-CN.md) 的 API 表与事件列表；
5. 加 TestClient 集成测试（含鉴权、404、非法输入）。

**文件类事件的额外约定**（以 `turn_files` 为例）：

- 只下发**会话相对路径**（`scripts/x.py`、`outputs/reports/x.docx`），不暴露宿主机
  绝对路径；下载仍走 `/files/{path}` 的鉴权与 `resolve_within` 路径校验。
- 行数、大小等派生字段由 `qaapp/workspace/turns.py` 的纯函数计算，单测放
  `tests/test_qaapp/test_turn_files.py`，不要在 `server/runtime.py` 里堆 diff 逻辑（纯函数放 `workspace/turns.py`）。
- 事件必须有上限（文件数、结果数、文本读取预算），否则大目录会把 SSE 帧和轨迹
  JSONL 撑大。
- **结果文件只收成果性文档**：`outputs/` 下的 Word / PDF / PPT / Excel / Markdown；
  csv / py / txt / json / 图片属于过程文件，只在过程区展示（前端用
  `excludeResultFiles` 从 `changed` 中剔除结果文件）。
- **完成后一次性展示**：前端在 `turn_complete` 后才把 `changed/results` 落到消息上，
  进行中不渲染卡片；中断 / 断流时在流结束的 `finally` 里补一次。
- 前端除事件外还要能从工具输出推导（`src/turnFiles.ts`
  `deriveTurnFilesFromSteps`），保证旧轨迹/事件缺失时仍可展示；两条路径的筛选规则
  保持一致，并用 `npm run check:turn-files` 覆盖。

---

## 7. 自主编排：决策链与边界

### 7.1 决策链

```text
用户消息
  │
  ├─ 显式指定 agent？ → 用它（越权/停用则 404）
  ├─ 关键词规则命中？ → 用它（确定性兜底）
  ├─ 默认 Agent（通常是 general harness）
  │     │
  │     ├─ 能力清单：能直接回答 → 直接回答
  │     ├─ 需要专业能力 → external_agent(agent_id, message)
  │     ├─ 需要真实执行 → run_script(code/language)
  │     ├─ 需要外部事实 → web_search / web_fetch
  │     └─ 信息不足 → 一个最小澄清问题
  │
  └─ 无默认 Agent → 明确报错（no_default_agent）
```

### 7.2 为什么用「harness 总控」而不是「先 LLM 分类」

| 方案 | 成本 | 可解释性 | 扩展成本 | 结论 |
| --- | --- | --- | --- | --- |
| 每次先 LLM 分类 | 每轮多一次模型调用 | 分类结果可记录 | 分类提示词要维护 | 不推荐作为默认 |
| harness 总控 + 能力清单 | 一次主调用内决策 | 工具调用轨迹完整 | 改 agents.json 即可 | **推荐** |
| 关键词硬路由 | 最低 | 高 | 每加能力改规则 | 仅作确定性兜底 |

### 7.3 执行边界

- `run_script` 脚本固定写入**本会话目录** `scripts/`，工作目录为会话根，产物写 `outputs/`；
- 默认走危险操作门禁审批；要放开需显式配置
  `{"tool": "run_script", "action": "allow"}`（高风险，仅可信部署）；
- `bash` 与 `run_script` 都属于执行类，**不会**被「会话目录写文件免确认」放行；
- 敏感路径（`.ssh` / `.env` / `*.pem` 等）恒拒，优先于任何 allow。

---

## 8. 鉴权与安全边界

### 8.1 浏览器免手工 token（当前默认）

```text
浏览器 → GET /api/v1/bootstrap
        ← Set-Cookie: qaapp_session=<进程内随机值>; HttpOnly; SameSite=Lax
浏览器 → GET /api/v1/conversations（自动带 Cookie）
        ← 200
```

- 服务端从 `webapp.json:api_tokens` 判断是否需要鉴权；
- Cookie 值**不是 token**，也不进入 localStorage / JS；
- 服务重启后随机值变化，前端收到 401 会自动重建一次会话；
- 关闭方式：`browser_auth.enabled=false`；此时前端用 `VITE_API_TOKEN` 构建，
  或由反向代理 / SSO 注入 `Authorization`。

> ⚠️ **边界**：能访问 `/api/v1/bootstrap` 的客户端都能建立会话。它是「免手工输入」
> 的体验优化，不是访问控制边界。对公网暴露时必须在网关层做认证/限流，或关闭
> `browser_auth` 并使用严格 Bearer。

### 8.2 鉴权矩阵

| 端点 | 未配置 api_tokens | 配置 api_tokens + browser_auth | 配置 api_tokens + 关闭 browser_auth |
| --- | --- | --- | --- |
| `/healthz` | 开放 | 开放 | 开放 |
| `/api/v1/bootstrap` | 开放（返回无需鉴权） | 开放（签发 Cookie） | 开放（不签发） |
| `/api/v1/agents` | 开放 | 开放 | 开放 |
| 其他 API | 开放 | Cookie 或 Bearer | 仅 Bearer |
| WebSocket `/stream` | 开放 | 开放 | 开放 |

生产建议：Nginx/Caddy 做 TLS + 认证 + 限流；`/api/v1/bootstrap`、`/api/v1/agents`、
WebSocket 一并纳入网关管控。

---

## 9. 可观测性与排障

| 层 | 证据 | 用途 |
| --- | --- | --- |
| 后端日志 | `sse emit: type=...` | 后端实际发出了哪些事件 |
| 后端日志 | `openai stream done: ...` | 模型本轮返回了多少 content / tool_calls |
| 浏览器控制台 | `[sse] <type>` | 前端实际收到了哪些事件 |
| 轨迹 | `GET /turns/{turn_id}` | 回放一轮的完整步骤（工具输入/输出/审批） |
| 会话目录 | `GET /workspace`、`GET /files/...` | 看脚本、解析文本、产物 |
| 配置 | `abcqa config` | 四份配置实际读了哪个文件 |
| Agent | `abcqa agents list/validate` | 注册表是否生效 |

常见断点：

- 只有「思考中…」→ 先比后端 `sse emit` 与控制台 `[sse]`，再看反向代理是否缓冲；
- 外部 Agent 报「工具未加载」→ 插件缺失；
- 外部 Agent 报「会话创建失败」→ 对端平台问题，或配置固定 `conversation_id`；
- 新增 Agent 不生效 → 没重启，或 `config/agents.json` 不是实际生效文件。

---

## 10. 扩展 Checklist（Definition of Done）

新增任何一种能力，至少完成：

- [ ] **契约**：`id` 唯一；`description` 写清「做什么 / 何时用 / 不适用」；
- [ ] **边界**：`tools` / `disallowed_tools` 明确；执行类工具声明权限策略；
- [ ] **上下文**：需要文件时使用会话目录相对路径，不依赖宿主机绝对路径；
- [ ] **输出**：有长度上限/截断；错误用 `is_error` 返回，不抛穿；
- [ ] **安全**：不向用户索要 token；不在日志/前端泄露密钥；敏感路径规则仍优先；
- [ ] **可观测**：关键步骤有 SSE 事件 / 轨迹记录；
- [ ] **测试**：配置校验 + 单测 + 至少一个集成路径；
- [ ] **文档**：README 配置表 / API 表 / 工具名清单同步更新；
- [ ] **兼容**：旧配置不写新字段仍能启动；旧 API 调用方不受影响；
- [ ] **回滚**：只改配置即可关闭新能力（无需回滚代码）。

---

## 11. 已知缺口与演进路线

### 11.1 已知缺口

| 缺口 | 影响 | 建议 |
| --- | --- | --- |
| LLM 分类路由未注入 classifier | `routing.mode=llm` 实际不生效 | 保持默认 harness 总控；需要时再实现 |
| 多实例不共享会话/审批 | 只能单进程横向扩展 | 引入 Redis pub/sub + 共享存储 |
| 清理需要显式触发 | 长期运行磁盘增长 | 用 cron 定期执行 `abcqa cleanup --apply`；默认 dry-run，避免误删 |
| `browser_auth` 不是访问控制 | 公网暴露风险 | 网关认证 / SSO / 严格 Bearer |
| 工具策略只支持名称通配 | 不能按参数/路径细化 | 需要时扩展 `tool_policy` |
| 没有任务级评测集 | 编排质量难回归 | 建「问题 → 期望能力/结果」评测集 |

### 11.2 清理职责边界（不要为了“合并”制造上帝模块）

| 清理对象 | 负责模块 | 触发方式 |
| --- | --- | --- |
| qaapp 会话目录 / 快照 / 轨迹 | `qaapp/workspace/maintenance.py` + `abcqa cleanup` | 手动或 cron；默认 dry-run |
| 项目 memory / ohmo personal memory 的软删除 | `openharness.memory.manager.soft_delete_memory_entry()` | `/memory remove`、`abcqa`/`abcag` 命令 |
| swarm team / worktree 生命周期 | `src/openharness/swarm/team_lifecycle.py`、`worktree.py` | 进程退出 / 会话结束自动回收 |
| resolved permission 文件 | `src/openharness/swarm/permission_sync.py` | 定期 / 读取后删除 |
| 插件安装目录 | `src/openharness/plugins/installer.py` | 安装/卸载命令 |

判断标准：**同一种数据的所有者才合并**。qaapp 会话是磁盘保留策略，swarm worktree 是
进程生命周期，memory 是用户内容；它们的删除时机、失败语义、安全边界都不同，强行合并
会把“可审计的清理”变成“一个谁都敢调的 rmtree”。

### 11.3 演进路线（建议顺序）

1. **能力清单再结构化**：给 Agent 增加 `examples` / `not_for` 字段，提升路由准确率；
2. **规划-执行分离**：复杂任务先生成计划（todo），再逐步调用能力，失败可重试；
3. **评测集**：覆盖「直接回答 / 专业智能体 / run_script / 澄清」四类，回归编排策略；
4. **多实例**：会话、审批、订阅外置；
5. **网关鉴权**：SSO / OAuth2 / API Gateway，替换 `browser_auth` 的公网用法；
6. **产物协议**：统一 `outputs/` 的文件类型、预览方式与下载鉴权。

---

## 12. 相关文档

- 启动、配置、部署、排障：[`qaapp/README.md`](../../qaapp/README.md)
- 参考前端：[`frontend/webapp/README.md`](../../frontend/webapp/README.md)
- 外部 Agent 插件：[`plugins/external-agents/README.md`](../../plugins/external-agents/README.md)
- OpenHarness 配置：[`docs/CONFIGURATION.zh-CN.md`](../CONFIGURATION.zh-CN.md)
- 定制/插件：[`docs/CUSTOMIZATION.zh-CN.md`](../CUSTOMIZATION.zh-CN.md)

---

## 13. 业务库直连与多智能体编排（新增）

### 13.1 数据源：唯一入口

MySQL/Redis 连接信息**只**维护在 `datasources.json`（`${DB_HOST}` 等占位符 + 环境变量/就近 `.env`），由 `qaapp/data/datasources.py` 唯一加载，构造一个进程内共享的 `DataRuntime`：

```text
QaWebService
  └─ DataRuntime
       ├─ MySqlClient（只读、连接池、超时、行数上限）
       ├─ RedisClient（只读 db0、前缀白名单）
       ├─ BusinessCatalog（指标/机构/维度/可用性缓存）
       └─ BusinessQueryService（路由/查询/质量诊断）
```

其他模块只接收 `DataRuntime` 或 `BusinessQueryService`，**不读取 `DB_*`/`REDIS_*`，不创建连接**。`abcqa datasource show/check` 用于查看（密码脱敏）与连通性检查。

### 13.2 新增工具

| 工具 | 作用 | 权限 |
| --- | --- | --- |
| `db_query` | 业务库结构化查询/EDA/大纲/新闻/图表/受控 SQL | 只读 |
| `consult_agents` | 1–4 个外部智能体并行/顺序调用，重试、超时、截断、证据落盘 | 只读 |
| `write_report_docx` | Markdown/结构化章节 → Word（国网公文格式；标题/单位/日期、可选目录、表格/图片/页码/来源） | 写（默认确认） |

`db_query` / `consult_agents` 是**增量能力**：不替换 `external_agent`、`run_script` 或任何原有工具；general 的工具仍为 `["*"]`。

### 13.3 通用能力保留

- `general` 仍是默认 Agent，原系统提示词/直接回答/写作/代码/文件/Web/MCP/子智能体能力保持不变；
- 四阶段业务分析放在 `business-analysis` 技能中，仅当任务明确是业务分析/报告时按需加载；
- 项目技能统一放在仓库根 `skills/`（随 git 维护），通过 `.openharness/settings.json:project_skill_dirs` + `build_runtime(extra_skill_dirs=...)` 加载；不再放 `.openharness/skills/`；
- 能力清单第一条仍是“能直接回答就直接回答”，不把业务分析设为默认；
- 新增测试覆盖“写邮件/解释概念”等开放任务不触发 DB/分析流程。

### 13.4 业务分析场景

`qaapp/agents/scenarios.py` 集中定义 10 类场景：经营综合、售电量与市场、电费回收与欠费、供电服务、电网运行、资产投资、新能源政策、区域/企业专题、预测与目标、自定义数据+业务库。每类包含指标关键词、维度、必做分析、外部来源、报告章节；`db_query(outline, scenario=...)` 用它生成数据可得性驱动的大纲。

### 13.5 多智能体编排

- `qaapp/agents/capabilities.py` 生成“组合编排矩阵”，按 `AgentEntry.role`（data/analysis/knowledge）动态引用智能体，不写死 ID；
- 不再暗示“只选一个”；复杂任务要求取数/归因/政策组合；
- `consult_agents` 一次批量调用，`shared_context` 共享已核验数据；数字仍以 `db_query` 为准；
- 外部失败重试一次，仍失败切另一个或 `db_query`，不编造。

### 13.6 失败与质量

- 无数据/缺表/周期不符/恒定序列/陈旧/异常均有诊断；
- `high` 级质量问题必须进入回答/报告；
- 报告使用 `write_report_docx` 生成，`markdown_path` 适合长文分段写；
- 详细取数口径见 [`database/IMPLEMENTATION.zh-CN.md`](database/IMPLEMENTATION.zh-CN.md)。

### 13.7 Word 报告版式（固定）

所有 `write_report_docx` 输出统一按国网公文格式，不随模型/调用参数变化：

- A4；上下页边距 35 mm，左右 28 mm；
- 标题：宋体二号加粗；作者/日期：楷体三号加粗；
- 一级标题：黑体三号；二级标题：楷体三号加粗；三级标题：宋体三号加粗；四级标题：仿宋三号；
- 正文：仿宋三号，首行缩进两字符；
- 行距：固定值 29 磅；段前/段后 0。

标题层级只使用 1–4 级；编号建议按“一、/（二）/3./（4）”书写。默认标题/单位/日期与正文同页；长报告可传 `include_toc=true` 单独生成目录页，页码默认开启。

---

## 14. 仓库目录结构

```text
qaapp/
├── cli.py
├── config/          # 路径、Pydantic 模型、加载/校验
├── data/            # datasources/env、MySQL/Redis、catalog、query、quality、charting
├── agents/          # external、router、capabilities、scenarios
├── workspace/       # files、documents、turns、trace、maintenance
├── tools/           # db_query、report、script、consult、policy、dangerous
├── server/          # service、session、runtime、logging、constants
│   └── api/         # Starlette app 组装与路由
└── examples/        # abcqa init 模板（随 wheel 安装）
```

分层依赖：`openharness`（核心）← `ohmo`（个人助手）← `qaapp`（产品服务）。
核心不得导入 qaapp；qaapp 可导入核心与 ohmo。该约束由 `tests/test_architecture.py` 守护。

## 15. 扩展 Checklist

### 15.1 能力扩展规范

完整的分层架构、能力矩阵、扩展契约与决策链见
[`docs/qaapp/ARCHITECTURE.zh-CN.md`](ARCHITECTURE.zh-CN.md)。
这里只放最常用的「新增能力」检查清单。

### 11.1 新增外部智能体（纯配置）

```text
config/agents.json → agents[] 追加 { id, kind:"external", name, description, external:{...} }
→ abcqa agents validate
→ 重启服务
→ 问一个该领域的问题，确认 agent_routed / tool_started(tool_name=external_agent)
```

必须写清 `description`（做什么 / 何时用 / 不适用）；**不要**改通用助手的
`system_prompt`，运行时能力清单会自动生成。

### 11.2 新增 harness 智能体（独立人设/工具边界）

配置 `kind:"harness"`，用 `tools` / `disallowed_tools` 划边界；提示词只写角色、
工作原则、输出格式。`tools` 现在是硬边界，支持 `*` 与 fnmatch 通配。

### 11.3 新增工具（代码）

1. 继承 `BaseTool`（`qaapp/xxx_tool.py` 或 `plugins/<name>/tools/`）；
2. 在 `ensure_bundle` 注册（qaapp 会话级）或由插件自动发现；
3. 声明 `is_read_only`，在 `dangerous-actions.json` 配默认策略；
4. 输出有上限、错误用 `ToolResult(is_error=True)`；
5. 加单测并更新 §9.8 工具名清单。

### 11.4 扩展完成的判定

- [ ] 旧配置不写新字段仍能启动；
- [ ] `abcqa config` / `agents validate` 通过；
- [ ] 至少一条集成路径（真实消息 → 路由 → 工具/外部 Agent → SSE 事件）；
- [ ] 前端无需改代码即可看到新能力（或已同步更新前端）；
- [ ] 文档的配置表 / API 表 / 工具清单已更新；
- [ ] 只改配置即可关闭新能力，无需回滚代码。

---


