# OpenHarness 定制化说明

本文档说明本次二开的两个定制点：**模型配置非交互化** 与 **启动画面品牌化（logo/产品名可配置）**。

---

## 1. 模型配置非交互化

模型 / Provider 配置不再通过交互式问答获取，统一由 `config/backend/.env` 承载。

### 1.1 配置载体（优先级从高到低）

1. **进程环境变量**
2. **`config/backend/.env`**（仓库内唯一配置来源；`abc` CLI 与 Web 服务共用）
3. **`~/.openharness/settings.json`**（可选的高级结构化配置）
4. **默认值**

### 1.2 配置文件

编辑 `config/backend/.env`（由模板复制：`cp .env_dev .env`）：

```bash
OPENHARNESS_PROFILE=dev
OPENHARNESS_PROVIDER=openai
OPENHARNESS_API_FORMAT=openai
OPENHARNESS_MODEL=deepseek-chat
OPENHARNESS_BASE_URL=https://api.deepseek.com/v1
OPENHARNESS_AUTH_SOURCE=openai_api_key
OPENHARNESS_OPENAI_API_KEY=sk-...
```

> 安全建议：密钥只放 `.env`（已 gitignore）或进程环境变量；
> `OPENHARNESS_AUTH_SOURCE` 填来源名（`openai_api_key` 等），不要填密钥本身。

### 1.3 环境变量

| 变量 | 作用 |
|---|---|
| `OPENHARNESS_PROFILE` | 激活的 profile 名 |
| `OPENHARNESS_MODEL` | 模型名 |
| `OPENHARNESS_BASE_URL` | 接口地址 |
| `OPENHARNESS_PROVIDER` | provider 标识 |
| `OPENHARNESS_API_FORMAT` | `anthropic` / `openai` / `copilot` |
| `OPENHARNESS_AUTH_SOURCE` | 凭据来源名（如 `openai_api_key`） |
| `OPENHARNESS_API_KEY` | 直接指定密钥（优先级最高） |
| `OPENHARNESS_MAX_TOKENS` / `OPENHARNESS_MAX_TURNS` | 输出 / 轮次上限 |
| `OPENHARNESS_ALLOW_PROJECT_PLUGINS` / `OPENHARNESS_PROJECT_SKILL_DIRS` | 项目插件 / 技能目录 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等 | 各 provider 的 API key |

### 1.4 非交互校验命令

```bash
abc setup                 # 读取并校验当前配置（不再问答）
abc setup <profile>       # 校验指定 profile
abc config show           # 打印解析后的完整设置
```

`abc setup` 会打印 `Resolved model configuration` 与 `Status: READY / NOT READY`，
未就绪时给出配置示例与下一步提示。

`abcag` 侧同理：

```bash
abcag config                    # 查看 abcag provider/gateway 配置（不再问答）
abcag config --profile <name>   # 非交互切换 provider profile
abcag init                      # 初始化 workspace（不再触发向导）
```

---

## 2. 启动画面品牌化（logo / 产品名可配置）

启动画面的 ASCII 大字 logo 已替换为**可配置的纯文本产品名 + 副标题**，默认值：

- 产品名：`abc helper`
- 副标题：`An AI-powered coding assistant`

### 2.1 配置方式

写在 `config/backend/.env`（或进程环境变量，优先级更高）：

```bash
OPENHARNESS_PRODUCT_NAME="ABC Tech"
OPENHARNESS_SLOGAN="企业级AI助手"
```

需要时也可写在用户级 `~/.openharness/settings.json` 的 `branding` 字段中；
环境变量始终优先。

`abc` 与 `abcag` 的交互式启动画面都会读取同一份 branding 配置。

---

## 3. 改动文件清单

### 后端（Python）

| 文件 | 改动 |
|---|---|
| `src/openharness/config/settings.py` | 新增 `BrandingSettings`（`product_name` / `slogan`）与 `Settings.branding` 字段；`_apply_env_overrides` 支持 `OPENHARNESS_PRODUCT_NAME` / `OPENHARNESS_SLOGAN` |
| `src/openharness/ui/react_launcher.py` | 新增 `_resolve_branding()`，将 branding 写入 `OPENHARNESS_FRONTEND_CONFIG` |
| `src/openharness/cli.py` | `setup_cmd` 改为非交互：读配置 + 校验 + 打印，不再问答 |
| `ohmo/runtime.py` | `launch_abcag_react_tui` 传递 branding |
| `ohmo/cli.py` | `config_cmd` / `init_cmd` 改为非交互 |

### 前端（TypeScript / React）

| 文件 | 改动 |
|---|---|
| `frontend/terminal/src/types.ts` | `FrontendConfig` 增加 `theme` / `branding` 字段 |
| `frontend/terminal/src/components/WelcomeBanner.tsx` | 移除硬编码 ASCII logo 与版本号，改为渲染 `product_name` + `slogan` |
| `frontend/terminal/src/components/ConversationView.tsx` | 传递 branding props |
| `frontend/terminal/src/App.tsx` | 从 `config.branding` 读取并下发 |

---

## 4. 验证

```bash
# 后端（需在已安装 openharness 的环境，如 conda env `openharness`）
abc setup                          # 非交互校验
abc --dry-run -p "hello"           # 确认 dry-run 未受影响
abcag config                       # 非交互查看

# 前端类型检查
cd frontend/terminal
npx tsc --noEmit
```

> 前端改动后，交互式 `abc` 会自动 `npm install` 并以 `tsx` 启动，无需重新构建；若已手动构建过，请重新 `npm run build`。
