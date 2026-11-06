# qaapp 镜像化部署（Docker + 外部 MySQL/Redis）

面向“把当前 qaapp Web 服务整体迁移到另一台 Linux 服务器”的场景：

- 应用（后端 + Web 前端 + 运行时依赖）打成一个 Docker 镜像；
- MySQL / Redis **不部署**，复用目标环境现有实例；
- 配置与密钥不打进镜像，由部署包在宿主侧维护并只读挂载；
- 运行数据全部落在 `data/`，容器重建不丢。

构建入口：
- 目标机无外网（推荐）：[`deploy/build_offline.sh`](../../deploy/build_offline.sh)（构建机不需要 Docker）；
- 构建机有 Docker：[`deploy/build.sh`](../../deploy/build.sh)。

部署包安装入口：`install.sh`。目标机只要求 Docker Engine + Docker Compose v2，
不需要外网、不需要 `docker build`、不需要 pip/npm/apt/yum。

---

## 1. 交付物

```text
dist/
├── qaapp-image-<tag>-<arch>.tar.gz     # docker save 的镜像
├── qaapp-deploy-<tag>-<arch>.tar.gz    # 部署包（含镜像、compose、配置种子、脚本）
└── SHA256SUMS

qaapp-deploy-<tag>-<arch>/
├── qaapp-image-<tag>-<arch>.tar.gz
├── docker-compose.yaml
├── stack.env                 # 镜像 tag / 端口 / 初始密码（0600）
├── config/backend/           # 当前配置（.env + 4 个 JSON）
├── config.seed/backend/      # 原始配置模板（--force-config 用）
├── install.sh
├── upgrade.sh
├── backup.sh
├── cleanup.sh
├── uninstall.sh
├── smoke.sh
├── BUILD_INFO / BUILD_INFO.pip-freeze.txt / BUILD_INFO.wheels.txt
└── bundle.meta
```

镜像内包含：Python 3.11、全部 Python 依赖、Node 20（`run_script` 的 .js）、
Noto Sans CJK 字体、`tzdata`、ripgrep、git、curl、bash 等执行能力。
（`deploy/build.sh` 的 Docker 路径还会 apt 安装 jq 等常用工具；离线单层镜像以
git/curl/rg 为主，不包含 jq，非必需。）
镜像**不包含**：`.env`、`~/.openharness/settings.json`、历史会话、MySQL/Redis。

---

## 2. 架构与数据流

```text
浏览器 ──HTTP/SSE/WS──> qaapp 容器 :8787
                         ├─ 静态前端 /app/frontend/webapp/dist
                         ├─ 模型网关 (OPENHARNESS_BASE_URL)
                         ├─ 外部 Agent (agents.json 的三个 endpoint)
                         ├─ MySQL (只读，datasources.json)
                         └─ Redis (db0，只读缓存)
运行数据：./data -> /data
配置：    ./config/backend -> /app/config/backend:ro
```

- 容器内固定监听 `0.0.0.0:8787`；宿主端口由 `--port` 决定。
- 单进程会话池：**只能跑 1 个副本**，不要 `--scale`。
- 会话历史落 `data/sessions/`，会话文件落 `data/conversations/<id>/`。

---

## 3. 环境依赖

### 3.1 目标机

| 项 | 要求 |
| --- | --- |
| OS / CPU | Linux x86_64（镜像按 `linux/amd64` 构建） |
| Docker | Engine 20.10+，`docker compose` v2（实测 CentOS 7 + Docker 26.1.4 + Compose v2.27.1） |
| 资源 | 建议 2–4 vCPU / 4 GB RAM / 20 GB 磁盘（镜像 + Python 依赖本身约 1 GB） |
| 权限 | 安装脚本需要 root/sudo（docker、chown） |
| 端口 | 宿主端口默认 8787；前置反代时只暴露 443 |
| 外网/内网 | 能访问模型网关、三个外部 Agent、MySQL、Redis；`web_search` 需要访问 Bing |

### 3.2 镜像内系统依赖

| 依赖 | 用途 | 缺失影响 |
| --- | --- | --- |
| Noto Sans CJK（离线镜像为 TTC） | matplotlib 中文图表 | 图表中文变方框 |
| `tzdata` + `TZ=Asia/Shanghai` | `ZoneInfo("Asia/Shanghai")` | 业务日期错/运行时报错 |
| Node 20 | `run_script` 的 `language=node` | Node 脚本不可执行 |
| ripgrep | `grep`/`glob` 工具的高性能路径 | 回退 Python 扫描（更慢） |
| git | agent 的 git/worktree 能力 | 相关工具不可用 |
| bash/curl/unzip 等（Docker 路径另有 jq） | agent 的通用执行能力 | 部分任务需要时失败 |
| `libgomp1/libfreetype6/libatomic1/libstdc++6` | numpy/matplotlib/node 动态库 | 启动或绘图失败 |

### 3.3 外部服务

| 服务 | 配置项 | 说明 |
| --- | --- | --- |
| MySQL | `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME` | 只读账号；`read_only=true`；表级黑名单见 `datasources.json` |
| Redis | `REDIS_HOST/REDIS_PORT/REDIS_PASSWORD` | 只读、db0；不可用时 `--no-redis` |
| 模型网关 | `OPENHARNESS_BASE_URL` + 密钥 | 不通则对话不可用 |
| 外部 Agent | `POWER_GRID_AGENT_ENDPOINT` 等 | 不通时通用问答仍可用，专业 Agent 降级 |
| 搜索/抓取 | `web_search` / `web_fetch` | 需要出口；可用 `OPENHARNESS_WEB_PROXY` |

**不包含**：Docker 沙箱（默认关闭）、MCP 服务器、TUI、Telegram/Slack/飞书渠道。

---

## 4. 构建

### 4.1 目标机无外网：在普通构建机上生成“单层离线镜像”（推荐）

适用：目标服务器是内网 CentOS/Ubuntu，**不能访问外网，也不能 docker build 拉依赖**。
构建机不需要 Docker、不需要 Linux，只需要 Python 3.9+（带 pip）和外网。

```bash
# 在仓库根目录（构建机）
./deploy/build_offline.sh --tag openharness-qaapp:0.1.9-offline
```

产物：

```text
dist/qaapp-image-openharness-qaapp_0.1.9-offline-amd64.tar.gz   # 约 501 MB，docker load 即用
dist/qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64.tar.gz  # 约 498 MB，含镜像+compose+配置+脚本
dist/SHA256SUMS
```

它做的事：

1. 从 Docker 镜像代理下载 `python:3.11-bookworm`（amd64）全部 layers，展开成完整 rootfs；
2. 用 `pip download` 拉取 **linux cp311 wheels**，再用 `pip install --target /app/vendor`
   装进镜像（只写文件，不执行 Linux 二进制）；
3. 放入 Node 20 linux-x64、ripgrep 静态二进制、Noto Sans CJK 字体；
4. 复制应用源码、`frontend/webapp/dist`、skills/plugins/scripts；
5. 生成 docker-save 格式的单层镜像（`manifest.json` + `config.json` + `layer.tar`）。

**镜像完全自包含**：目标机 `docker load` 后直接 `docker run`/compose 启动，不需要
`docker build`、不需要 pip/npm、不需要 apt/yum、不需要外网。

构建机默认使用的源（可用参数覆盖）：

| 用途 | 默认 |
| --- | --- |
| Docker 镜像代理 | `https://docker.m.daocloud.io`，token `https://m.daocloud.io/auth/token` |
| PyPI | `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple` |
| Node | `https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/` |
| 字体 | 同镜像站的 Debian `fonts-noto-cjk` |

```bash
# 内网/其它镜像源
./deploy/build_offline.sh \
  --registry https://your-registry-mirror \
  --pip-index-url https://your-pypi/simple \
  --node-url https://your-node/v20.19.0/node-v20.19.0-linux-x64.tar.xz \
  --font-url https://your-debian/fonts-noto-cjk.deb

# 只重新组装部署包，不重建镜像
./deploy/build_offline.sh --reuse-image
```

构建缓存默认在 `/tmp/openharness-offline`（基础 layers/rootfs、wheels、node、fonts、rg）；
镜像约 1.47 GB（未压缩），压缩后约 501 MB。目标是单层镜像，`docker load` 需要约 1.5 GB 磁盘。

**已验证环境**：CentOS 7.9（kernel 3.10）+ Docker 26.1.4 + Compose v2.27.1。
验证结果：`docker load` 成功；镜像内 `selfcheck --runtime --strict` 46/46 通过；
服务启动、登录、模型问答、MySQL/Redis 连接、附件上传解析、静态前端均正常。

### 4.2 有 Docker 的构建机（可选）

构建机需要 Docker + 外网（或内网 PyPI/npm/镜像仓库）。

```bash
# 在仓库根目录
./deploy/build.sh --platform linux/amd64
# 指定 tag / 内网源
./deploy/build.sh --platform linux/amd64 \
  --tag registry.example.com/qaapp:0.1.9 \
  --pip-index-url https://mirrors.example.com/pypi/simple \
  --npm-registry https://registry.npmmirror.com
```

构建过程：

1. `node:20` 阶段 `npm ci` + `npm run build`（强制使用 `config/frontend/.env_prod`）；
2. `python:3.11-slim-bookworm` 阶段安装系统依赖与 `pip install -e .`（editable，保证 `skills/`、`plugins/` 的相对路径成立）；
3. 构建期执行 `python /app/deploy/selfcheck.py --runtime --strict`，依赖/字体/静态资源任一缺失即失败；
4. `docker save` 镜像并组装部署包、写 `SHA256SUMS`。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--tag` | 镜像 tag；默认 `openharness-qaapp:<version>-<sha>` |
| `--platform` | 默认 `linux/amd64` |
| `--pip-index-url` / `--npm-registry` | 内网 PyPI / npm 源 |
| `--python-image` / `--node-image` | 替换基础镜像（内网 Harbor） |
| `--no-constraints` | 不锁定依赖版本（排障用） |
| `--push` | 构建后推送镜像 |
| `--no-save` | 只构建/推送，不生成 tar |
| `--keep-stage` | 保留部署包暂存目录 |

---

## 5. 安装（目标机）

```bash
tar xzf qaapp-deploy-*.tar.gz
cd qaapp-deploy-*

sudo ./install.sh \
  --db-host 10.0.0.10 --db-port 3306 \
  --db-user readonly --db-password '***' --db-name ai_search_fj \
  --redis-host 10.0.0.11 --redis-port 6379 --redis-password '***' \
  --port 8787 \
  --public-url http://10.0.0.12:8787
```

不带参数会交互式询问 DB/Redis/公开地址/初始密码。

安装脚本按顺序执行：

1. 检查 root、docker、compose、端口、镜像 tar、SHA256、镜像架构；
2. `docker load`；
3. 镜像运行期自检 `selfcheck --runtime --strict`（依赖/字体/Node/静态资源）；
4. 初始化 `config/`、`data/`（首次从 `config.seed` 补齐，已存在则保留）；
5. 容器内 `configure.py` 写入 `.env`（DB/Redis/CORS），同时按 `--no-redis` 改 `datasources.json`；
6. `selfcheck --config --strict`（JSON/`.env`/静态目录/workspace）；
7. `abcqa datasource check`（MySQL/Redis 鉴权与只读检查）；
8. `selfcheck --network`（模型网关/外部 Agent/DB/Redis TCP）；
9. `docker compose up -d --force-recreate --wait` + `/healthz` 轮询 + `smoke.sh`。

失败时打印 `docker compose logs`；`--skip-preflight` 可跳过第 7/8 步（通用问答仍可用），
但不跳过第 3/6 步。

安装完成后：

- 访问 `http://<IP>:8787`，用 admin 初始密码登录；
- 初始密码在 `stack.env`（0600）与安装输出中；
- `users.json` 只在首次启动生成，之后改 `stack.env` 密码无效，需在 UI 改密或删除 `data/users.json`（会重置账户）。

---

## 6. 配置

### 6.1 文件

| 文件 | 作用 | 备注 |
| --- | --- | --- |
| `config/backend/.env` | 唯一环境配置 | 密钥/DB/Redis/外部 Agent；只读挂载进容器 |
| `config/backend/agents.json` | Agent 注册表 | 改后重启 |
| `config/backend/webapp.json` | 服务配置 | host/port/auth/cors/静态目录 |
| `config/backend/dangerous-actions.json` | 高危操作门禁 | 改后重启 |
| `config/backend/datasources.json` | MySQL/Redis | `${DB_*}` 由 `.env` 展开 |
| `stack.env` | compose 变量 | 镜像 tag、端口、初始密码 |

优先级：**compose 注入的环境变量 > `.env` > JSON 字面值**。

### 6.2 容器强制项（compose 注入，覆盖 `.env`）

| 变量 | 值 |
| --- | --- |
| `OHMO_WORKSPACE` | `/data` |
| `QAAPP_CONFIG_DIR` | `/app/config/backend` |
| `QAAPP_STATIC_DIR` | `/app/frontend/webapp/dist` |
| `QAAPP_HOST` / `QAAPP_PORT` | `0.0.0.0` / `8787` |
| `HOME` / `MPLCONFIGDIR` | `/data/home` / `/data/home/.cache/matplotlib` |

### 6.3 关键 `.env` 项

```bash
# 模型
OPENHARNESS_PROVIDER=openai
OPENHARNESS_API_FORMAT=openai
OPENHARNESS_MODEL=deepseek-flash
OPENHARNESS_BASE_URL=http://ai.ac-energytech.com/v1
OPENHARNESS_AUTH_SOURCE=dashscope_api_key
OPENHARNESS_DASHSCOPE_API_KEY=ENC[...]   # 或 OPENHARNESS_API_KEY

# Web
QAAPP_HOST=0.0.0.0
QAAPP_PORT=8787
QAAPP_STATIC_DIR=/app/frontend/webapp/dist
QAAPP_CORS_ORIGINS=*                     # 同源可 *；分域名/多入口写显式来源
QAAPP_AUTH_TRUST_PROXY=false             # 反代后设 true

# 数据源
DB_HOST=...
DB_PORT=3306
DB_USER=...
DB_PASSWORD=...
DB_NAME=ai_search_fj
REDIS_HOST=...
REDIS_PORT=6379
REDIS_PASSWORD=...

# 外部 Agent
POWER_GRID_AGENT_ENDPOINT=http://...:18081/api/chat/stream
SMART_QUERY_AGENT_ENDPOINT=http://...:18094/chat_agent_roll
KNOWLEDGE_AGENT_ENDPOINT=http://...:18094/chat_agent_roll
EXTERNAL_AGENT_USER_ID=...
EXTERNAL_AGENT_ORG_CODE=...
```

改完配置必须重启：

```bash
docker compose --env-file stack.env -f docker-compose.yaml restart qaapp
```

> `ENC[...]` 是仓库内的可逆编码，不是强加密；真正敏感的环境建议用密钥管理/KMS 注入进程环境变量。

---

## 7. 数据、日志与备份

```text
data/
├── users.json          # 账户摘要（0600）
├── sessions/           # 会话快照 + traces
├── conversations/      # 每会话 uploads/extracted/scripts/tmp/outputs
├── data/               # openharness 数据目录
├── logs/               # 服务日志（前台模式主要走 docker logs）
└── home/               # HOME、matplotlib/字体缓存、pip --user
```

- 容器日志：`docker compose ... logs -f qaapp`（json-file，50 MB × 5）。
- 备份：`./backup.sh --keep 14`，输出 `backups/qaapp-<ts>.tar.gz`（0600）。
- 恢复：`docker compose ... down` → 解包覆盖 → `up -d --wait`。
- 清理：`./cleanup.sh --older-than-days 30`（dry-run）→ 确认后 `--apply`。
- 建议把 `./cleanup.sh --apply` 加进 cron（每天 03:00）。

---

## 8. 升级与回滚

```bash
# 新包解压到新目录
./upgrade.sh --bundle /path/to/qaapp-deploy-<new>
```

- 只替换 `stack.env` 的 `QAAPP_IMAGE` 并重建容器；
- `config/`、`data/` 不动；`config.seed/` 更新（不影响当前配置）；
- 失败时脚本打印回滚命令：把 `QAAPP_IMAGE` 改回旧 tag，再 `up -d --wait`。

新增配置项时：对比新包的 `config.seed/backend/*.json` 与当前 `config/backend/*.json`，
手工合并后重启（不要直接用 `--force-config`，它会重置 `.env`）。

---

## 9. 安全清单

1. **首次登录立即改密**；删除/重建 `data/users.json` 会重置账户，谨慎。
2. 公网访问必须前置 HTTPS 反代，并设 `QAAPP_AUTH_TRUST_PROXY=true`；仅暴露 443。
3. `QAAPP_CORS_ORIGINS` 用显式来源；`*` 只适合同源/内网可信场景。
4. 部署包与 `config/backend/.env` 含密钥：`0600`、按密钥文件管理、不要进 git/工单/聊天记录。
5. 轮换仓库历史中出现过的 Key：`config/backend/.env_dev` 里有 `ENC[...]` 的模型 Key 与 DB/Redis 密码。
6. 只用最小权限的 MySQL 只读账号；Redis 只读、db0。
7. 镜像不含 `.env`，可安全推到私有仓库；部署包不要公开分发。
8. 容器以 UID 1000 运行；`data/`、`config/` 需属于 1000。需要 root 能力时自行评估覆盖。

---

## 10. 验证清单

> 离线单层镜像已在 **CentOS 7.9（kernel 3.10）+ Docker 26.1.4 + Compose v2.27.1**
> 上实测：`docker load`、镜像内 `selfcheck --runtime --strict`（46/46）、服务启动、
> 登录、模型问答、MySQL/Redis 连接、附件上传解析、静态前端均通过。

安装后按顺序执行：

```bash
docker run --rm <镜像> python /app/deploy/selfcheck.py --runtime --strict
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
./smoke.sh http://127.0.0.1:8787

docker compose --env-file stack.env -f docker-compose.yaml \
  exec -T qaapp python -m qaapp config --cwd /app --config-dir /app/config/backend
docker compose --env-file stack.env -f docker-compose.yaml \
  exec -T qaapp python -m qaapp datasource check --cwd /app --config-dir /app/config/backend
```

功能冒烟：

| 能力 | 操作 | 期望 |
| --- | --- | --- |
| 模型对话 | 发一句普通问题 | 正常流式返回 |
| 历史 | 刷新/重启容器后打开会话 | 历史仍在 |
| 附件 | 上传 `.docx`/`.xlsx` | 解析出文本，可用于分析 |
| 数据源 | 问一个业务指标 | `db_query` 返回数据 |
| 图表 | 要求画图 | PNG 中文正常 |
| 脚本 | 要求用 Python/Node 计算 | `run_script` 执行成功 |
| 报告 | 要求生成 Word 报告 | `outputs/` 下生成 docx |
| 外部 Agent | 问专业问题 | 能触发/或明确降级 |
| 登录 | 退出后重新登录 | 正常，viewer 只读 |

---

## 11. 已知限制

- **单实例**：会话/审批状态在进程内存，不能多副本；横向扩展需要共享存储 + 消息总线改造。
- **无历史迁移**：本方案只迁移配置与密钥；旧服务器的 `~/.ohmo` 会话不在包内。需要时单独拷贝 `sessions/` 与 `conversations/`。
- **Docker 沙箱不可用**：容器内没有 Docker daemon；`sandbox.enabled=true` 会失败。默认关闭。
- **TUI/渠道不含**：`abc` 的 React TUI、`abcag` 的 Telegram/Slack/飞书渠道不在镜像内。
- **Node 只带运行时**：镜像内有 `node` 但没有 `npm`；`run_script` 的 .js 只能用 Node 内置模块，
  需要第三方包时用 Python 脚本，或在会话里 `pip install --user`（会落在 `data/home`）。
- **容器内非 root**：agent 不能 `apt-get install`；需要系统包时请扩展镜像。
- **`/app` 是临时的**：agent 直接写在工程目录的文件会随容器重建丢失；交付物应放会话
  `outputs/`（`data/conversations/<id>/outputs/`），才会被持久化与备份。
- **MCP 未配置**：`mcp_servers` 为空；需要时在 `~/.openharness/settings.json`（挂载进 `/data/home/.openharness/`）配置。
- **前端与后端同源**：由后端托管 `dist`；分域名部署需反代并显式配置 `QAAPP_CORS_ORIGINS`。
- **离线镜像为单层**：`docker load` 后约 1.5 GB；压缩包约 501 MB。升级时整层替换，
  不能做增量层复用。
- **离线镜像不含 jq/less**：git/curl/rg 已包含；jq 可用 `python -m json.tool` 替代。
- **镜像构建需 Docker（仅 Docker 路径）**：如果构建机没有 Docker，请使用
  `deploy/build_offline.sh`；它不执行 Linux 二进制，只做文件装配。

---

## 12. 排障

| 现象 | 排查 |
| --- | --- |
| `healthz` 不通 | `compose logs --tail=200 qaapp`；端口占用；镜像 tag 是否存在 |
| 容器起不来，日志提示静态目录 | `QAAPP_STATIC_DIR=/app/frontend/webapp/dist` 是否被覆盖 |
| 登录 403 `cross_site` | `--public-url`/`QAAPP_CORS_ORIGINS` 与实际入口不一致；改后重启 |
| 登录 401 / Cookie 丢失 | 是否 HTTPS 但未开 `QAAPP_AUTH_TRUST_PROXY`；反代是否剥掉 `Set-Cookie` |
| `datasource check` 失败 | 目标机到 DB/Redis 的网络、账号授权、密码；`--skip-preflight` 仅临时 |
| 容器内连不上宿主上的 DB | 容器里的 `127.0.0.1` 是容器自身；填宿主内网 IP，或改用 `network_mode: host`（端口映射会失效） |
| 模型 401/超时 | `OPENHARNESS_BASE_URL`、Key、出口网络；`selfcheck --network --strict` |
| 图表中文方框 | 是否用了自定义基础镜像；容器内执行 `fc-list \| grep -i noto` |
| 上传解析失败 | `.doc` 不支持，请转 `.docx`；检查文件大小/后缀白名单 |
| `run_script` Node 失败 | `docker exec qaapp node --version`；镜像是否被替换 |
| 数据目录权限 | `chown -R 1000:1000 data config` |
| SELinux 主机挂载被拒 | 给 compose 卷加 `:z`，或临时 `security_opt: [label=disable]` |
| 升级后旧页面 | 浏览器缓存；镜像内 dist 是否为新构建 |
| 磁盘涨满 | 日志轮转、`cleanup.sh --apply`、备份保留份数 |

---

## 13. 相关文件

- 构建脚本：[`deploy/build.sh`](../../deploy/build.sh)
- Dockerfile：[`deploy/docker/Dockerfile`](../../deploy/docker/Dockerfile)
- 安装脚本：[`deploy/bundle/install.sh`](../../deploy/bundle/install.sh)
- 配置改写：[`deploy/configure.py`](../../deploy/configure.py)
- 自检：[`deploy/selfcheck.py`](../../deploy/selfcheck.py)
- 原有部署文档：[`DEPLOYMENT.zh-CN.md`](DEPLOYMENT.zh-CN.md)
