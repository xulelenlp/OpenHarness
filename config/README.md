# 项目配置

前后端各只使用一个环境配置文件 `.env`。开发/生产切换 = 复制模板后重启：

```bash
# 开发
cp config/backend/.env_dev  config/backend/.env
cp config/frontend/.env_dev config/frontend/.env

# 生产
cp config/backend/.env_prod  config/backend/.env
cp config/frontend/.env_prod config/frontend/.env
```

`.env` 已在 `.gitignore` 中，不会提交；`.env_dev` / `.env_prod` 是唯一需要维护的模板。

## 后端 `config/backend/`

| 文件 | 作用 |
| --- | --- |
| `.env_dev` / `.env_prod` | 开发 / 生产模板；复制为 `.env` 后生效 |
| `.env` | 当前环境配置（本机生成，gitignore） |
| `settings.json` | OpenHarness 模型 profile / 权限 / 沙箱等非环境配置 |
| `agents.json` | Agent 注册表；端点用 `${...}`，值来自 `.env` |
| `dangerous-actions.json` | 高危操作策略 |
| `datasources.json` | MySQL/Redis 模板；连接值用 `${DB_*}` / `${REDIS_*}`，值来自 `.env` |

只读取一个 `.env`，没有 base/dev/prod/local 分层合并。`.env` 中可配置：

- 模型：`OPENHARNESS_PROFILE`、`OPENHARNESS_DASHSCOPE_API_KEY` / `OPENHARNESS_OPENAI_API_KEY`
- Web：`QAAPP_HOST`、`QAAPP_PORT`、`QAAPP_API_TOKENS`、`QAAPP_CORS_ORIGINS`、`QAAPP_STATIC_DIR`、`QAAPP_PROVIDER_PROFILE`、`QAAPP_BROWSER_AUTH_ENABLED`
- 数据库：`DB_*`
- Redis：`REDIS_*`
- 外部 Agent：`POWER_GRID_AGENT_ENDPOINT`、`SMART_QUERY_AGENT_ENDPOINT`、`KNOWLEDGE_AGENT_ENDPOINT`、`EXTERNAL_AGENT_USER_ID`、`EXTERNAL_AGENT_ORG_CODE`

进程环境变量优先于 `.env`。

## 前端 `config/frontend/`

| 文件 | 作用 |
| --- | --- |
| `.env_dev` / `.env_prod` | 开发 / 生产模板；复制为 `.env` 后生效 |
| `.env` | 当前环境配置（本机生成，gitignore） |

Vite 的 `envDir` 指向本目录，只读取 `.env`。常用变量：

| 变量 | 说明 |
| --- | --- |
| `VITE_API_TARGET` | 开发代理的后端地址 |
| `VITE_API_BASE_URL` | 生产浏览器直连后端基址；留空 = 同源 |
| `VITE_API_TOKEN` | 仅关闭 browser_auth 时使用 |
| `VITE_DEV_PORT` | Vite 开发端口（默认 5173） |
| `VITE_APP_*` / `VITE_PRODUCT_NAME` | 品牌文案 |

## 密钥

- 不要把真实 API Key、数据库密码、`QAAPP_API_TOKENS` 写入 `.env_dev` / `.env_prod` 或任何 JSON。
- 只写在本地 `.env`（已 gitignore），或在部署平台上用进程环境变量注入。
- 历史提交中如出现过明文 key，请轮换。
