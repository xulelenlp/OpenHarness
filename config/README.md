# 项目配置（唯一配置根目录）

本目录是 OpenHarness 所有**项目级配置**的唯一入口：

```text
config/
├── backend/                  # 后端：OpenHarness + qaapp
│   ├── base/                 # 低优先级默认值（提交 git）
│   ├── dev/                  # 开发环境覆盖（提交 git）
│   ├── prod/                 # 生产环境覆盖（提交 git）
│   ├── local/                # 本机覆盖（gitignore）
│   ├── .env                  # 公共非敏感变量
│   ├── .env.dev              # 开发环境变量
│   ├── .env.prod             # 生产环境变量模板
│   └── .env.local            # 本机/部署密钥（gitignore）
└── frontend/                 # 前端：Vite
    ├── .env                  # 公共品牌与端口
    ├── .env.development      # 开发代理
    ├── .env.production       # 生产直连地址
    └── .env.local            # 本机覆盖（gitignore）
```

## 1. 后端加载顺序

JSON 配置（`settings.json`、`webapp.json`、`agents.json`、`dangerous-actions.json`、`datasources.json`）：

1. `config/backend/base/<name>.json`
2. `config/backend/<QAAPP_ENV>/<name>.json`
3. `config/backend/local/<name>.json`
4. 进程环境变量（最高优先级，用于 `${VAR}` 占位符）

- 对象递归合并；数组和标量整体替换。
- `QAAPP_ENV` 默认 `dev`，生产设置为 `prod`。
- 旧部署仍可读取 `<cwd>/config/<name>.json` 与 `~/.ohmo/<name>.json`；新部署请使用分层目录。

环境变量文件：

1. `config/backend/.env`
2. `config/backend/.env.<QAAPP_ENV>`
3. `config/backend/.env.local`
4. 进程环境变量（最高优先级）

`config/backend/.env.dev` 与 `.env.prod` 会设置 `OPENHARNESS_PROFILE`，用于选择
`base/settings.json` 里的 `profiles.dev` / `profiles.prod`。

## 2. 前端加载顺序

Vite 的 `envDir` 已指向 `config/frontend/`，自动加载：

1. `.env`
2. `.env.<mode>`（`development` / `production`）
3. `.env.local`（最高优先级，gitignore）

常用变量：

| 变量 | 说明 |
| --- | --- |
| `VITE_API_TARGET` | 开发/预览时代理的后端地址 |
| `VITE_API_BASE_URL` | 生产浏览器直连后端基址；留空 = 同源 |
| `VITE_API_TOKEN` | 仅关闭 browser_auth 时使用，会被打进前端产物 |
| `VITE_DEV_PORT` | Vite 开发端口（默认 5173） |
| `VITE_APP_NAME` / `VITE_APP_SUBTITLE` / `VITE_PRODUCT_NAME` / `VITE_APP_DESCRIPTION` | 品牌文案 |

## 3. 开发 / 生产切换

```bash
# 开发（默认）
QAAPP_ENV=dev python -m qaapp webapp run

# 生产
QAAPP_ENV=prod python -m qaapp webapp run

# 查看实际生效的层级与文件
QAAPP_ENV=prod python -m qaapp config
```

只改配置即可切换：

- 服务端口 / 监听地址 / 静态目录 / CORS / 鉴权：`config/backend/{base,dev,prod}/webapp.json`
- 模型 / Provider：`config/backend/base/settings.json` 的 `profiles` + `config/backend/.env.*`
- 外部 Agent 端点：`config/backend/base/agents.json` 的 `${...}` 占位符 + `config/backend/.env.local`
- 数据库：`config/backend/base/datasources.json` 的 `${DB_*}` + `config/backend/.env.local`

## 4. 密钥规则

- **不要**把 API Key、数据库密码、`QAAPP_API_TOKEN` 写入 `base/` 或 `dev/`/`prod/`。
- 真实值只放 `config/backend/.env.local`（已 gitignore），或部署平台的进程环境变量。
- 模板见 `config/backend/.env.local.example`。
- 历史提交中如出现过明文 key，请视为已泄露并轮换。

## 5. 迁移速查

| 旧位置 | 新位置 |
| --- | --- |
| `.openharness/settings.json` | `config/backend/base/settings.json` |
| `config/agents.json` | `config/backend/base/agents.json` |
| `config/datasources.json` | `config/backend/base/datasources.json` |
| `qaapp/examples/webapp.json` | `config/backend/base/webapp.json` |
| `qaapp/examples/dangerous-actions.json` | `config/backend/base/dangerous-actions.json` |
| 根 `.env` | `config/backend/.env.local` |
| `frontend/webapp/.env.*` | `config/frontend/.env*` |
