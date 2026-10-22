# qaapp 部署、配置与运维

本文覆盖环境要求、配置体系、前端部署、运维安全与常见问题。快速开始与 CLI 见
[`qaapp/README.md`](../../qaapp/README.md)；架构与扩展见
[`ARCHITECTURE.zh-CN.md`](ARCHITECTURE.zh-CN.md)。

---

## 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **3.10+（推荐 3.11+）** |
| 依赖 | 见 `pyproject.toml`；Web 服务额外需要 `starlette` / `uvicorn` / `sse-starlette` |
| 文档解析 | `python-docx` / `openpyxl` / `xlrd` / `pandas`（已列入 `dependencies`，用于上传附件解析与会话脚本数据分析） |
| Node | 仅构建前端时需要，Node 18+；`run_script` 支持 node 脚本时也要求服务器有 `node` |
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

1. `<conda-env>`（`conda activate openharness` 激活的）
2. `<repo-root>/.conda/envs/openharness`（仓库自带）

确认当前用的是哪个：

```bash
which python                                   # 看解释器路径
python -c "import qaapp; print(qaapp.__file__)"  # 应指向本仓库 qaapp/__init__.py
```

---


---

## 配置体系（三层配置）

qaapp 的配置集中在 `config/backend/`，环境差异只由一个 `.env` 控制：

| 类型 | 文件 | 位置 | 内容 |
| --- | --- | --- | --- |
| 环境配置 | `.env_dev` / `.env_prod` → `.env` | `config/backend/` | 模型密钥、DB/Redis、Web 端口、外部 Agent 端点 |
| OpenHarness 主配置 | `settings.json` | `config/backend/` | 模型 profile / 权限 / 沙箱 / MCP |
| qaapp 配置 | `webapp.json`、`agents.json`、`dangerous-actions.json`、`datasources.json` | `config/backend/` | 服务、Agent 注册、高危策略、数据源（单份，不分层） |

### 5.1 settings.json（OpenHarness 主配置）

查找顺序（**`build_runtime` 每次会话懒构建时读取**）：

1. `OPENHARNESS_CONFIG_DIR` 环境变量指向的 `settings.json`
2. 项目级 `config/backend/settings.json`（从服务启动的 `--cwd` 向上逐级找，到 git 根或 home 为止）
3. `~/.openharness/settings.json`（legacy 用户配置）

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
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
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

> 仓库 `config/backend/settings.json` 只保留 profile 模板，**不含任何明文 key**。
> 真实凭据放 `config/backend/.env`（已 gitignore）或进程环境变量；详见
> [`config/README.md`](../../config/README.md)。

### 5.2 ② qaapp 配置（单目录、单 `.env`）

配置只放在 `config/backend/`，不再有 base/dev/prod/local 分层合并：

```text
config/backend/
├── .env_dev / .env_prod   # 环境模板
├── .env                   # 当前环境（本机 cp 生成，gitignore）
├── settings.json
├── webapp.json
├── agents.json
├── dangerous-actions.json
└── datasources.json
```

切换环境只复制 `.env`：

```bash
# 开发
cp config/backend/.env_dev  config/backend/.env
# 生产
cp config/backend/.env_prod config/backend/.env
```

`webapp.json` 提供结构化默认值；`host` / `port` / `api_tokens` / `cors_origins` /
`static_dir` / `browser_auth.enabled` 等环境差异由 `.env` 中的 `QAAPP_*` 覆盖。
`datasources.json` 与 `agents.json` 只保留 `${DB_*}` / `${...}` 占位符，实际值来自 `.env`。

查看实际生效文件：

```bash
abcqa config
abcqa agents list
```

**服务器部署（推荐流程）**：

```bash
git pull
cp config/backend/.env_prod config/backend/.env
# 编辑 .env 填写密钥
abcqa webapp stop && abcqa webapp start
```

> 旧部署仍可读取 `~/.ohmo/<name>.json` 与早期 `<cwd>/config/<name>.json`；
> 新部署请使用 `config/backend/`，详见 [`config/README.md`](../../config/README.md)。

配置文件缺失或非法时**回退到内置默认值**（不阻塞启动，仅在日志告警），所以
「文件在但内容非法」要主动用 `abcqa config` 检查。

> 启动时还会做一次外部 Agent 自检：`kind=external` 若只写了 `external_ref` 而对应配置
> 找不到，会直接打 **ERROR** 日志（含 agent id 与修复建议），不会等到用户提问时才发现。

#### `webapp.json` —— 服务配置

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `host` | `0.0.0.0` | 监听地址。**默认监听所有网卡**，便于局域网内的 APP / 其他设备直接访问；仅需本机访问时改为 `127.0.0.1`。对外暴露务必配 `api_tokens`（见 部署与运维注意事项） |
| `port` | `8787` | 监听端口 |
| `provider_profile` | `""` | 空=继承 `active_profile`；非空=指定 profile 名 |
| `api_tokens` | `[]` | 服务端 token 列表；**空=不鉴权**。非空时，API 客户端用 `Authorization: Bearer <token>`，浏览器用 `browser_auth` 自动建立会话（见 部署与运维注意事项） |
| `browser_auth.enabled` | `true` | 浏览器免手工 token：`/api/v1/bootstrap` 用 `api_tokens` 的存在性签发 HttpOnly 会话 Cookie。关闭后前端需 `VITE_API_TOKEN` 或反向代理注入 Authorization |
| `browser_auth.ttl_seconds` | `604800` | 浏览器会话 Cookie 有效期（秒，默认 7 天） |
| `browser_auth.cookie_name` | `"qaapp_session"` | Cookie 名；同主机多实例时用于区分 |
| `cors_origins` | `["*"]` | 允许跨域来源；前后端分机 + 浏览器会话 Cookie 时必须写成明确来源（不能是 `"*"`） |
| `static_dir` | `""` | 前端构建产物目录；非空则挂载为静态站点 |
| `approval_timeout_seconds` | `300` | 高危操作审批超时（秒），超时按拒绝处理 |
| `conversation.max_active_bundles` | `100` | `abcqa cleanup` 默认保留的最近会话数（`--keep-latest`） |
| `conversation.idle_timeout_seconds` | `86400` | `abcqa cleanup` 默认的闲置阈值（秒；`--older-than-days` 可覆盖） |
| `attachments.enabled` | `true` | 是否允许上传附件 |
| `attachments.max_files_per_message` | `2` | 单条消息（单次上传请求）最多文件数 |
| `attachments.max_file_mb` | `20.0` | 单文件大小上限（MB），超出返回 413 |
| `attachments.max_files_per_conversation` | `50` | 单会话累计附件数上限（防磁盘写满） |
| `attachments.allowed_suffixes` | `.txt/.md/.csv/.docx/.xlsx/.xls/.json/.log/.yaml/.yml/.xml` | 允许上传的后缀白名单；**不支持旧版 `.doc`**（提示另存为 `.docx`） |
| `attachments.max_extract_chars` | `400000` | 解析文本落盘上限（超出截断并标注） |
| `attachments.preview_chars` | `1500` | 每个附件注入提示词的节选长度 |
| `attachments.context_max_chars` | `8000` | 「会话工作区」上下文块总长度上限 |
| `attachments.max_excel_rows_per_sheet` | `2000` | Excel/CSV 每个工作表最多解析行数 |
| `conversation_workspace.root` | `""` | 会话目录根；空 = `<OHMO_WORKSPACE>/conversations` |
| `conversation_workspace.auto_allow_write_tools` | `["write_file","edit_file","notebook_edit"]` | 这些工具在**会话目录内**写文件免审批（`bash`/`run_script` 不在此列，仍按门禁确认） |
| `conversation_workspace.inventory_limit` | `40` | 每轮列给模型的会话目录文件条数上限 |
| `conversation_workspace.script_timeout_seconds` | `300` | `run_script` 单次执行超时（秒） |
| `conversation_workspace.script_code_max_chars` | `200000` | 单个脚本内容上限 |
| `conversation_workspace.script_output_max_chars` | `40000` | 回传给模型的脚本输出上限 |

#### `agents.json` —— Agent 注册表与路由

**推荐架构：harness 总控自动编排** —— 开放问答由通用助手直接回答；当模型判断问题
属于专业领域时，自动调用 `external_agent` 工具；需要真实计算/文件处理时用
`run_script`。专业 Agent 仍注册、前端可显式指定：

> **通用提示词里不要写死 agent id / 端点 / 关键词**。这些信息由运行时从
> `agents.json` 生成「可调度能力清单」并追加到系统提示（见 `qaapp/agents/capabilities.py`）。
> 新增 Agent 只需改配置，下一轮模型就能看到。

```jsonc
{
  "default_agent": "general",                     // 兜底 = 通用助手
  "routing": { "mode": "explicit", "rules": [] }, // 默认不做关键词强制路由
  "agents": [
    {
      "id": "general", "kind": "harness",
      "system_prompt": "你是企业级智能助手：能直接回答就直接回答；需要专业能力或真实执行时，按运行时注入的「可调度能力」清单选择工具。不要向用户索要 token / 接口地址。",
      "tools": ["*"], "permission_mode": "default"
    },
    // 外部 Agent 直接内联访问配置（endpoint/请求体/响应解析），不再另建文件
    { "id": "power-grid-analysis", "kind": "external", "name": "电网指标数据分析",
      "description": "售电量/线损等指标分析",
      "external": { "type": "http", "conversation_id": "auto",
        "config": { "endpoint": "http://192.168.139.144:18081/api/chat/stream",
                    "method": "POST", "headers": { "content-type": "application/json" },
                    "request_body": { "message": "{message}", "conversation_id": "{conversation_id}" } },
        "response": { "mode": "auto", "extract_tags": ["answer"] } } }
  ]
}
```

`kind` 三种派发方式：

| kind | 说明 | 关键字段 |
| --- | --- | --- |
| `external` | 调用外部 HTTP/stdio 智能体（transport 由 external-agents 插件提供） | `external`（内联 endpoint/请求体/响应解析，推荐）；旧配置可仍用 `external_ref` |
| `subagent` | 复用 OpenHarness 的 AgentDefinition | `subagent_type` |
| `harness` | 内联运行，具备完整工具能力（**含 `external_agent` 工具，可自动编排其它 Agent**） | `system_prompt` / `system_prompt_file` / `tools` / `disallowed_tools` / `model` / `max_turns` / `permission_mode` |

**`tools` / `disallowed_tools` 是硬边界**（运行时由 `qaapp/tools/policy.py` 生效）：

- 省略或 `["*"]` = 全部工具；`[]` = 不允许任何工具；支持 `web_*`、`mcp_*` 等通配；
- `disallowed_tools` 优先于 `tools`；
- 被裁掉的工具不会出现在模型工具表，也不会出现在动态能力清单里。

**两种工作方式（可并存）**：

1. **harness 总控编排（默认）**：`default_agent` 指向一个 `harness` Agent、`routing.rules`
   留空。开放问答由 harness 用自身能力（模型知识 + `web_search`/`read_file`/`bash` 等
   内置工具）直接回答；需要专业能力时调用 `external_agent`，需要真实执行时调用
   `run_script`。每轮系统提示 = Agent 自身提示 + **动态能力清单** + 会话工作区上下文。
2. **显式指定 / 关键词路由（可选）**：前端让用户选 Agent（请求带 `agent` 字段）；
   或在 `routing.rules` 配关键词规则做确定性快速分流。

路由解析顺序：**显式指定（请求带 `agent`）→ 关键词规则（若配置，priority 降序、子串
匹配）→ `default_agent`**。

> LLM 分类是**预留能力**：`resolve_agent` 接受 `llm_classifier`，但 qaapp 服务端目前
> 未注入；`routing.mode=llm` 不会产生额外模型调用。生产推荐「默认 harness + 动态能力
> 清单」，比每轮先做一次分类调用更省成本、更可解释。

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

### 5.3 ③ 外部 Agent：内联在 agents.json（唯一维护文件）

**所有 Agent 只在一个文件里维护：`agents.json`**，仓库内推荐路径
**`config/backend/agents.json`（可见、提交 git、`git pull` 即生效）**；解析顺序见 配置体系
（`--config-dir` → `<cwd>/config/` → `~/.ohmo/`）。
`kind=external` 的 Agent 直接内联它的访问配置，qaapp 在会话启动时把它注入
`external_agent` 工具（内存注入，不再生成第二份配置文件）：

```jsonc
{
  "agents": [
    {
      "id": "power-grid-analysis",
      "kind": "external",
      "name": "电网指标数据分析",
      "description": "售电量、线损等指标的分析报告",   // 模型据此路由，要写清楚触发场景
      "external": {
        "type": "http",                  // http | stdio
        "enabled": true,
        "conversation_id": "auto",       // auto = 用 qaapp 会话 ID；固定值 = 每次复用该会话
        "timeout_seconds": 300,
        "max_response_chars": 220000,
        "progress_max_chars": 4000,      // 流式进度最多转发多少字符（防刷屏/卡死）
        "response": {                    // 响应解析（与插件 AgentResponseConfig 同构）
          "mode": "auto",                // auto | sse | ndjson | text
          "json_text_paths": ["choices[0].delta.content", "content", "answer", "message", "data", "text"],
          "extract_tags": ["answer"]     // 只保留这些标签里的最终结果；[] = 关闭提取
        },
        "config": {                      // 传输配置（与插件 HttpTransportConfig 同构）
          "endpoint": "http://192.168.139.144:18081/api/chat/stream",
          "method": "POST",
          "headers": { "content-type": "application/json" },
          "request_body": {
            "message": "{message}",
            "conversation_id": "{conversation_id}",
            "context": {},
            "chat_type": 4
          }
        }
      }
    }
  ]
}
```

要点：

- **占位符**：`request_body` 里的 `{message}` = 用户原话（有附件时前面会拼上解析内容），
  `{conversation_id}` = 下面这条规则算出来的会话 ID；stdio 类型用 `input_json` + `command`，
  见插件 README。
- **会话 ID 映射（很重要）**：qaapp 统一按「**一个 qaapp 会话 = 一个对端会话**」注入
  `conversation_id`，**harness 总控把 `external_agent` 当工具调用时也一样**：

  | `external.conversation_id` | 实际发出 |
  | --- | --- |
  | `auto`（默认）或空 | 本 qaapp 会话 ID（同一对话的多次调用复用同一个对端会话） |
  | 固定值 | 该固定值（每个 qaapp 会话都复用它，用于跳过对端的会话创建流程） |

  > 为什么要强制注入：插件本身在调用方没传 `conversation_id` 时会**每次生成一个随机
  > uuid**，对端就要为每次调用**新建会话**——平台侧会话存储不稳时，用户看到的就是
  > 「会话创建失败: ... Connection reset by peer」。固定成 qaapp 会话 ID 后，同一个对话只在
  > 第一次调用时创建会话、后续复用。若对端仍然报错，说明问题在对端（**qaapp 侧没有任何
  > Redis / 会话存储依赖**），可让对端排查，或配一个已知可用的固定会话 ID。
- **兼容旧配置**：仍支持 `external_ref`（指向 `external-agents.json` 里的 id），此时会话 ID
  取文件里该 Agent 的配置（同为 `auto`/空时也用 qaapp 会话 ID）。插件原有的
  文件发现顺序不变：`<cwd>/.openharness/external-agents.json` → `~/.openharness/external-agents.json`
  → 插件自带的 `plugins/external-agents/external-agents.json`（**仓库自带那份已清空为占位**，
  避免和 agents.json 放两份真实地址）。qaapp 的规则是：**同名以内联配置优先**，其余文件里的
  Agent 依然可用。
- 旧的示例文件 `plugins/external-agents/external-agents.json` 已删除；需要给 `abc` / `abcag`
  等不读 agents.json 的入口配置时，才在插件目录那份里添加。

> **问数类智能体只取最终结果**：数据查询类智能体的流式响应会把推理过程、工具调用与
> 工具返回全部包在外层标签里（单次可达数十万字符），给前端会造成页面卡死。默认只保留
> `<answer>` 标签内的最终结果（`response.extract_tags`，取最后一次出现），并把流式进度
> 限制在 `progress_max_chars`（默认 4000）字符内。需要图表数据时可配
> `"response": {"extract_tags": ["answer", "extractData"]}`；详见
> [`plugins/external-agents/README.md`](../../plugins/external-agents/README.md)。

---


---

## 前端部署

参考前端在 `frontend/webapp/`（React + Vite + TS），演示流式问答、工具轨迹折叠、
审批弹窗、中断等交互。**助手回答默认按 Markdown 渲染**（GFM 表格/代码块/列表，整体被
` ```markdown ` 包住时自动解包），顶栏可切换全局默认、单条消息可切源码或复制原文；
渲染层不开 `rehype-raw`、不自动加载远程图片（详见
[`frontend/webapp/README.md`](../../frontend/webapp/README.md)）。

```bash
cd frontend/webapp
npm install
npm run dev      # 开发：监听 0.0.0.0:5173，/api 与 /healthz 代理到 VITE_API_TARGET（默认 127.0.0.1:8787）
npm run build    # 生产构建，产物在 dist/
npm run preview  # 预览构建产物：监听 0.0.0.0:4173
```

两种生产部署方式：

1. **同源托管（推荐、最简单）**：把 `dist/` 绝对路径填进 `webapp.json` 的
   `static_dir`，重启服务后由同一端口托管前端 + API（前端请求走同源，无需跨域）。
2. **独立托管**：把 `dist/` 部署到 Nginx/CDN，用 `VITE_API_BASE_URL` 指定后端地址
   （见 §8.1），并在 `webapp.json` 的 `cors_origins` 里放行前端域名。

前端交互还包含（详见 [`frontend/webapp/README.md`](../../frontend/webapp/README.md)）：

- **免手工 token**：页面启动时调用 `/api/v1/bootstrap` 建立 HttpOnly 会话，不再显示
  Bearer Token 输入框；token 由服务端从 `webapp.json` 读取，不进入 localStorage / JS。
- **国网绿 Codex 风格界面**：浅色中性画布 + 国网绿主色，侧边栏会话列表、居中对话列、
  紧凑执行轨迹、浮动输入区；支持 390 / 768 / 1440 三档响应式、移动端侧边栏/文件抽屉、
  自动增高输入框、流式自动跟随、错误 toast、代码块复制与编辑审批 diff 预览。
- **选项式澄清**：模型遇到口径、时间范围、统计维度、输出格式等不明确点时，通过
  `ask_user_question` 给出 2–6 个具体选项（可标推荐项），前端渲染为单选卡 +「其他」
  自由输入；用户点「确认并继续」后工具拿到答案、模型继续执行。刷新页面后已回答的
  问题由 `approval_resolved` 轨迹事件还原为完成态，不会重新弹出。
- **附件上传**：输入框工具条的「附件」按钮 + 拖拽到消息区/输入框；最多 2 个，选中即上传并
  显示解析状态；发送时把 `attachment_id` 随消息带给后端；刷新页面后靠 `attachments_ready`
  轨迹事件还原附件芯片。
- **右侧「会话文件」抽屉**：按 uploads/extracted/scripts/tmp/outputs 分组列出会话目录文件，
  支持下载（fetch + Blob，避免 token 进 URL）、文本预览与复制会话目录绝对路径。
  **自动同步刷新**：抽屉打开时写文件类工具完成即静默刷新（600ms 防抖），任务结束 /
  中断时也会刷新；新生成的脚本与产物无需手动关闭重开抽屉。
- **过程文件 / 结果文件卡片**：任务全部完成后，每轮回答正文下方先展示「已编辑 N 个文件
  +X -Y」的过程文件卡片（默认展开前 5 个，可看全部 / 折叠），再展示 `outputs/` 下的成果
  文件卡片（Word / PDF / PPT / Excel / Markdown）；结果文件点击卡片或「下载」即可直接
  下载，Markdown 可预览。csv / py / txt / json / 图片只在过程区展示。任务进行中不展示
  文件卡片，避免列表随工具调用反复增减。旧轨迹没有 `turn_files` 事件时，前端从工具
  输出推导，保证历史会话同样可见（只对已完成的轮次）。
- 前端自检：`npm run check:render`（Markdown 渲染）+ `npm run check:attachments`（附件纯函数）
  + `npm run check:turn-files`（过程/结果文件纯函数）+ `npm run check:questions`
  （选项规范化 / 审批回放）。

### 8.1 配置后端地址（前后端分机部署必读）

前端通过**环境变量**指定后端，**无需改代码**。两个变量语义不同，别混用：

| 变量 | 作用 | 用在哪 |
| --- | --- | --- |
| `VITE_API_TARGET` | **Vite 开发/预览服务器**代理转发的目标地址（服务端转发） | `npm run dev` / `npm run preview`；浏览器同源请求，**不产生跨域** |
| `VITE_API_BASE_URL` | **浏览器直连**后端的基址 | 仅当前端产物部署在与后端不同的域名/端口时；需后端 `cors_origins` 放行（不能是 `"*"`，否则 Cookie 不生效） |
| `VITE_API_TOKEN` | 构建时写死的 Bearer token（可选兜底） | 仅当 `browser_auth.enabled=false`（公网 / SSO 场景）；会打进前端产物，公网慎用 |

配置方式（**推荐第 2 种**，长期生效且不进版本库）：

```bash
cd frontend/webapp

# 方式一：命令行临时指定
VITE_API_TARGET=http://your-backend:8787 npm run dev

# 方式二：复制集中配置模板（.env 已 gitignore）
cp config/frontend/.env_dev config/frontend/.env
# 按需编辑 config/frontend/.env，例如：VITE_API_TARGET=http://your-backend:8787
npm run dev
```

常见场景该填什么：

| 场景 | `VITE_API_TARGET` |
| --- | --- |
| 前端与后端**同一台机器** | `http://127.0.0.1:8787`（默认值，可不配） |
| 前端在本机、后端在 `192.168.139.144` | `http://192.168.139.144:8787` |
| 前端就跑在 `192.168.139.144`、别人访问 `:5173` | `http://127.0.0.1:8787`（dev server 与后端同机，走回环即可） |

> ⚠️ `VITE_API_TARGET` 是**运行 `npm run dev` 那台机器**去连后端的地址，**不是浏览器的地址**。
> 因此只有「前端与后端不在同一台机器」时才需要改成服务器 IP。

生产构建且前后端分离部署时：

```bash
VITE_API_BASE_URL=http://192.168.139.144:8787 npm run build
```

### 8.2 改动生效规则（很容易踩）

| 改了什么 | 必须做什么 | 为什么 |
| --- | --- | --- |
| `qaapp/` 或 `src/openharness/` 的 **Python 代码** | **重启服务**（前台 Ctrl+C 重跑，或 `abcqa webapp stop && abcqa webapp start`） | 后端没有热重载，进程内跑的是启动时加载的代码 |
| `frontend/webapp/src/` 的**前端代码** | `npm run build` 后**刷新浏览器**（同源托管）；`npm run dev` 模式则自动热更新 | 同源托管加载的是 `dist/` 构建产物，不重新构建就仍是旧代码 |
| 仅改 `~/.ohmo/*.json` 的**配置** | 重启服务 | agent 注册表 / 危险操作策略在服务启动时加载 |
| 改 `config/backend/agents.json`（提交 git 的那份） | `git pull` + **重启服务** | 配置只在启动时读取；`abcqa config` 可确认实际生效路径（配置体系） |

> 排查「改了没生效」时，先确认这两条：**后端重启了吗？前端重新构建并刷新了吗？**

---


---

## 部署与运维注意事项（重点）

### 9.1 鉴权暴露面（务必看清）

> ⚠️ **服务默认监听 `0.0.0.0`（所有网卡）**，同一局域网内的任何设备都能访问。
> 因此 `api_tokens` **强烈建议务必配置**；留空等于把「对话 API + 文件写入 + shell 执行」
> 的能力开放给同网段所有人。仅需本机访问时，把 `host` 改回 `127.0.0.1`。

#### 浏览器：免手工 token（默认）

页面不再要求用户填 Bearer Token。流程是：

```text
浏览器 GET /api/v1/bootstrap
  服务端读取 webapp.json:api_tokens
  → 签发 Set-Cookie: qaapp_session=<进程内随机值>; HttpOnly; SameSite=Lax
浏览器后续请求自动携带 Cookie
```

- Cookie 值**不是 token 本身**，也不进入 localStorage / JS；
- `api_tokens` 为空时 `auth_required=false`，前端不需要任何凭据；
- 服务重启后随机值变化，前端收到 401 会自动重建一次会话；
- 关闭：`webapp.json` 设 `"browser_auth": {"enabled": false}`，此时前端需
  `VITE_API_TOKEN` 构建，或由反向代理 / SSO 注入 `Authorization`。

> ⚠️ **边界**：能访问 `/api/v1/bootstrap` 的客户端都能建立会话。它是「免手工输入」的
> 体验优化，**不是访问控制边界**。公网部署必须在网关层做认证/限流，或关闭
> `browser_auth` 使用严格 Bearer。

#### API 客户端：Bearer

API 客户端（APP / curl / 服务端调用）仍使用：

```bash
Authorization: Bearer <api_tokens 里的任意一个>
```

`api_tokens` 非空时，**以下端点仍不鉴权**：`/healthz`、`/api/v1/bootstrap`、
`/api/v1/agents`、WebSocket `/stream`。其余 API 需要 Cookie 或 Bearer。

- `api_tokens` 为空 = 完全开放，**仅限本机/可信内网**。
- 生产对公网暴露时，建议：① 配 `api_tokens`；② 在反向代理（Nginx/Caddy）层对
  `/api/v1/*`（含 bootstrap/agents/ws）统一加认证/限流；③ 用 TLS；④ 必要时关闭
  `browser_auth`，只允许网关注入 Authorization。

### 9.2 会话是「单进程内存 + 磁盘快照」

- **历史持久化**：会话快照（`<workspace>/sessions/session-{id}.json`）与轨迹
  （`<workspace>/sessions/traces/{id}.jsonl`）落盘；**重启后可凭 conversation_id 恢复
  历史，并继续同一对话**（运行时懒加载恢复消息上下文）。
- **不持久化的部分**：进行中的 SSE/WS 订阅、`pending` 审批请求、`always_approved`
  白名单都在进程内存，**重启即丢失**。
- **横向扩展受限**：当前是单进程会话池，多个进程实例不共享内存中的订阅/审批状态。
  需要多实例时，须自行引入共享存储 + 消息总线（如 Redis pub/sub）改造。

### 9.3 对外访问：host / 端口 / 反向代理

- **默认 `host=0.0.0.0`，即监听所有网卡**，局域网内其他设备（手机 APP、同事电脑）
  可直接访问。仅需本机时改成 `127.0.0.1`，或用 CLI 临时覆盖：
  `abcqa webapp run --host 127.0.0.1`。
- 前端 `npm run dev` / `npm run preview` 同样监听 `0.0.0.0`（见 `vite.config.ts`）：
  - 前端页面：`http://<本机IP>:5173`（dev）或 `http://<本机IP>:4173`（preview）
  - 后端 API：`http://<本机IP>:8787`
  - dev 模式下前端的 `/api` 由 Vite 代理转发到 `VITE_API_TARGET`（默认 `127.0.0.1:8787`），
    因此**浏览器无需跨域**；后端在别的机器时按 §8.1 改这个变量即可。
  - 查本机 IP：macOS `ipconfig getifaddr en0`；Linux `hostname -I`。
- 因为默认已对外监听，**请务必设置 `api_tokens`**：否则同网段任何人都能调用对话
  API，进而触发文件写入 / shell 执行（见 部署与运维注意事项）。
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
`image_generation`、`sleep` 等。

qaapp 额外注入：

- `run_script` —— 会话目录内执行 Python/Node（脚本固定写 `scripts/`，产物 `outputs/`）；
- `external_agent` —— 调用 `agents.json` 注册的外部智能体（插件提供）；
- `db_query` —— 业务库只读查询/EDA/大纲/新闻/图表（qaapp 内置）；
- `consult_agents` —— 批量调用多个外部智能体（qaapp 内置）；
- `write_report_docx` —— 生成 Word 报告（qaapp 内置）。

以 `abcqa agents list` / 运行时 `tool_search` 为准。`tools` / `disallowed_tools`
里的名字必须与真实工具名一致；支持 `*` 与 `web_*`、`mcp_*` 这类通配。

### 9.9 密钥安全（重要）

- 密钥只放 `config/backend/.env`（已 gitignore）或部署平台的进程环境变量。
- `config/backend/settings.json` 只保留 profile 模板；`config/backend/local/*.json`
  用于本机覆盖，同样不提交。
- 历史提交中如出现过明文 key，请视为已泄露并立即轮换。

### 9.10 其它

- 高危操作审批**超时默认 300s 按拒绝处理**；前端需及时响应 `approval_required`，
  否则 Agent 工具调用会被拒。
- 同一对话同一时刻只处理一轮（`busy` 状态），并发发送会得到 409；可调
  `/interrupt` 中断。
- `conversation.max_active_bundles` / `idle_timeout_seconds` 同时是 `abcqa cleanup` 的
  默认策略：前者始终保留最近 N 个会话，后者是闲置阈值。清理只删 qaapp 的 12 位会话 ID
  数据，不会碰 ohmo 个人助手的其它会话快照（见 部署与运维注意事项1）。

### 9.11 会话临时目录、附件上传与 `run_script`

**目录布局**（`conversation_workspace.root` 留空时为 `<OHMO_WORKSPACE>/conversations`）：

```text
~/.ohmo/conversations/<conversation_id>/
├── uploads/       用户上传的原始文件（保留原名，重名自动 -1/-2）
├── extracted/     解析后的纯文本（模型用 read_file 读它拿到全文）
├── scripts/       本会话生成的脚本（run_script 写入）
├── tmp/           中间临时文件
├── outputs/       脚本产物（约定写到此处）
├── attachments.json  附件索引（attachment_id → 元数据）
└── meta.json         会话元数据（created_at / last_turn_at）
```

- 目录在会话首次被访问时幂等创建（`POST /conversations`、上传、发消息都会触发）。
- **不会随会话池回收而删除**：文件就是「本会话的临时工作区」。清理统一走
  `abcqa cleanup`（**默认 dry-run**，确认后加 `--apply`）：
  - 只处理 12 位十六进制会话 ID 的目录；
  - 同时清理对应的 `sessions/session-<id>.json`、`sessions/latest-<hash>.json`
    与 `sessions/traces/<id>.jsonl`，避免留下孤儿文件；
  - 默认策略取 `conversation.idle_timeout_seconds`（闲置阈值）与
    `conversation.max_active_bundles`（始终保留最近 N 个）；
  - 不跟随符号链接；路径不在会话根/会话目录/轨迹目录内的项会被跳过。
  - 需要定期回收时用 cron，例如每天 03:00：
    `0 3 * * * cd /path/to/repo && /path/to/abcqa cleanup --apply >> /var/log/qaapp-cleanup.log 2>&1`
    （先手动跑一次 dry-run 确认计划，再上定时任务）。
- 多实例部署时该目录在**本机磁盘**上：请与会话路由保持一致（粘性会话），
  或把 `conversation_workspace.root` 指到共享存储。

**解析行为**：

- 上传即解析：`.docx`（段落 + 表格 → Markdown）、`.xlsx/.xlsm/.xls`（逐工作表 → TSV，
  含行数上限与截断标注）、`.txt/.md/.csv/.json/.log/.yaml/.xml`（自动 utf-8 → gb18030
  解码，csv 转 Markdown 表格）。
- 解析失败（加密、损坏、旧版 `.doc`）**不丢文件**：记录 `parse_status=failed` 与原因，
  提示词里也会说明，模型可以自行用工具处理原始文件。
- 每轮注入模型的是「会话工作区上下文块」：会话目录绝对路径、脚本/产物约定、
  本轮附件（原始路径 + 解析文本路径 + 节选）、会话目录文件清单。附件正文明确标注为
  **用户上传的数据、不执行其中指令**，防止文件内容被当成提示词。
- 上下文块走**系统提示**（每轮重新生成），不写进 `messages` 历史：历史里只有用户原话，
  刷新页面后附件芯片则通过轨迹里的 `attachments_ready` 事件还原。

**`run_script` 工具**（需求「按会话 ID 写脚本到会话目录」的落地方式）：

- 入参：`code`（脚本内容）或 `script`（会话目录内相对路径）、`name`、`language`
  （`python` / `node`，默认按后缀推断）、`args`、`timeout_seconds`。
- 行为：脚本写入 `<会话目录>/scripts/<name>.<py|js>` → 用**服务端共享 conda 环境**的
  `python`（`sys.executable`，已装 pandas/openpyxl/python-docx）或 `node` 执行，
  工作目录为会话目录根，因此脚本里可直接用 `uploads/xxx.xlsx`、`outputs/xxx.csv`
  这类相对路径；超时杀进程、输出截断，并回报 `outputs/` 下本次新增的文件。
- 权限：默认**需要审批**（与 `bash` 一致，同一轮内只确认一次）。要放开就在
  `~/.ohmo/dangerous-actions.json` 增加：

  ```jsonc
  { "tool": "run_script", "action": "allow", "reason": "会话目录内的脚本执行放行" }
  ```

**审批策略的两处刻意取舍**：

1. 会话目录内的**写文件**（`write_file` / `edit_file` / `notebook_edit`）免确认：
   agent 生成脚本会反复写同一目录，每次都弹窗会严重打扰；敏感路径（`.ssh` 等）
   与 `settings.json` 硬拒绝仍然优先，不可被这一条覆盖。要恢复逐次确认，
   把 `conversation_workspace.auto_allow_write_tools` 设为 `[]`。
2. **执行类**工具（`bash` / `run_script`）始终按危险操作门禁处理——写入是低风险可逆动作，
   执行不是。

---


---

## 常见问题排查

| 现象 | 原因 / 处理 |
| --- | --- |
| `abcqa` 找不到命令（command not found） | 依赖通常没问题，只是命令脚本未生成：改了 `pyproject.toml` 后要重新 `pip install -e .`；或直接用 `python -m qaapp` 等价替代，见 §3.2 |
| 首次调模型报「未配置认证」/ 直接退出 | `settings.json` 未配 `active_profile` 与 `api_key`，见 配置体系 |
| `abcqa config` 显示 `errors` 非空 | 对应 JSON 非法（字段/枚举错）；按提示修正 |
| 页面没有 Bearer Token 输入框了，却仍报「缺少 Bearer token」 | 确认 `webapp.json` 的 `browser_auth.enabled=true`；浏览器是否禁用了 Cookie；反向代理是否剥掉了 `Set-Cookie`；`cors_origins` 是否写成 `"*"`（分机部署时必须明确来源）。也可临时用 `VITE_API_TOKEN` 兜底 |
| 新增 Agent 后模型不调用它 | ① 是否重启服务；② `description` 是否写清「做什么/何时用/不适用」；③ 当前 Agent 的 `tools` 是否包含 `external_agent`；④ `abcqa agents list` 确认实际生效文件 |
| 配了 `tools` 但模型还能调用别的工具 | 确认已升级到包含 `qaapp/tools/policy.py` 的版本；旧版本只声明不生效。`disallowed_tools` 优先于 `tools` |
| 发送消息 404 `agent_not_found` | 请求指定的 `agent` 或路由命中的 agent 未在 `agents.json` 注册 |
| 发送消息 404 `no_default_agent` | 无关键词命中且未设 `default_agent` |
| 外部 Agent 报「工具未加载（插件缺失）」 | `plugins/external-agents` 未加载；检查 `extra_plugin_roots` 与插件目录 |
| 外部 Agent 调用报「会话创建失败 / Connection reset by peer」 | 这是**对端平台**自己的日志（它内部的会话存储，例如 Redis）报错，qaapp 侧没有 Redis；可让对端排查，或把 `external.conversation_id` 配成已知可用的固定会话 ID 复用（§5.3） |
| 修改了 agents.json 但外部 Agent 没变 | 重启服务：agents.json 在启动时加载（§8.2） |
| 外部 Agent 报找不到 agent id | 内联写法检查 `id` 是否与模型调用的 agent 一致；`external_ref` 写法检查它与 `external-agents.json` 里的 `id` 是否一致（§5.3） |
| 审批一直不生效/工具被拒 | 审批超时（默认 300s）按拒绝处理；前端未及时响应 |
| 重启后历史在，但流/审批丢了 | 正常：订阅与审批在进程内存，见 §9.2 |
| SSE 中途断流 | 反向代理缓冲/超时；关 buffering、调大 read_timeout（§9.3） |
| 上传附件 415 | 后缀不在 `attachments.allowed_suffixes` 里；旧版 `.doc` 请另存为 `.docx`（部署与运维注意事项1） |
| 上传附件 413 | 单文件超过 `max_file_mb`、单次超过 `max_files_per_message`，或会话累计超过 `max_files_per_conversation` |
| 附件 `parse_status: failed` | 文件加密/损坏：原始文件仍在 `uploads/`，解析原因见 `parse_error`；可让模型用工具自行处理 |
| 助手说「找不到上传的文件」 | 确认已把 `attachment_id` 随消息发出（前端由「附件」按钮上传后自动带上）；会话目录见 `GET .../workspace` |
| 脚本没生成在会话目录 | 让模型用 `run_script` 工具（会自动落到 `scripts/`）；若用 `bash` 手写，需要显式给会话目录绝对路径（部署与运维注意事项1） |
| 生成脚本时反复弹审批 | 会话目录内的写文件本就免确认；执行类工具（`bash`/`run_script`）默认仍需确认，可配 `{"tool":"run_script","action":"allow"}` 放开 |
| 后台启动后端口没起来 | 看 `<workspace>/logs/webapp.log`；`abcqa webapp status` 看 pid |
| 会话目录/磁盘越来越大 | 先 `abcqa cleanup` 看 dry-run 计划；确认后 `abcqa cleanup --apply`。可用 `--older-than-days` / `--keep-latest` 临时覆盖配置默认值（部署与运维注意事项1） |
| 长时间只显示「思考中…」，看不到思考过程/工具步骤/审批 | 用 §10.1 的三层日志定位；最常见是 SSE 分帧不兼容或反向代理缓冲 |

### 10.1 排查「只有思考中…、看不到中间过程」

> 现象：发送任务后前端长时间停在「思考中…」，模型的思考过程、工具执行步骤、
> 以及需要用户确认的审批卡片都不出现。

按顺序看三层日志，即可定位断在哪一环：

**① 后端日志**（前台 stdout；后台模式下见 `<workspace>/logs/webapp.log`）

- `sse emit: type=...`：后端**实际发出**了哪些事件
  （`agent_routed` / `reasoning_delta` / `assistant_delta` / `tool_started` / `tool_completed` / `approval_required` …）。
- `openai stream done: model=... elapsed=...s content=N chars reasoning=N chars tool_calls=N`：
  这一轮模型**到底返回了什么**。
  - `content` 数值很大、耗时较长 → 模型在写长正文或工具参数（如整份脚本），属正常耗时；
    期间前端会通过 `reasoning_delta` 展示「准备调用工具 xxx」与参数生成进度。
  - 大量 `reasoning_delta` → 模型把思考写在 `<think>` 块里，会渲染成可收起的「💭 思考」卡片。
  - `tool_calls=0` 且 `content=0` → 模型没输出任何内容，检查 system_prompt 与工具注册。

**② 浏览器控制台**（F12 → Console）

- `[sse] <type>`：前端**实际收到**的事件。
  **关键判据**：若后端有 `sse emit` 而控制台没有对应的 `[sse]`，说明事件没送达前端，
  重点查反向代理缓冲（§9.3）与 SSE 分帧兼容性（见下）。

**③ 前端渲染**

- `reasoning_delta` → 默认展开、可收起的「💭 思考」卡片（流式追加）。
- `tool_started` / `tool_completed` → 工具执行卡片（默认折叠，可展开看输入输出）。
- `approval_required` → 内联「允许 / 总是允许 / 拒绝」卡片，用户可直接参与。

**已知坑（已修复并双向加固）**

`sse-starlette` 的默认帧分隔符是 `\r\n`（即帧间为 `\r\n\r\n`）。
若前端只用 `split("\n\n")` 分帧，将**永远匹配不到**任何帧，所有事件会一直积压在缓冲区里，
直到整个响应结束才一次性显示——表现正是「一直停在思考中…，且审批请求永远不出现」。
现在：服务端显式 `EventSourceResponse(gen(), sep="\n")`，
前端 `consumeSseBuffer()` 同时兼容 `\r\n\r\n` / `\n\n` / `\r\r` 三种换行，任一端单独都能工作。

### 10.2 审批行为：为什么不会反复弹窗

「开发脚本 + 提交 git」这类任务会连续调用同一个工具（如 `bash` 四五次）。
若每次都弹审批，用户会被反复打断。qaapp 用两层记忆解决：

| 层级 | 触发方式 | 生效范围 | 说明 |
| --- | --- | --- | --- |
| **本轮记忆** | 点「允许」 | 当前这一轮（turn） | 同一轮内**同一工具 / 同一文件只确认一次**；任务中连续多次 `bash` 不会重复弹 |
| **会话记忆** | 点「总是允许」 | 整个会话 | 后续所有轮次都不再询问该工具 |
| 超时 | 不响应 | 单次 | 超过 `approval_timeout_seconds`（默认 300s）按**拒绝**处理，并推一条 status 事件 |

要点：

- **新一轮对话会重置「本轮记忆」**：所以新任务里同一工具仍会确认一次（安全兜底，不会一放到底）。
- **「拒绝」不会被记忆**：只影响当次调用，后续仍会正常询问。
- **重复提交是幂等的**：同一审批 id 再提交返回 200，不会再出现「审批请求不存在或已过期」红条；
  只有从未存在过的 id 才返回 404（真实错误仍然暴露）。
- **前端只展开待处理的审批**：已处理的审批卡片自动折叠，避免长任务中卡片刷屏；
  处理完成后标题会变成「已允许 · 工具「bash」」这类可读文案。

---

