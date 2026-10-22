# OpenHarness 全局配置维护指南

本文档列出一份完整的配置文件地图，方便你全局统一维护项目配置。

> 环境切换：开发 `cp config/backend/.env_dev config/backend/.env`，生产
> `cp config/backend/.env_prod config/backend/.env`。`.env` 是唯一环境文件，
> 不再做 base/dev/prod/local 分层合并。前端同理：`config/frontend/.env_dev` /
> `.env_prod` → `.env`。

---

## 1. 配置文件地图（总览）

| 配置文件 | 默认位置 | 作用 | 维护方式 |
|---|---|---|---|
| **主配置（唯一）** | `config/backend/.env` | 模型 / Provider / 密钥 / 品牌 / 运行参数 / Web 服务 / 数据源 | `cp .env_dev .env` 后编辑 |
| **高级设置（可选）** | `~/.openharness/settings.json` | 权限 / Hooks / 记忆 / 沙箱 / Web / MCP / UI 等结构化配置 | 一般无需维护，需要时手工编辑 |
| **凭证（可选）** | `~/.openharness/credentials.json` | `abc auth login` 管理的 API key（权限 `600`） | `abc auth login` 管理 |
| **ohmo gateway** | `~/.ohmo/gateway.json` | personal agent 的 Provider / 渠道 / 会话路由 / 权限 | 手工编辑 + `abcag config` 校验 |
| **ohmo 人格** | `~/.ohmo/soul.md` / `identity.md` / `user.md` / `BOOTSTRAP.md` | agent 的人设、身份、用户偏好、引导词 | 手工编辑 |
| **项目级** | `<project>/.openharness/` | issue.md、pr_comments.md、autopilot 状态与策略 (yaml) | 运行时生成 |
| **用户级扩展** | `~/.openharness/skills/`、`~/.openharness/plugins/` | 用户级技能 / 插件 | 手工放置目录 |
| **运行时数据** | `~/.openharness/data/`、`logs/` | sessions、tasks、cron_jobs.json、日志 | **自动生成，无需维护** |

> 目录可整体迁移：设置 `OPENHARNESS_CONFIG_DIR` / `OPENHARNESS_DATA_DIR` /
> `OPENHARNESS_LOGS_DIR`、`OHMO_WORKSPACE` 指向任意位置。
> 仓库**不再提交** `config/backend/settings.json`：模型配置全部收敛到 `.env`。

---

## 2. 主配置 `config/backend/.env`

`.env` 是唯一配置来源。qaapp 启动时把 `.env` 注入进程环境，`abc` CLI 也会自动读取
同一份文件；进程环境变量优先级最高。dev / prod 切换 = 复制不同模板后重启：

```bash
cp config/backend/.env_dev  config/backend/.env   # 开发
cp config/backend/.env_prod config/backend/.env   # 生产
```

### 2.1 模型 / Provider 字段

| 变量 | 说明 |
|---|---|
| `OPENHARNESS_PROFILE` | profile 名（标识用；`webapp.json:provider_profile` 为空时继承） |
| `OPENHARNESS_PROVIDER` | Provider 标识（OpenAI 兼容网关填 `openai`） |
| `OPENHARNESS_API_FORMAT` | `anthropic` / `openai` / `copilot` |
| `OPENHARNESS_MODEL` | 默认模型 |
| `OPENHARNESS_BASE_URL` | 接口地址（OpenAI 兼容端点填此处） |
| `OPENHARNESS_AUTH_SOURCE` | 凭据来源名（`dashscope_api_key` / `openai_api_key`）；**不能填密钥本身** |
| `OPENHARNESS_DASHSCOPE_API_KEY` / `OPENHARNESS_OPENAI_API_KEY` | 对应来源的密钥 |
| `OPENHARNESS_API_KEY` | 直接指定密钥（优先级最高） |
| `OPENHARNESS_MAX_TOKENS` / `OPENHARNESS_TIMEOUT` / `OPENHARNESS_MAX_TURNS` | 输出 / 超时 / 轮次 |
| `OPENHARNESS_PRODUCT_NAME` / `OPENHARNESS_SLOGAN` | 品牌化 |
| `OPENHARNESS_ALLOW_PROJECT_PLUGINS` / `OPENHARNESS_PROJECT_SKILL_DIRS` | 项目插件开关 / 技能目录 |

最小示例（完整模板见 `config/backend/.env_dev`）：

```bash
OPENHARNESS_PROFILE=dev
OPENHARNESS_PROVIDER=openai
OPENHARNESS_API_FORMAT=openai
OPENHARNESS_MODEL=deepseek-flash
OPENHARNESS_BASE_URL=http://ai.ac-energytech.com/v1
OPENHARNESS_AUTH_SOURCE=dashscope_api_key
OPENHARNESS_DASHSCOPE_API_KEY=sk-...
```

### 2.2 多环境 / 多 Provider

- 一个环境一份 `.env`；新增环境就新增一份模板，不再维护 JSON `profiles`。
- 需要临时切换 provider 时改 `.env` 后重启；`abc provider use` 仍会写用户级
  `~/.openharness/settings.json`，但已有 `.env` 的 `OPENHARNESS_*` 会覆盖它。
- 因为 profile 由环境变量动态定义，`webapp.json:provider_profile` 留空即可。

### 2.3 高级结构化配置（可选）

权限 / Hooks / 记忆 / 沙箱 / MCP / UI 等字段不再提供仓库模板，默认值即可用；
需要定制时在 `~/.openharness/settings.json` 维护（`abc config set` 也可写入），
`OPENHARNESS_*` 环境变量始终优先。qaapp 的数据源 / Agent / 高危策略仍分别由
`datasources.json` / `agents.json` / `dangerous-actions.json` 维护。

---

## 3. API key

| 优先级 | 方式 | 位置 |
|---|---|---|
| 1 | `OPENHARNESS_API_KEY` | `.env`（最直接） |
| 2 | `credential_slot` | `credentials.json` 的 `profile:<slot>`（仅 profile 配置了该字段） |
| 3 | `OPENHARNESS_AUTH_SOURCE` 指向的内置变量 | 如 `dashscope_api_key` → `OPENHARNESS_DASHSCOPE_API_KEY`，也兼容 `DASHSCOPE_API_KEY` |
| 4 | `abc auth login` | `~/.openharness/credentials.json`（权限 `600`） |
| 5 | 用户级 settings.json 的 `profiles.<name>.api_key` | 可选，不推荐 |

```bash
# 方式一：来源名 + 对应变量
OPENHARNESS_AUTH_SOURCE=dashscope_api_key
OPENHARNESS_DASHSCOPE_API_KEY=sk-...

# 方式二：直接给密钥（优先级最高）
OPENHARNESS_API_KEY=sk-...
```

> `OPENHARNESS_AUTH_SOURCE` 只填来源名。把密钥本身填进去会报
> `No credentials found for auth source ...`（报错中已对密钥脱敏）。

---

## 4. ohmo（personal agent）配置

| 文件 | 说明 |
|---|---|
| `~/.ohmo/gateway.json` | `provider_profile`（provider 标识，模型配置见 `.env`）、`enabled_channels`、`session_routing`、`permission_mode`、`channel_configs` 等 |
| `~/.ohmo/soul.md` / `identity.md` / `user.md` / `BOOTSTRAP.md` | agent 人设与引导词 |

校验/切换命令：

```bash
abcag config                       # 查看当前 gateway 配置
abcag config --profile <name>      # 非交互切换 provider profile
```

---

## 5. 环境变量（优先级最高，覆盖 settings.json）

| 变量 | 作用 |
|---|---|
| `OPENHARNESS_CONFIG_DIR` | 重定向配置目录 |
| `OPENHARNESS_MODEL` / `OPENHARNESS_BASE_URL` / `OPENHARNESS_PROVIDER` / `OPENHARNESS_API_FORMAT` / `OPENHARNESS_PROFILE` | 模型 / Provider 覆盖 |
| `OPENHARNESS_AUTH_SOURCE` / `OPENHARNESS_API_KEY` | 凭据来源名 / 直接密钥 |
| `OPENHARNESS_ALLOW_PROJECT_PLUGINS` / `OPENHARNESS_PROJECT_SKILL_DIRS` | 项目插件开关 / 技能目录 |
| `OPENHARNESS_MAX_TOKENS` / `OPENHARNESS_MAX_TURNS` / `OPENHARNESS_TIMEOUT` | 输出 / 轮次 / 超时 |
| `OPENHARNESS_PRODUCT_NAME` / `OPENHARNESS_SLOGAN` | 品牌化覆盖 |
| `OPENHARNESS_SANDBOX_ENABLED` / `OPENHARNESS_SANDBOX_BACKEND` | 沙箱 |
| `OPENHARNESS_WEB_PROXY` / `OPENHARNESS_WEB_RESOLUTION_MODE` | 网络代理 |
| `OPENHARNESS_WEB_SEARCH_URL` / `OPENHARNESS_WEB_SYNTHETIC_DNS_CIDRS` | 搜索端点覆盖（默认 `https://cn.bing.com/search`）/ 合成 DNS 网段 |
| `OPENHARNESS_LOGS_DIR` / `OPENHARNESS_LOG_LEVEL` | 日志目录（默认 `~/.openharness/logs`，其中 `web.log` 记录每次出网结果）/ 日志级别 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENHARNESS_*_API_KEY` 等 | 各 Provider 凭证 |
| `OHMO_WORKSPACE` | ohmo workspace 目录 |

### 5.1 `web_search` / `web_fetch` 出网排查

- 默认搜索端点是 **Bing**（`https://cn.bing.com/search`，可用 `web.search_url` / `OPENHARNESS_WEB_SEARCH_URL` 覆盖）。DuckDuckGo 已移除：其 HTML 端点在中国大陆网络不可达，且域名常被 DNS 污染成假地址。
- 每次出网都会往 `<logs>/web.log`（默认 `~/.openharness/logs/web.log`，`OPENHARNESS_LOGS_DIR` 可改）追加一行：`event=ok/failed`、`endpoint`、`q`、`elapsed`、`status`、`type`、`message`、`args`、`cause`、`dns`、`detail`。失败行是 WARNING，可直接从服务器下载。
- 一键复现网络状况：`python scripts/diag_web.py --report /tmp/web-diag.txt`。
- 典型现象：失败信息形如 `ConnectTimeout｜原始错误信息为空｜目标 …:443｜耗时 20.0s｜代理 未配置｜原因 ConnectTimeout <- TimeoutError()` → 表示该机器无法直达目标地址，请配置 `web.proxy` / `OPENHARNESS_WEB_PROXY`，或把 `web.search_url` 指向可达端点。

---

## 6. 配置优先级

```
CLI 参数  >  进程环境变量  >  config/backend/.env  >  ~/.openharness/settings.json（可选高级配置）  >  默认值
```

启动命令本身也有别名优先级（模块入口保留用于内部派生）：

| 入口 | 说明 |
|---|---|
| `abc` | 编码助手（console script） |
| `abcag` | personal agent（console script） |
| `python -m openharness` / `python -m ohmo` | 模块入口（内部 spawn / cron 依赖） |

---

## 7. 维护命令速查

```bash
abc setup                 # 校验当前配置并打印 READY / NOT READY
abc setup <profile>       # 校验指定 profile
abc config show           # 打印解析后的完整设置 JSON
abc config set branding.slogan "新的副标题"   # 写入一条设置（支持点分嵌套键）
abc provider list         # 查看 Provider profiles
abc auth status           # 查看凭证状态
abcag config              # 查看 ohmo gateway 配置
```