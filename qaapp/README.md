# qaapp —— 问答产品服务（落地部署文档）

基于 **OpenHarness** 与 **ohmo** 构建的问答产品后端。面向客户 APP / Web 前端，
提供「配置化多 Agent 接入 + 文件可配置的高危操作门禁 + 完整对话 API + 带对话 ID
的历史（含中间执行过程）」。

本文档面向**启动、配置与部署落地**，逐项讲清需要留意的坑。

---

## 目录

1. [项目简介](#1-项目简介)
2. [架构与目录](#2-架构与目录)
3. [环境要求](#3-环境要求)
4. [快速开始（5 步跑起来）](#4-快速开始5-步跑起来)
5. [配置体系（三层配置）](#5-配置体系三层配置)
6. [命令行速查](#6-命令行速查)
7. [HTTP API 参考](#7-http-api-参考)
8. [前端部署](#8-前端部署)
9. [部署与运维注意事项（重点）](#9-部署与运维注意事项重点)
10. [常见问题排查](#10-常见问题排查)

---

## 1. 项目简介

| 能力 | 说明 |
| --- | --- |
| 多 Agent 接入 | 通过 `agents.json` 声明 Agent：默认由 **harness 通用助手总控**（开放问答直接回答，需要专业能力时自动调用 `external_agent` 工具），也支持关键词路由 / LLM 分类 / 显式指定 |
| 高危操作门禁 | `dangerous-actions.json` 对删除/写文件/执行命令等做 allow / confirm / deny 三级控制 |
| 对话 API | REST + SSE + WebSocket，保留 Agent 调用与操作系统文件能力 |
| 对话历史 | 每个对话有独立 `conversation_id`，历史（含中间工具执行过程）可查询 |

**核心设计原则：能力保留、风险受控** —— 工具不会因为“危险”被删除，而是在运行时
被门禁拦一道，规则全部来自可编辑的 JSON 文件。

---

## 2. 架构与目录

```
请求 (APP/Web)
   │  REST / SSE / WebSocket
   ▼
qaapp/service.py          Starlette 服务，维护“每对话一个 RuntimeBundle”的会话池
   │
   ├─ qaapp/agent_router.py    多 Agent 路由（显式 → 关键词 → LLM → 默认）
   ├─ qaapp/dangerous.py       高危操作门禁 + 权限检查器
   ├─ qaapp/config.py          三份配置的模型与加载
   ├─ qaapp/trace.py           执行轨迹 JSONL 存储
   └─ openharness 运行时       模型调用 / 工具执行 / 插件 / MCP / 沙箱
        └─ ohmo 存储            会话快照 + 轨迹

仓库关键目录：
qaapp/                  后端包（含 examples/ 示例配置、README.md）
frontend/webapp/        参考前端（React + Vite + TS）
tests/test_qaapp/       后端测试
```

---

## 3. 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **3.10+（推荐 3.11+）** |
| 依赖 | 见 `pyproject.toml`；Web 服务额外需要 `starlette` / `uvicorn` / `sse-starlette` |
| Node | 仅构建前端时需要，Node 18+ |
| Docker | **可选**，仅在开启 Docker 沙箱时需要（默认关闭，见 §9.5） |

### 3.1 安装（生成 `abcqa` 命令的关键一步）

`pip install -e .`（或 `uv sync`）会**同时完成两件事**：

1. 安装全部依赖（anthropic / openai / starlette / uvicorn / sse-starlette 等）；
2. 按 `pyproject.toml` 的 `[project.scripts]` 生成命令脚本 `abc` / `abcag` / `abcqa`。

**务必在「能 import openharness 的 Python 环境」里执行**：

```bash
# 方式 A：仓库自带 conda 环境
.conda/envs/openharness/bin/python -m pip install -e .

# 方式 B：uv / pip
uv sync --extra dev          # 或：pip install -e .
```

> ⚠️ **改了 `pyproject.toml`（例如新增了 `abcqa` 入口）之后，必须重新执行一次
> `pip install -e .`，新命令脚本才会生成。** editable 安装指向仓库，代码改动即时
> 生效，但**命令脚本不会自动更新**——这是「`abcqa: command not found`」最常见的原因。

### 3.2 如何判断「装没装好」

```bash
# ① 依赖是否可用（最关键，能过就说明依赖没问题）
python -c "import qaapp, openharness, ohmo; print('依赖 OK')"

# ② 命令脚本是否生成
which abcqa
#    有路径输出 = 已生成；command not found = 脚本缺失（依赖可能仍是好的）
```

**`abcqa` 找不到时，两种等价用法：**

| 用法 | 前提 | 说明 |
| --- | --- | --- |
| `abcqa init` | 已 `pip install -e .` 生成脚本 | 标准用法 |
| `python -m qaapp init` | 只要 `import qaapp` 可用即可 | **无需脚本入口，立即可用** |

> 下文所有 `abcqa xxx` 都可替换成 `python -m qaapp xxx`（建议在仓库根目录执行）。

### 3.3 关于 conda 环境（重要）

本机可能同时存在两个名为 `openharness` 的环境，**二者都可编辑安装指向本仓库、
依赖齐全、均可用**：

1. `/Users/xulele/anaconda3/envs/openharness`（`conda activate openharness` 激活的）
2. `/Users/xulele/Documents/workspace/OpenHarness/.conda/envs/openharness`（仓库自带）

确认当前用的是哪个：

```bash
which python                                   # 看解释器路径
python -c "import qaapp; print(qaapp.__file__)"  # 应指向本仓库 qaapp/__init__.py
```

---

## 4. 快速开始（5 步跑起来）

> 先完成 §3.2 的验证：`python -c "import qaapp"` 能过即可。

```bash
cd /Users/xulele/Documents/workspace/OpenHarness   # 进入仓库根

# ① 验证依赖可用
python -c "import qaapp, openharness, ohmo; print('依赖 OK')"

# ② 写入三份示例配置到 ohmo workspace（默认 ~/.ohmo/）
python -m qaapp init
#    若已生成 abcqa 脚本，也可写：abcqa init
#    已存在时跳过；想覆盖用 --force

# ③ 配置模型与 API Key（关键！不配无法调用模型）
#    编辑 ~/.openharness/settings.json，设置 active_profile 与对应 api_key，详见 §5.1

# ④ 校验配置
python -m qaapp config              # 三份配置的存在性与校验结果
python -m qaapp agents list         # 列出注册的 Agent
python -m qaapp dangerous validate  # 校验高危操作策略

# ⑤ 启动服务
python -m qaapp webapp run          # 前台运行（Ctrl+C 退出），日志输出到 stdout
# 或后台守护：
python -m qaapp webapp start        # 后台启动，日志写入 <workspace>/logs/webapp.log
python -m qaapp webapp status       # 查看运行状态
python -m qaapp webapp stop         # 停止
```

验证：

```bash
curl http://127.0.0.1:8787/healthz
# → {"status":"ok","active_sessions":0}
```

---

## 5. 配置体系（三层配置）

qaapp 的配置分三层，**分别放在不同位置**，请务必分清：

| 层级 | 文件 | 位置 | 内容 |
| --- | --- | --- | --- |
| ① OpenHarness 主配置 | `settings.json` | `~/.openharness/` 等（见 §5.1） | 模型 / API Key / 权限 / 沙箱 / MCP |
| ② qaapp 三份配置 | `webapp.json`、`agents.json`、`dangerous-actions.json` | ohmo workspace（`~/.ohmo/`） | 服务、Agent 注册、高危策略 |
| ③ 外部 Agent 配置 | `external-agents.json` | 见 §5.3 | `kind=external` 的 Agent 端点 |

### 5.1 ① settings.json（OpenHarness 主配置）

查找顺序（**`build_runtime` 每次会话懒构建时读取**）：

1. `OPENHARNESS_CONFIG_DIR` 环境变量指向的 `settings.json`
2. 项目级 `.openharness/settings.json`（从服务启动的 `--cwd` 向上逐级找，到 git 根或 home 为止）
3. `~/.openharness/settings.json`

**必配项**（否则首次真正调用模型会报「未配置认证」并退出）：

```jsonc
{
  "active_profile": "qwen",
  "profiles": {
    "qwen": {
      "provider": "dashscope",
      "api_format": "openai",
      "auth_source": "dashscope_api_key",
      "default_model": "qwen-plus",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk-..."         // ← 改成你自己的 key
    }
  },
  "model": "qwen-plus",
  "api_format": "openai",
  "provider": "dashscope",
  "permission": { "mode": "default", "allowed_tools": [], "denied_tools": [] },
  "sandbox": { "enabled": false },
  "mcp_servers": {}
}
```

- `active_profile` 决定默认使用哪个 profile；`webapp.json` 的 `provider_profile`
  为空时**继承**它，非空时**覆盖**它（该 profile 必须在 `profiles` 中定义）。
- `permission`、`sandbox`、`mcp_servers` 详见 §9。

> 仓库 `.openharness/settings.json` 内含一个可用的 qwen 示例（**含明文 key，仅供
> 本地开发**）。生产环境务必换成你自己的凭据，并注意 §9.9 的密钥安全提醒。

### 5.2 ② qaapp 三份配置（ohmo workspace）

放置位置：`OHMO_WORKSPACE` 环境变量指向的目录，或 `~/.ohmo/`；也可用
`abcqa --workspace <dir>` / `abcqa webapp run --workspace <dir>` 指定。

三份文件缺失或非法时**回退到内置默认值**（不阻塞启动，仅在日志告警），所以
「文件在但内容非法」要主动用 `abcqa config` 检查。

#### `webapp.json` —— 服务配置

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1` | 监听地址；生产对外需 `0.0.0.0` |
| `port` | `8787` | 监听端口 |
| `provider_profile` | `""` | 空=继承 `active_profile`；非空=指定 profile 名 |
| `api_tokens` | `[]` | 静态 Bearer Token 列表；**空=不鉴权**，非空时除 healthz/agents/WS 外均需鉴权（见 §9.1） |
| `cors_origins` | `["*"]` | 允许跨域来源；生产应限定域名 |
| `static_dir` | `""` | 前端构建产物目录；非空则挂载为静态站点 |
| `approval_timeout_seconds` | `300` | 高危操作审批超时（秒），超时按拒绝处理 |
| `conversation.max_active_bundles` | `100` | 预留字段（当前未强制回收） |
| `conversation.idle_timeout_seconds` | `86400` | 预留字段（当前未强制回收） |

#### `agents.json` —— Agent 注册表与路由

**推荐架构：harness 总控自动编排** —— 开放问答由通用助手直接回答；当模型判断问题
属于专业领域时，自动调用 `external_agent` 工具。三个专业 Agent 仍注册、前端可显式指定：

```jsonc
{
  "default_agent": "general",                     // 兜底 = 通用助手
  "routing": { "mode": "explicit", "rules": [] }, // 默认不做关键词强制路由
  "agents": [
    {
      "id": "general", "kind": "harness",
      "system_prompt": "你是通用助手：开放问答直接回答；涉及电网分析/数据查询/知识问答时调用 external_agent 工具",
      "tools": ["*"], "permission_mode": "default"
    },
    { "id": "power-grid-analysis", "kind": "external", "external_ref": "power-grid-analysis" },
    { "id": "smart-data-query",    "kind": "external", "external_ref": "smart-data-query" },
    { "id": "knowledge-qa",        "kind": "external", "external_ref": "knowledge-qa" }
  ]
}
```

`kind` 三种派发方式：

| kind | 说明 | 关键字段 |
| --- | --- | --- |
| `external` | 调用外部 HTTP/stdio 智能体（复用 external-agents 插件） | `external_ref`（对应 §5.3 的 agent id） |
| `subagent` | 复用 OpenHarness 的 AgentDefinition | `subagent_type` |
| `harness` | 内联运行，具备完整工具能力（**含 `external_agent` 工具，可自动编排其它 Agent**） | `system_prompt` / `system_prompt_file` / `tools` / `model` / `max_turns` / `permission_mode` |

**两种工作方式（可并存）**：

1. **harness 总控编排（默认）**：`default_agent` 指向一个 `harness` Agent、`routing.rules`
   留空。开放问答由 harness 用自身能力（模型知识 + `web_search`/`read_file`/`bash` 等
   内置工具）直接回答；当模型判断问题属于专业领域时，自动调用 `external_agent` 工具
   （该工具的描述会动态列出各专业 Agent 的名称、能力与意图判定规则）。
2. **显式指定 / 关键词路由（可选）**：前端让用户选 Agent（请求带 `agent` 字段）；
   或在 `routing.rules` 配关键词规则做确定性快速分流。

路由解析顺序：**显式指定（请求带 `agent`）→ 关键词规则（若配置，priority 降序、子串
匹配）→ LLM 分类（仅 mode 含 `llm` 时）→ `default_agent`**。

#### `dangerous-actions.json` —— 高危操作策略

```jsonc
{
  "approval_timeout_seconds": 300,
  "rules": [
    { "tool": "write_file", "action": "confirm", "reason": "写文件需确认" },
    { "tool": "edit_file",  "action": "confirm" },
    { "tool": "bash", "action": "confirm",
      "deny_commands": ["rm -rf *", "sudo *", "shutdown *"],
      "confirm_commands": ["pip install*"] },
    { "tool": "*", "action": "deny", "paths": ["**/*.pem", "**/.env", "**/.ssh/**"] }
  ]
}
```

规则字段：`tool`（工具名或 `*` 通配）、`action`（allow/confirm/deny）、`paths`（glob，
命中才生效）、`deny_commands`/`confirm_commands`（命令级覆盖，deny > confirm > action）。

**判定优先级（由高到低）**：内置敏感路径（`.pem`/`.env`/`.ssh` 等）→ settings 的
`denied_tools`/`denied_commands`/路径拒绝 → 危险规则 deny → confirm → allow →
settings 的 `allowed_tools` → 基础权限模式。

> **工具名用 OpenHarness 真实名**：`write_file` / `edit_file` / `read_file` / `bash` /
> `web_search` 等。**没有独立的「删除文件」工具**——删除由 `bash` 的 `rm` 完成，因此
> 用 `deny_commands: ["rm -rf *"]` 拦截。完整工具名见 §9.8。

### 5.3 ③ external-agents.json（外部 Agent 配置）

`kind=external` 的 Agent，其真实端点在这里声明。查找顺序：

1. `<cwd>/.openharness/external-agents.json`（cwd = `abcqa webapp run --cwd`，默认当前目录）
2. `~/.openharness/external-agents.json`（或 `OPENHARNESS_CONFIG_DIR` 下）
3. 插件自带的 `plugins/external-agents/external-agents.json`

示例：

```jsonc
{
  "agents": [
    {
      "id": "power-grid-analysis",           // ← agents.json 里的 external_ref 指向这里
      "type": "http",
      "name": "电网专业数据洞察",
      "config": {
        "endpoint": "https://your-gateway/chat",
        "method": "POST",
        "headers": { "content-type": "application/json", "authorization": "Bearer xxx" },
        "request_body": { "message": "{message}", "conversation_id": "{conversation_id}" }
      },
      "response": { "mode": "auto" }          // text | sse | ndjson | auto
    }
  ]
}
```

> 完整示例见 [`qaapp/examples/`](examples/)。`abcqa init` 只写入三份 qaapp 配置；
> `external-agents.json` 需按 `qaapp/examples/external-agents.json` 手工放置。

---

## 6. 命令行速查

```bash
abcqa init [--workspace DIR] [--force]     # 写入三份示例配置
abcqa config [--workspace DIR]             # 三份配置存在性与校验结果
abcqa agents list                          # 列出注册的 Agent
abcqa agents validate                      # 校验 agents.json（exit 1 表示有错）
abcqa dangerous validate                   # 校验 dangerous-actions.json
abcqa dangerous show                       # 打印当前高危策略
abcqa webapp run [--workspace DIR] [--cwd DIR] [--host H] [--port P]
abcqa webapp start|stop|status [--workspace DIR]
```

---

## 7. HTTP API 参考

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/healthz` | **否** | 健康检查 |
| GET | `/api/v1/agents` | **否** | Agent 列表 |
| POST | `/api/v1/conversations` | 是 | 创建对话 → `{"conversation_id": "..."}` |
| GET | `/api/v1/conversations` | 是 | 列出对话（`?limit=&offset=`） |
| GET | `/api/v1/conversations/{id}` | 是 | 对话详情（含 `messages`、`turns`、`pending_approvals`） |
| POST | `/api/v1/conversations/{id}/messages` | 是 | 发送消息（`stream:true` 走 SSE） |
| GET | `/api/v1/conversations/{id}/turns/{turn_id}` | 是 | 单轮完整轨迹（含中间执行过程） |
| POST | `/api/v1/conversations/{id}/approvals/{request_id}` | 是 | 响应审批 |
| POST | `/api/v1/conversations/{id}/interrupt` | 是 | 中断当前回答 |
| WS | `/api/v1/conversations/{id}/stream` | **否** | WebSocket 流式订阅 |

> 「鉴权」列 = 配置了 `api_tokens` 之后是否校验 `Authorization: Bearer <token>`。
> 三个「否」的端点在开启鉴权后仍然开放，见 §9.1。

**发送消息**：

```bash
curl -N http://127.0.0.1:8787/api/v1/conversations/<id>/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"content": "分析福建售电量情况", "stream": true}'
```

`agent` 字段可省略（自动路由）；`stream:false` 同步返回 `{"conversation_id", "turn_id"}`。

**SSE 事件类型**：`agent_routed` / `status` / `tool_started` / `tool_completed` /
`tool_progress` / `assistant_delta` / `assistant_complete` / `approval_required` /
`error` / `turn_complete`。

**审批**：收到 `approval_required`（带 `request_id`）后：

```jsonc
POST /api/v1/conversations/{id}/approvals/{request_id}
{ "decision": "approve" }   // approve | deny | always；always = 本对话内该工具不再询问
```

---

## 8. 前端部署

参考前端在 `frontend/webapp/`（React + Vite + TS），演示流式问答、工具轨迹折叠、
审批弹窗、中断等交互。

```bash
cd frontend/webapp
npm install
npm run dev      # 开发：http://localhost:5173，/api 与 /healthz 代理到 127.0.0.1:8787
npm run build    # 生产构建，产物在 dist/
```

两种生产部署方式：

1. **同源托管（推荐、最简单）**：把 `dist/` 绝对路径填进 `webapp.json` 的
   `static_dir`，重启服务后由同一端口托管前端 + API（前端请求走同源，无需跨域）。
2. **独立托管**：把 `dist/` 部署到 Nginx/CDN，前端 `api.ts` 里把 `BASE` 改成 API
   地址，并在 `webapp.json` 的 `cors_origins` 里加入前端域名。

---

## 9. 部署与运维注意事项（重点）

### 9.1 鉴权暴露面（务必看清）

`api_tokens` 非空时，**以下端点仍不鉴权**：`/healthz`、`/api/v1/agents`、
WebSocket `/stream`。其余 API 需 `Authorization: Bearer <token>`。

- `api_tokens` 为空 = 完全开放，**仅限本机/可信内网**。
- 生产对公网暴露时，建议：① 配 `api_tokens`；② 在反向代理（Nginx）层对
  `/api/v1/*` 统一加鉴权/限流，把 healthz/agents/ws 一并纳入管控；③ 用 TLS。

### 9.2 会话是「单进程内存 + 磁盘快照」

- **历史持久化**：会话快照（`<workspace>/sessions/session-{id}.json`）与轨迹
  （`<workspace>/sessions/traces/{id}.jsonl`）落盘；**重启后可凭 conversation_id 恢复
  历史，并继续同一对话**（运行时懒加载恢复消息上下文）。
- **不持久化的部分**：进行中的 SSE/WS 订阅、`pending` 审批请求、`always_approved`
  白名单都在进程内存，**重启即丢失**。
- **横向扩展受限**：当前是单进程会话池，多个进程实例不共享内存中的订阅/审批状态。
  需要多实例时，须自行引入共享存储 + 消息总线（如 Redis pub/sub）改造。

### 9.3 对外访问：host / 端口 / 反向代理

- 默认 `host=127.0.0.1` 只监听本机；对外服务改成 `0.0.0.0`（或 CLI `--host 0.0.0.0`）。
- 生产建议前置 Nginx/Caddy 做 TLS 终止、反向代理到 `127.0.0.1:8787`，并对 SSE 路径
  关闭缓冲（`proxy_buffering off`）以保持流式。
- SSE 长连接会占用连接数，注意反向代理的 `proxy_read_timeout` 调大（如 300s+）。

### 9.4 模型凭据与 profile

- 必须在 `settings.json` 配好 `active_profile` + 对应 `api_key`，否则首次调用模型时
  进程会因「未配置认证」退出。
- `webapp.json.provider_profile` 为空 → 用 `active_profile`；非空 → 覆盖（profile 名
  必须存在于 `profiles`）。多个租户/模型可借此切不同 profile。
- 凭据也可走环境变量（取决于 profile 的 `auth_source`，如 `OPENAI_API_KEY`、
  `DASHSCOPE_API_KEY` 等）。

### 9.5 沙箱（Docker）默认关闭

- `settings.json` 的 `sandbox.enabled` 默认 `false`，**不需要 Docker**。
- 若开启 `sandbox.enabled=true` 且 `backend=docker`，则需安装 Docker 并准备镜像
  （`sandbox.docker.image`），否则启动沙箱会失败。

### 9.6 MCP 服务器

- 若 `settings.json` 配置了 `mcp_servers`，每次会话懒构建时会 `connect_all()`，
  需要网络可达；未配置则 no-op。

### 9.7 运行时数据与日志落点

服务启动时会（若环境变量未显式设置）自动把 openharness 数据目录重定向到
workspace 下：

- `OPENHARNESS_DATA_DIR` → `<workspace>/data`（记忆等）
- `OPENHARNESS_LOGS_DIR` → `<workspace>/logs`

后台 `start` 模式的 stdout/stderr 追加到 `<workspace>/logs/webapp.log`；前台 `run`
输出到终端。会话/轨迹始终在 `<workspace>/sessions/`。

### 9.8 内置工具名清单（供配置门禁/denied_tools 参考）

`read_file`、`write_file`、`edit_file`、`bash`、`grep`、`glob`、`web_search`、
`web_fetch`、`agent`、`task_*`、`cron_*`、`team_*`、`skill`、`mcp_*`、`todo_write`、
`ask_user_question`、`send_message`、`enter_plan_mode`、`exit_plan_mode`、
`enter_worktree`、`exit_worktree`、`notebook_edit`、`image_to_text`、
`image_generation`、`sleep` 等。以 `abcqa agents list` / 运行时 `tool_search` 为准。

### 9.9 密钥安全（重要）

- 仓库 `.openharness/settings.json` 目前**含明文 `api_key`**，且根 `.gitignore` 中
  存在 `!.openharness/settings.json` 例外，**可能被提交进版本库**。
- 落地时务必：① 从 `settings.json` 移除明文 key，改用环境变量或 `abc auth login`
  存储凭据；② 确认 `.openharness/settings.json` 不在版本控制内，或单独维护一份脱敏
  模板；③ 轮换已泄露的 key。

### 9.10 其它

- 高危操作审批**超时默认 300s 按拒绝处理**；前端需及时响应 `approval_required`，
  否则 Agent 工具调用会被拒。
- 同一对话同一时刻只处理一轮（`busy` 状态），并发发送会得到 409；可调
  `/interrupt` 中断。
- `conversation.max_active_bundles` / `idle_timeout_seconds` 目前是**预留字段，
  未强制回收**——长期运行需自行关注会话池内存占用。

---

## 10. 常见问题排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `abcqa` 找不到命令（command not found） | 依赖通常没问题，只是命令脚本未生成：改了 `pyproject.toml` 后要重新 `pip install -e .`；或直接用 `python -m qaapp` 等价替代，见 §3.2 |
| 首次调模型报「未配置认证」/ 直接退出 | `settings.json` 未配 `active_profile` 与 `api_key`，见 §5.1 |
| `abcqa config` 显示 `errors` 非空 | 对应 JSON 非法（字段/枚举错）；按提示修正 |
| 发送消息 404 `agent_not_found` | 请求指定的 `agent` 或路由命中的 agent 未在 `agents.json` 注册 |
| 发送消息 404 `no_default_agent` | 无关键词命中且未设 `default_agent` |
| 外部 Agent 报「工具未加载（插件缺失）」 | `plugins/external-agents` 未加载；检查 `extra_plugin_roots` 与插件目录 |
| 外部 Agent 报找不到 agent id | `external_ref` 与 `external-agents.json` 里的 `id` 不一致，或配置放错位置（§5.3） |
| 审批一直不生效/工具被拒 | 审批超时（默认 300s）按拒绝处理；前端未及时响应 |
| 重启后历史在，但流/审批丢了 | 正常：订阅与审批在进程内存，见 §9.2 |
| SSE 中途断流 | 反向代理缓冲/超时；关 buffering、调大 read_timeout（§9.3） |
| 后台启动后端口没起来 | 看 `<workspace>/logs/webapp.log`；`abcqa webapp status` 看 pid |

---

## 附：测试与校验

```bash
pytest tests/test_qaapp -q        # 30 个用例
ruff check qaapp tests/test_qaapp # 静态检查
cd frontend/webapp && npm run build
```
