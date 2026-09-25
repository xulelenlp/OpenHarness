# OpenHarness 定制化说明

本文档说明本次二开的两个定制点：**模型配置非交互化** 与 **启动画面品牌化（logo/产品名可配置）**。

---

## 1. 模型配置非交互化

模型 / Provider 配置不再通过交互式问答获取，而是统一由配置文件 + 环境变量承载。

### 1.1 配置载体（优先级从高到低）

1. **环境变量**
2. **`~/.openharness/settings.json`**（`OPENHARNESS_CONFIG_DIR` 可覆盖根目录）
3. **默认值**

### 1.2 配置文件

编辑 `~/.openharness/settings.json`，参考 [`docs/settings.example.json`](./settings.example.json)：

```json
{
  "active_profile": "openai-compatible",
  "profiles": {
    "openai-compatible": {
      "label": "DeepSeek",
      "provider": "openai",
      "api_format": "openai",
      "auth_source": "openai_api_key",
      "default_model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1"
    }
  }
}
```

> 安全建议：API key 不要写进 `settings.json`，而是通过环境变量（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`，或各 provider 专属变量）注入，或使用 `oh auth login` 存入 `~/.openharness/credentials.json`（`chmod 600`）。

### 1.3 环境变量

| 变量 | 作用 |
|---|---|
| `OPENHARNESS_PROFILE` | 激活的 profile |
| `OPENHARNESS_MODEL` | 模型名 |
| `OPENHARNESS_BASE_URL` | 接口地址 |
| `OPENHARNESS_PROVIDER` | provider 标识 |
| `OPENHARNESS_API_FORMAT` | `anthropic` / `openai` / `copilot` |
| `OPENHARNESS_MAX_TOKENS` / `OPENHARNESS_MAX_TURNS` | 输出 / 轮次上限 |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等 | 各 provider 的 API key |

### 1.4 非交互校验命令

```bash
oh setup                 # 读取并校验当前配置（不再问答）
oh setup <profile>       # 校验指定 profile
```

`oh setup` 会打印 `Resolved model configuration` 与 `Status: READY / NOT READY`，未就绪时给出配置文件示例与下一步提示。

`ohmo` 侧同理：

```bash
ohmo config                    # 查看 ohmo provider/gateway 配置（不再问答）
ohmo config --profile <name>   # 非交互切换 provider profile
ohmo init                      # 初始化 workspace（不再触发向导）
```

---

## 2. 启动画面品牌化（logo / 产品名可配置）

启动画面的 ASCII 大字 logo 已替换为**可配置的纯文本产品名 + 副标题**，默认值：

- 产品名：`ABC Tech`
- 副标题：`An AI-powered coding assistant`

### 2.1 配置方式

在 `settings.json` 顶层加 `branding` 键：

```json
{
  "branding": {
    "product_name": "ABC Tech",
    "slogan": "企业级编码助手"
  }
}
```

或使用环境变量（优先级高于 `settings.json`）：

```bash
export OPENHARNESS_PRODUCT_NAME="ABC Tech"
export OPENHARNESS_SLOGAN="企业级编码助手"
```

`oh` 与 `ohmo` 的交互式启动画面都会读取同一份 branding 配置。

---

## 3. 改动文件清单

### 后端（Python）

| 文件 | 改动 |
|---|---|
| `src/openharness/config/settings.py` | 新增 `BrandingSettings`（`product_name` / `slogan`）与 `Settings.branding` 字段；`_apply_env_overrides` 支持 `OPENHARNESS_PRODUCT_NAME` / `OPENHARNESS_SLOGAN` |
| `src/openharness/ui/react_launcher.py` | 新增 `_resolve_branding()`，将 branding 写入 `OPENHARNESS_FRONTEND_CONFIG` |
| `src/openharness/cli.py` | `setup_cmd` 改为非交互：读配置 + 校验 + 打印，不再问答 |
| `ohmo/runtime.py` | `launch_ohmo_react_tui` 传递 branding |
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
oh setup                          # 非交互校验
oh --dry-run -p "hello"           # 确认 dry-run 未受影响
ohmo config                       # 非交互查看

# 前端类型检查
cd frontend/terminal
npx tsc --noEmit
```

> 前端改动后，交互式 `oh` 会自动 `npm install` 并以 `tsx` 启动，无需重新构建；若已手动构建过，请重新 `npm run build`。
