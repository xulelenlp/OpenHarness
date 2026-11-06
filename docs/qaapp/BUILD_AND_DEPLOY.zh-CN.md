# qaapp 镜像构建与内网部署

> 目标：**构建机把“代码 + 运行环境”打成一个镜像文件**，拷到不联网的内网服务器，
> `docker load` 后改项目配置里的 DB/Redis，直接运行。
>
> 不需要 Docker 构建机、不需要在目标机 `docker build`、不部署 MySQL/Redis。
>
> `deploy/` 现在只有 6 个文件（含 README），没有 bundle/compose/install/复杂脚本：
>
> ```text
> deploy/
> ├── build.sh          # 唯一构建入口
> ├── build_image.py    # 下载基础镜像/依赖、组装单层镜像
> ├── constraints.txt   # Python 依赖 pin
> ├── entrypoint.sh     # 容器入口
> ├── run.sh            # 目标机启动
> └── README.md
> ```

---

## 1. 产物

构建机执行 `deploy/build.sh` 后：

```text
dist/
├── qaapp-image-<version>-amd64.tar.gz     # 镜像文件，约 501 MB
└── qaapp-simple/                          # 直接拷到内网
    ├── qaapp-image-<version>-amd64.tar.gz
    ├── run.sh
    ├── README.md
    ├── QUICK_DEPLOY.zh-CN.md
    ├── BUILD_AND_DEPLOY.zh-CN.md
    └── SHA256SUMS
```

镜像 tag 默认：`openharness-qaapp:<version>-offline`。

镜像内包含：

| 内容 | 位置 |
| --- | --- |
| 后端代码 | `/app/src`、`/app/ohmo`、`/app/qaapp` |
| 前端产物 | `/app/frontend/webapp/dist` |
| Python 依赖 | `/app/vendor`（linux cp311 wheels） |
| Node 20 | `/usr/local/bin/node` |
| ripgrep | `/usr/local/bin/rg` |
| 中文字体 | `/usr/share/fonts/opentype/noto` |
| 入口 | `/app/deploy/entrypoint.sh` |
| 技能/插件/脚本 | `/app/skills`、`/app/plugins`、`/app/scripts` |
| 基础环境 | Python 3.11、git、curl、bash、tzdata |

镜像**不包含**：

- `config/backend/.env`（DB/Redis/模型 Key 在目标机项目配置里）
- 历史会话（`sessions/`、`conversations/`）
- MySQL/Redis
- abc TUI / abcag 渠道

---

## 2. 环境要求

### 2.1 构建机

| 项 | 要求 |
| --- | --- |
| OS | macOS / Linux |
| Python | 3.9+，带 pip |
| Docker | **不需要** |
| 网络 | 首次完整构建需要访问 Docker 镜像代理、PyPI、Node、Debian 镜像 |
| 磁盘 | 8–10 GB 空闲 |

### 2.2 目标机

| 项 | 要求 |
| --- | --- |
| OS/CPU | Linux x86_64（实测 CentOS 7.9） |
| Docker | 20.10+（实测 26.1.4） |
| Compose | **不需要** |
| 网络 | 不需要外网；需能访问 MySQL/Redis/模型网关/外部 Agent |
| 权限 | root 或 sudo |
| 磁盘 | 镜像加载后约 1.47 GB；建议 ≥ 20 GB |

### 2.3 外部服务

- MySQL：现有实例、只读账号、库名、密码。
- Redis：现有实例、端口、密码、db0。
- 模型网关：`OPENHARNESS_BASE_URL` 可达、Key 有效。
- 外部 Agent：`agents.json` 的 endpoint 可达。

以上只写在目标机 `config/backend/.env`，不进入镜像。

---

## 3. 构建前检查（构建机）

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
git status --short
git log -1 --oneline
python3 -V
python3 -m pip -V
df -h .
```

### 3.1 前端产物

镜像使用**构建好的** `frontend/webapp/dist`，不是 Vite dev server：

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

### 3.2 配置

`config/backend/.env` 是目标机配置，打包时至少保证模型配置有效：

```bash
grep -E '^(OPENHARNESS_PROVIDER|OPENHARNESS_MODEL|OPENHARNESS_BASE_URL|OPENHARNESS_AUTH_SOURCE|OPENHARNESS_DASHSCOPE_API_KEY|OPENHARNESS_API_KEY)=' config/backend/.env
```

`DB_*`/`REDIS_*` 可以保持占位，部署时在目标机改。镜像不会带走 `.env`。

---

## 4. 构建镜像

### 4.1 日常改代码：从已有镜像离线重建（推荐，无外网）

前提：保留上一次的镜像，例如 `dist/qaapp-image-0.1.9-amd64.tar.gz`。

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
./deploy/build.sh \
  --from-image dist/qaapp-image-0.1.9-amd64.tar.gz \
  --output dist/qaapp-image-new-amd64.tar.gz
```

过程：

1. 解开旧镜像的 `layer.tar`，得到完整 rootfs；
2. **保留** `/app/vendor`（Python 依赖）、Node、字体、系统库；
3. 用当前仓库的 `src/`、`ohmo/`、`qaapp/`、`frontend/webapp/dist`、
   `skills/`、`plugins/`、`scripts/`、`deploy/` 替换代码；
4. 重新打包 layer 和镜像 tar。

耗时约 2–4 分钟，**不下载任何依赖**。适合：

- 改后端 Python 代码；
- 改前端（先 `npm run build`）；
- 改 skills/plugins/scripts。

> 注意：`--from-image` 不更新 Python 依赖、Node、字体、基础系统；
> 这些变了要走 §4.2 完整构建。

### 4.2 首次 / 依赖变更：完整构建（需要外网）

```bash
./deploy/build.sh
```

首次会下载：

- `python:3.11-bookworm` 基础镜像（约 361 MB）
- linux cp311 wheels（约 91 MB）
- Node 20 linux-x64（约 26 MB）
- Noto CJK 字体（约 54 MB）
- ripgrep 静态二进制

耗时约 10–20 分钟。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--tag` | 镜像 tag；默认 `openharness-qaapp:<version>-offline` |
| `--output` | 镜像 tar；默认 `dist/qaapp-image-<version>-amd64.tar.gz` |
| `--from-image` | 从已有镜像离线重建（§4.1） |
| `--reuse-image` | 不重建，只重新整理 `dist/qaapp-simple/` |
| `--work-dir` | 构建缓存；默认 `/tmp/openharness-offline` |
| `--refresh` | 重新下载/展开基础镜像与依赖 |
| `--clean-work` | 构建前清空缓存 |
| `--registry` / `--auth-url` | 自定义 Docker 镜像代理 |
| `--pip-index-url` | 自定义 PyPI |
| `--node-url` / `--font-url` | 自定义 Node/字体源 |

> 默认镜像代理是 `docker.m.daocloud.io`。如果它 403/超时，换一个可用的
> `--registry`；或者保留旧镜像，用 `--from-image` 做日常代码更新。

### 4.3 只整理部署目录

不重建镜像，只把当前镜像和 `run.sh` 放到 `dist/qaapp-simple/`：

```bash
./deploy/build.sh --reuse-image
```

### 4.4 直接调用底层脚本（可选）

```bash
python3 deploy/build_image.py \
  --tag openharness-qaapp:0.1.9-offline \
  --output dist/qaapp-image-0.1.9-amd64.tar.gz
```

`build_image.py` 同样支持 `--from-image`。

---

## 5. 本机校验

```bash
cd dist/qaapp-simple
shasum -a 256 -c SHA256SUMS      # macOS
sha256sum -c SHA256SUMS          # Linux
```

检查镜像结构：

```bash
tar tzf qaapp-image-0.1.9-amd64.tar.gz
# manifest.json
# config.json
# layer.tar
```

本机没有 Docker 时不需要运行镜像；目标机 `docker load` 后直接启动即可。

---

## 6. 部署（目标机三步）

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

模型 Key、外部 Agent 地址也在这里；沿用现有配置即可。
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

8787 被旧进程占用时：

```bash
HOST_PORT=18790 ./run.sh
```

或直接：

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/workspace/workspace/OpenHarness/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:0.1.9-offline
```

> `--user 0:0` 用于读取项目里 root 0600 的 `.env`。
> 非 root：`chown -R 1000:1000 config/backend`，去掉 `--user 0:0`。

---

## 7. 验证

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
curl -fsS -o /dev/null -w "index=%{http_code}\n" http://127.0.0.1:8787/
docker logs --tail=100 qaapp
```

登录：`admin / Admin@Qa2026`（首次生成 `data/users.json`，登录后立即改密）。

数据源：

```bash
docker exec qaapp python -m qaapp datasource check \
  --cwd /app --config-dir /app/config/backend --workspace /data
```

---

## 8. 改动后怎么做

| 改了什么 | 要重建镜像吗 | 怎么做 |
| --- | --- | --- |
| `src/`、`ohmo/`、`qaapp/` | 要 | `build.sh --from-image <旧镜像>` |
| `frontend/webapp` | 要 | 先 `npm run build`，再 `--from-image` |
| `skills/`、`plugins/`、`scripts/` | 要 | `--from-image` |
| `config/backend/.env`（DB/Redis/Key） | **不要** | 改目标机配置 + `docker restart qaapp` |
| `config/backend/*.json` | **不要** | 改目标机配置 + `docker restart qaapp` |
| `run.sh` | **不要** | 重新拷贝脚本 |
| `pyproject.toml`/依赖/Node/字体 | 要 | 完整构建 `build.sh`（需外网） |

**DB/Redis 永远只改目标机配置，不重建镜像。**

---

## 9. 升级 / 回滚 / 备份

### 升级

```bash
# 构建机
./deploy/build.sh \
  --from-image dist/qaapp-image-0.1.9-amd64.tar.gz \
  --output dist/qaapp-image-new-amd64.tar.gz

# 目标机
cd /root/qaapp-simple
docker load -i qaapp-image-new-amd64.tar.gz
FORCE=1 ./run.sh
```

配置和数据不动。

### 回滚

```bash
docker rm -f qaapp
IMAGE=openharness-qaapp:<old-tag> ./run.sh
```

### 备份

```bash
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/workspace/workspace/OpenHarness/config/backend \
  /root/qaapp-data
```

---

## 10. 构建常见问题

| 现象 | 处理 |
| --- | --- |
| `frontend/webapp/dist/index.html` 不存在 | 先 `npm ci && npm run build` |
| 镜像代理 403/超时 | 换 `--registry`；或保留旧镜像用 `--from-image` |
| `pip download` 无匹配 wheel | 检查 `--pip-index-url`、Python/pip 版本 |
| 字体/Node 下载失败 | 换 `--font-url`/`--node-url` |
| 磁盘不足 | 换 `--work-dir`，清理 `/tmp/openharness-offline` |
| `--from-image` 报没有 `/app/vendor` | 旧镜像不是本项目完整镜像；用完整构建 |
| 构建中断 | `--refresh` 或 `--clean-work` 后重试 |
| `docker load` 失败 | 校验 tar 完整；Docker 20.10+ |

## 11. 部署常见问题

| 现象 | 处理 |
| --- | --- |
| 读不到配置 `Permission denied` | `--user 0:0`，或 `chown -R 1000:1000 config/backend` |
| 8787 被占用 | 停旧进程，或 `HOST_PORT=18790` |
| `healthz` 不通 | `docker logs --tail=100 qaapp` |
| DB/Redis 连不上 | 改 `.env` 后 `docker restart qaapp`；检查网络 |
| 模型 401/超时 | 检查 Key/`OPENHARNESS_BASE_URL` |
| 登录 403 `cross_site` | 改 `QAAPP_CORS_ORIGINS` 为实际来源 |
| HTTPS 登录掉线 | 设 `QAAPP_AUTH_TRUST_PROXY=true` |
| 中文图表方框 | 使用本项目镜像（已带 Noto CJK） |
| 默认密码不生效 | `data/users.json` 已存在；删除会重置账户 |
| 数据目录权限 | `chown -R 1000:1000 data` 或用 root |

---

## 12. 安全与限制

- 镜像不含 `.env`；配置挂载，按密钥文件管理。
- 首次登录立即改密；默认 `admin / Admin@Qa2026`。
- 公网访问前置 HTTPS 反代，设 `QAAPP_AUTH_TRUST_PROXY=true`。
- `QAAPP_CORS_ORIGINS` 用显式来源。
- MySQL 只读账号；Redis 只读 db0。
- 单实例，不能 `--scale`。
- 不迁移旧 `~/.ohmo` 历史。
- 离线镜像不含 `jq/less`；`git/curl/rg` 已包含。
- `--from-image` 只适合代码更新；依赖/Node/字体变更要完整构建。

---

## 13. 本次实测

| 项 | 结果 |
| --- | --- |
| 构建机 | macOS arm64、Python 3.11.5、无 Docker |
| 目标机 | CentOS 7.9、Docker 26.1.4 |
| 镜像 | `openharness-qaapp:0.1.9-offline`，约 501 MB |
| `docker load` | 成功 |
| 直接挂项目配置运行 | healthz/首页/静态资源 200 |
| 登录/模型 | admin 登录成功；SSE 返回 `OK` |
| MySQL/Redis | 142 张表 / 36351 keys，正常 |
| `--from-image` 离线重建 | 约 3m40s，重新 load 后运行正常 |

---

## 14. 相关文件

- 构建入口：[`deploy/build.sh`](../../deploy/build.sh)
- 镜像组装：[`deploy/build_image.py`](../../deploy/build_image.py)
- 目标机启动：[`deploy/run.sh`](../../deploy/run.sh)
- 依赖锁定：[`deploy/constraints.txt`](../../deploy/constraints.txt)
- 快速部署：[`QUICK_DEPLOY.zh-CN.md`](QUICK_DEPLOY.zh-CN.md)
- 原有裸机部署：[`DEPLOYMENT.zh-CN.md`](DEPLOYMENT.zh-CN.md)
