# OpenHarness 全局配置维护指南

本文档列出一份完整的配置文件地图，方便你全局统一维护项目配置。

---

## 1. 配置文件地图（总览）

| 配置文件 | 默认位置 | 作用 | 维护方式 |
|---|---|---|---|
| **主配置** | 项目内 `.openharness/settings.json`（**优先**）；否则 `~/.openharness/settings.json` | 模型 / Provider / **API key** / 品牌化 / 权限 / Hooks / 记忆 / 沙箱 / Web / MCP / UI 等**全部设置** | 手工编辑 + `abc setup` 校验 |
| **凭证（可选）** | `~/.openharness/credentials.json` | 命令管理的 API key（权限 `600`）；也可不写此文件，把 key 放 settings.json | `abc auth login` 管理 |
| **ohmo gateway** | `~/.ohmo/gateway.json` | personal agent 的 Provider / 渠道 / 会话路由 / 权限 | 手工编辑 + `abcag config` 校验 |
| **ohmo 人格** | `~/.ohmo/soul.md` / `identity.md` / `user.md` / `BOOTSTRAP.md` | agent 的人设、身份、用户偏好、引导词 | 手工编辑 |
| **项目级** | `<project>/.openharness/` | `settings.json`（可随仓库版本化的主配置）、issue.md、pr_comments.md、autopilot 状态与策略 (yaml) | 手工编辑 + 运行时生成 |
| **用户级扩展** | `~/.openharness/skills/`、`~/.openharness/plugins/` | 用户级技能 / 插件 | 手工放置目录 |
| **运行时数据** | `~/.openharness/data/`、`logs/` | sessions、tasks、cron_jobs.json、日志 | **自动生成，无需维护** |

> 目录可整体迁移：设置 `OPENHARNESS_CONFIG_DIR` / `OPENHARNESS_DATA_DIR` / `OPENHARNESS_LOGS_DIR`、`OHMO_WORKSPACE` 指向任意位置（例如团队共享目录或 Git 仓库）。也可直接把 `.openharness/settings.json` 随仓库提交，克隆到新机器即可复用同一份配置。

---

## 2. 主配置文件（项目内 `.openharness/settings.json` 优先，否则 `~/.openharness/settings.json`）

这是**唯一的核心配置文件**，承载绝大多数全局设置。放在项目（Git 仓库）里时随代码一起迁移，实现“集中管理、快速部署”。完整可复制模板见 [`docs/settings.example.json`](./settings.example.json)。

### 2.1 字段分组速查

| 分组 | 关键字段 | 说明 |
|---|---|---|
| **API / 模型** | `active_profile`、`profiles`、`model`、`max_tokens`、`base_url`、`api_format`、`provider`、`timeout`、`max_turns` | 模型接入；`active_profile` 指向 `profiles` 中的某个 profile |
| **品牌化** | `branding.product_name`、`branding.slogan` | 启动画面产品名 + 副标题 |
| **权限** | `permission.mode`、`permission.allowed_tools`、`denied_tools`、`denied_commands`、`path_rules` | 工具/命令/路径权限 |
| **Hooks** | `hooks` | 事件钩子（按事件名分组） |
| **记忆** | `memory.enabled`、`context_window_tokens`、`auto_compact_threshold_tokens`、`session_memory_enabled`、`auto_dream_enabled` | 记忆与自动压缩 |
| **沙箱** | `sandbox.enabled`、`sandbox.backend`、`sandbox.docker` | 命令执行沙箱 |
| **Web** | `web.proxy`、`web.resolution_mode` | 网络访问 / 代理 |
| **扩展** | `enabled_plugins`、`mcp_servers`、`allow_project_plugins`、`allow_project_skills`、`project_skill_dirs` | 插件、MCP 服务器、项目级技能 |
| **UI** | `theme`、`output_style`、`vim_mode`、`voice_mode`、`fast_mode`、`effort`、`passes` | 界面与运行行为 |
| **视觉/绘图** | `vision`、`image_generation` | 图像理解 / 生图模型 |

### 2.2 `profiles`（多 Provider 接入）

`settings.json` 内置一个 `profiles` 表，可同时维护多个 Provider，用 `active_profile` 切换。每个 profile 的关键字段：

| 字段 | 说明 |
|---|---|
| `label` | 显示名 |
| `provider` | Provider 标识（如 `openai` / `anthropic` / `copilot`） |
| `api_format` | `anthropic` / `openai` / `copilot` |
| `auth_source` | 凭证来源（如 `openai_api_key` / `anthropic_api_key`） |
| `default_model` | 默认模型 |
| `base_url` | 接口地址（OpenAI 兼容端点填此处） |
| `credential_slot` | 凭证在 `credentials.json` 中的槽位（可选） |
| `api_key` | API key 直接写在 settings.json（profile 级，可选） |
| `allowed_models` | 限制可切换的模型列表 |

---

## 3. API key（推荐直接写进 settings.json）

API key 按以下优先级读取（从高到低）：

| 优先级 | 方式 | 位置 |
|---|---|---|
| 1 | `credential_slot` | `credentials.json` 的 `profile:<slot>`（仅当 profile 配置了该字段） |
| 2 | 环境变量 | `OPENHARNESS_<PROVIDER>_API_KEY` 或 `<PROVIDER>_API_KEY`（如 `DASHSCOPE_API_KEY`） |
| 3 | **profile 级 `api_key`** | `settings.json` 的 `profiles.<name>.api_key`（**推荐，一个文件搞定**） |
| 4 | 顶层 `api_key` | `settings.json` 顶层（向后兼容，多 profile 时不推荐） |
| 5 | provider 槽 | `~/.openharness/credentials.json`（`abc auth login` 管理） |

**推荐做法**——把 key 直接写进 `settings.json` 的 profile，部署后只读这一个文件：

```json
{
  "active_profile": "qwen",
  "profiles": {
    "qwen": {
      "label": "Qwen (DashScope)",
      "provider": "dashscope",
      "api_format": "openai",
      "auth_source": "dashscope_api_key",
      "default_model": "qwen-plus",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk-your-key"
    }
  }
}
```

> 安全提示：key 明文存放在本机；用 `OPENHARNESS_CONFIG_DIR` 指向固定目录即可整体迁到新机器。若需团队共享，建议改用环境变量或 `abc auth login` 存 `credentials.json`（`600` 权限）。

---

## 4. ohmo（personal agent）配置

| 文件 | 说明 |
|---|---|
| `~/.ohmo/gateway.json` | `provider_profile`（指向 settings.json 的 profile）、`enabled_channels`、`session_routing`、`permission_mode`、`channel_configs` 等 |
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
| `OPENHARNESS_MAX_TOKENS` / `OPENHARNESS_MAX_TURNS` / `OPENHARNESS_TIMEOUT` | 输出 / 轮次 / 超时 |
| `OPENHARNESS_PRODUCT_NAME` / `OPENHARNESS_SLOGAN` | 品牌化覆盖 |
| `OPENHARNESS_SANDBOX_ENABLED` / `OPENHARNESS_SANDBOX_BACKEND` | 沙箱 |
| `OPENHARNESS_WEB_PROXY` / `OPENHARNESS_WEB_RESOLUTION_MODE` | 网络代理 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENHARNESS_*_API_KEY` 等 | 各 Provider 凭证 |
| `OHMO_WORKSPACE` | ohmo workspace 目录 |

---

## 6. 配置优先级

```
CLI 参数  >  环境变量  >  项目 settings.json (.openharness/settings.json)  >  用户 settings.json (~/.openharness/settings.json)  >  默认值
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