# qaapp 镜像构建与内网部署手册

> 面向以后自己维护：**从当前仓库构建“代码 + 运行环境”的镜像文件**，
> 拷贝到不联网的内网服务器，`docker load` + 改项目配置 + 启动即可。
>
> 不需要 Docker 构建机、不需要在目标机 `docker build`、不部署 MySQL/Redis。
>
> 只想看部署三步？直接跳到 §5；只想看打包？看 §3–§4。

---

## 1. 最终形态

```text
构建机（有外网，可无 Docker）
  └── ./deploy/simple/build.sh
        ├── dist/qaapp-image-<version>-amd64.tar.gz      # 镜像文件（约 501 MB）
        └── dist/qaapp-simple/                            # 直接拷走
              ├── qaapp-image-<version>-amd64.tar.gz
              ├── run.sh
              ├── README.md
              ├── QUICK_DEPLOY.zh-CN.md
              └── SHA256SUMS

内网目标机（有 Docker，无外网）
  └── docker load -i qaapp-image-<version>-amd64.tar.gz
      改项目 config/backend/.env 的 DB/Redis
      ./run.sh
```

镜像已经包含：

- 应用代码：`/app/src`、`/app/ohmo`、`/app/qaapp`、`/app/frontend/webapp/dist`、
  `/app/skills`、`/app/plugins`、`/app/scripts`、`/app/deploy`
- 运行环境：Python 3.11、全部 Python 依赖（`/app/vendor`）、Node 20、ripgrep、
  Noto CJK 字体、git/curl/bash、tzdata
- **不包含**：`config/backend/.env`、历史会话、MySQL/Redis、abc TUI、abcag 渠道

配置（尤其 DB/Redis）放在目标机的项目 `config/backend/.env`，通过挂载提供，
所以以后改 DB/Redis **不需要重新构建镜像**。

---

## 2. 环境要求

### 2.1 构建机

| 项 | 要求 |
| --- | --- |
| OS | macOS / Linux |
| Python | 3.9+，带 pip（`python3 -V`、`python3 -m pip -V`） |
| Docker | **不需要** |
| 网络 | 首次完整构建需要访问 Docker 镜像代理、PyPI、Node、Debian 镜像 |
| 磁盘 | 8–10 GB 空闲 |

### 2.2 目标机

| 项 | 要求 |
| --- | --- |
| OS/CPU | Linux x86_64（实测 CentOS 7.9） |
| Docker | 20.10+（实测 26.1.4） |
| Compose | **不需要**（本方案只用 `docker run`） |
| 网络 | 不需要外网；需能访问 MySQL/Redis/模型网关/外部 Agent |
| 权限 | root 或 sudo |
| 磁盘 | 镜像加载后约 1.47 GB；建议 ≥ 20 GB |

### 2.3 外部服务

只使用现有服务，不在本项目部署：

- MySQL：只读账号、密码、库名；目标机能访问。
- Redis：地址、端口、密码、db0；目标机能访问。
- 模型网关：`OPENHARNESS_BASE_URL` 可达、Key 有效。
- 外部 Agent：`agents.json` 的 endpoint 可达。

这些值只写在目标机的 `config/backend/.env`，不写进镜像。

---

## 3. 打包前检查（构建机）

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
git status --short
git log -1 --oneline
python3 -V
python3 -m pip -V
df -h .
```

### 3.1 前端产物必须存在

镜像里的前端是构建好的 `frontend/webapp/dist`，不是 Vite dev server：

```bash
test -f frontend/webapp/dist/index.html && echo "dist OK"
```

如果没有，或前端代码有改动：

```bash
cp config/frontend/.env_prod config/frontend/.env
cd frontend/webapp
npm ci
npm run build
cd ../..
```

> `VITE_API_BASE_URL` 必须留空（同源）；构建使用 `.env_prod`。

### 3.2 配置检查

`config/backend/.env` 是给**目标机**用的，打包时至少要保证模型配置有效：

```bash
grep -E '^(OPENHARNESS_PROVIDER|OPENHARNESS_MODEL|OPENHARNESS_BASE_URL|OPENHARNESS_AUTH_SOURCE|OPENHARNESS_DASHSCOPE_API_KEY|OPENHARNESS_API_KEY)=' config/backend/.env
```

`DB_*`/`REDIS_*` 可以保持占位；部署时在目标机改。

> 镜像不会带走 `.env`；它只带 `config/backend/*.json` 和 `.env_prod` 模板。

---

## 4. 构建镜像

### 4.1 日常改代码：从已有镜像离线重建（推荐，不需要外网）

前提：你已有上一次构建的镜像文件，例如
`dist/qaapp-image-0.1.9-amd64.tar.gz`。

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
./deploy/simple/build.sh \
  --from-image dist/qaapp-image-0.1.9-amd64.tar.gz
```

它做的事：

1. 解开旧镜像的 `layer.tar` 得到完整 rootfs；
2. 保留 `/app/vendor`（Python 依赖）以及 Node/字体/系统库；
3. 用当前仓库的 `src/`、`ohmo/`、`qaapp/`、`frontend/webapp/dist`、
   `skills/`、`plugins/`、`scripts/`、`deploy/` 替换 `/app` 里的代码；
4. 重新打包 layer + 生成新的镜像 tar。

建议加 `--output` 保留旧镜像，例如：

```bash
./deploy/simple/build.sh \
  --from-image dist/qaapp-image-0.1.9-amd64.tar.gz \
  --output dist/qaapp-image-0.1.9-new-amd64.tar.gz
```

不加 `--output` 时会覆盖原文件（内部先写临时文件再替换，不会写坏；但旧镜像不保留）。
耗时约 2–4 分钟；**不下载任何依赖**。适合：

- 改了后端 Python 代码；
- 改了前端并已 `npm run build`；
- 改了 skills/plugins/scripts。

> 注意：`--from-image` 不会更新 Python 依赖、Node、字体、基础系统。
> 这些变了要走 §4.2 完整构建。

### 4.2 首次构建 / 依赖变更：完整构建（需要外网）

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
./deploy/simple/build.sh
```

首次会下载：

- `python:3.11-bookworm` 基础镜像（约 361 MB）
- linux cp311 wheels（约 91 MB）
- Node 20 linux-x64（约 26 MB）
- Noto CJK 字体（约 54 MB）
- ripgrep 静态二进制

耗时约 10–20 分钟（受网络影响）。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--tag` | 镜像 tag；默认 `openharness-qaapp:<version>-offline` |
| `--output` | 镜像 tar 路径；默认 `dist/qaapp-image-<version>-amd64.tar.gz` |
| `--work-dir` | 构建缓存；默认 `/tmp/openharness-offline` |
| `--from-image` | 从已有镜像重建（§4.1） |
| `--reuse-image` | 不重建，只重新整理 `dist/qaapp-simple/` |
| `--refresh` | 重新下载/展开基础镜像与依赖 |
| `--clean-work` | 构建前清空缓存 |
| `--registry` / `--auth-url` | 自定义 Docker 镜像代理 |
| `--pip-index-url` | 自定义 PyPI |
| `--node-url` / `--font-url` | 自定义 Node/字体源 |

> 默认镜像代理是 `docker.m.daocloud.io`。如果它返回 403/超时，换一个可用的
> `--registry`；或者保留旧镜像，用 `--from-image` 做日常代码更新。

### 4.3 只重新整理部署目录

不重建镜像，只把当前镜像和 `run.sh` 放到 `dist/qaapp-simple/`：

```bash
./deploy/simple/build.sh --reuse-image
```

### 4.4 直接调用底层脚本（可选）

```bash
python3 deploy/offline/build_image.py \
  --tag openharness-qaapp:0.1.9-offline \
  --output dist/qaapp-image-0.1.9-amd64.tar.gz
```

`build_image.py` 支持 `--from-image`，参数与 `build.sh` 相同。

---

## 5. 部署（目标机，三步）

### 第 1 步：加载镜像

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-0.1.9-amd64.tar.gz
docker images | grep openharness-qaapp
```

### 第 2 步：改项目配置的 DB/Redis

```bash
vi /root/workspace/workspace/OpenHarness/config/backend/.env
```

只改需要改的：

```bash
DB_HOST=...
DB_PORT=...
DB_USER=...
DB_PASSWORD=...
DB_NAME=ai_search_fj

REDIS_HOST=...
REDIS_PORT=...
REDIS_PASSWORD=...
```

模型 Key、外部 Agent 地址也在这个文件；沿用现有配置即可。  
**不需要重新构建镜像。**

### 第 3 步：启动

```bash
cd /root/qaapp-simple
./run.sh
```

默认：

- 配置：`/root/workspace/workspace/OpenHarness/config/backend`
- 数据：`/root/qaapp-data`
- 端口：`8787`
- 容器：`qaapp`

如果 8787 被旧进程占用：

```bash
HOST_PORT=18790 ./run.sh
```

或不用脚本，直接：

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/workspace/workspace/OpenHarness/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:0.1.9-offline
```

> `--user 0:0` 是为了读取项目里 root 0600 的 `.env`。  
> 如需非 root：`chown -R 1000:1000 config/backend`，去掉 `--user 0:0`。

---

## 6. 验证

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
curl -fsS -o /dev/null -w "index=%{http_code}\n" http://127.0.0.1:8787/
docker logs --tail=100 qaapp
```

登录：`admin / Admin@Qa2026`（首次启动生成 `data/users.json`，登录后立即改密）。

数据源：

```bash
docker exec qaapp python -m qaapp datasource check \
  --cwd /app --config-dir /app/config/backend --workspace /data
```

模型问答、附件、图表、报告在前端验证。

---

## 7. 改动后怎么做（最重要）

| 改了什么 | 要不要重建镜像 | 怎么做 |
| --- | --- | --- |
| `src/`、`ohmo/`、`qaapp/` 后端代码 | 要 | `build.sh --from-image <旧镜像>` |
| `frontend/webapp` 源码 | 要 | 先 `npm run build`，再 `--from-image` |
| `skills/`、`plugins/`、`scripts/` | 要 | `--from-image` |
| `config/backend/.env`（DB/Redis/Key） | **不要** | 改目标机配置 + `docker restart qaapp` |
| `config/backend/*.json` | **不要** | 改目标机配置 + `docker restart qaapp` |
| `run.sh` | **不要** | 重新拷贝脚本即可 |
| `pyproject.toml` / 依赖 / Node / 字体 | 要 | 完整构建 `build.sh`（需外网） |
| 基础镜像/系统库 | 要 | 完整构建 `build.sh` |

> 关键：**DB/Redis 永远只改目标机 `config/backend/.env`**，不要为了换库重建镜像。

---

## 8. 升级、回滚、备份

### 8.1 升级

```bash
# 构建机：生成新镜像（建议 --output 保留旧镜像）
./deploy/simple/build.sh \
  --from-image dist/qaapp-image-0.1.9-amd64.tar.gz \
  --output dist/qaapp-image-0.1.9-new-amd64.tar.gz
# 或完整构建：./deploy/simple/build.sh

# 传到目标机后
cd /root/qaapp-simple
docker load -i qaapp-image-<new>.tar.gz
FORCE=1 ./run.sh
```

配置和数据不动。

### 8.2 回滚

```bash
docker rm -f qaapp
IMAGE=openharness-qaapp:<old-tag> ./run.sh
```

### 8.3 备份

```bash
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/workspace/workspace/OpenHarness/config/backend \
  /root/qaapp-data
```

数据目录包含 `users.json`、`sessions/`、`conversations/`、`logs/`。

---

## 9. 构建常见问题

| 现象 | 处理 |
| --- | --- |
| `frontend/webapp/dist/index.html` 不存在 | 先 `npm ci && npm run build`，再构建 |
| `docker.m.daocloud.io` 403/超时 | 换 `--registry`；或保留旧镜像用 `--from-image` |
| `pip download` 无匹配 wheel | 检查 `--pip-index-url`；确认 Python/pip 版本 |
| 字体/Node 下载失败 | 换 `--font-url`/`--node-url` |
| 磁盘不足 | 换 `--work-dir`，清理 `/tmp/openharness-offline` |
| `--from-image` 报没有 `/app/vendor` | 旧镜像不是本项目的完整镜像；用完整构建 |
| 构建中断 | `--refresh` 重试；或 `--clean-work` 清缓存 |
| `docker load` 失败 | 校验 tar 完整；确认 Docker 20.10+ |
| 镜像 tag 不是预期的 | 看 `build.sh --tag`；`docker images` |

## 10. 部署常见问题

| 现象 | 处理 |
| --- | --- |
| `Permission denied` 读不到配置 | 用 `--user 0:0`，或 `chown -R 1000:1000 config/backend` |
| 8787 被占用 | 停旧进程，或 `HOST_PORT=18790` |
| `healthz` 不通 | `docker logs --tail=100 qaapp`；等待启动 |
| DB/Redis 连不上 | 改 `.env` 后 `docker restart qaapp`；确认目标机网络 |
| 模型 401/超时 | 检查 `.env` 的 Key/`OPENHARNESS_BASE_URL` |
| 登录 403 `cross_site` | 改 `QAAPP_CORS_ORIGINS` 为实际来源；重启 |
| HTTPS 下登录掉线 | 设 `QAAPP_AUTH_TRUST_PROXY=true` |
| 中文图表方框 | 使用本项目镜像（已带 Noto CJK），不要换基础镜像 |
| 默认密码不生效 | `data/users.json` 已存在；删它重置（会丢账户） |
| 数据目录权限 | `chown -R 1000:1000 data`（或保持 root 运行） |

---

## 11. 安全与限制

- 镜像不含 `.env`；配置挂载，按密钥文件管理。
- 首次登录立即改密；默认 `admin / Admin@Qa2026`。
- 公网访问前置 HTTPS 反代，设 `QAAPP_AUTH_TRUST_PROXY=true`。
- `QAAPP_CORS_ORIGINS` 用显式来源；同源可留空。
- MySQL 只读账号；Redis 只读 db0。
- 单实例，不能 `--scale`。
- 不迁移旧 `~/.ohmo` 历史；需要时单独拷贝。
- 离线镜像不含 `jq/less`；`git/curl/rg` 已包含。
- `--from-image` 只适合代码更新；依赖/Node/字体变更要完整构建。

---

## 12. 本次实测

| 项 | 结果 |
| --- | --- |
| 构建机 | macOS arm64、Python 3.11.5、无 Docker |
| 目标机 | CentOS 7.9、Docker 26.1.4 |
| 镜像 | `openharness-qaapp:0.1.9-offline`，501 MB |
| `docker load` | 成功 |
| 镜像运行期自检 | 46 项，FAIL=0，WARN=0 |
| 直接挂项目配置运行 | healthz/首页/静态资源 200 |
| 登录/模型 | admin 登录成功；SSE 返回 `OK` |
| MySQL/Redis | 142 张表 / 36351 keys，正常 |
| `--from-image` 离线重建 | 3m40s 完成，重新 load 后自检 46/46 |
| 代码提交 | 以仓库 `git log -1` 为准 |

---

## 13. 相关文件

- 一键构建：[`deploy/simple/build.sh`](../../deploy/simple/build.sh)
- 最简启动：[`deploy/simple/run.sh`](../../deploy/simple/run.sh)
- 镜像组装：[`deploy/offline/build_image.py`](../../deploy/offline/build_image.py)
- 部署速查：[`QUICK_DEPLOY.zh-CN.md`](QUICK_DEPLOY.zh-CN.md)
- 完整高级方案（bundle/install/升级/排障）：[`OFFLINE_PACKAGING.zh-CN.md`](OFFLINE_PACKAGING.zh-CN.md)
