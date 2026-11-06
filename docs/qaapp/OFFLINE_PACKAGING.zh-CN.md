# qaapp 离线镜像打包与部署操作手册

> **如果只想“load 镜像 + 改项目配置”**，直接看
> [`QUICK_DEPLOY.zh-CN.md`](QUICK_DEPLOY.zh-CN.md)；从源码构建/离线重建看
> [`BUILD_AND_DEPLOY.zh-CN.md`](BUILD_AND_DEPLOY.zh-CN.md)；本文是完整构建/高级方案
> （包含一键安装包、升级、备份、排障），不是日常最短路径。
>
> 适用场景：**构建机有外网、目标服务器在内网且不能访问外网**，目标机只安装
> Docker + Docker Compose v2，不执行 `docker build`，不下载 pip/npm/apt/yum 依赖。
>
> 本文是“照着做”的完整步骤；设计说明、依赖矩阵和更多排障见
> [`IMAGE_DEPLOY.zh-CN.md`](IMAGE_DEPLOY.zh-CN.md)。

---

## 0. 一页速查

**构建机（本机）**

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
./deploy/build_offline.sh --tag openharness-qaapp:0.1.9-offline
```

产物：

```text
dist/qaapp-image-openharness-qaapp_0.1.9-offline-amd64.tar.gz    # 约 501 MB
dist/qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64.tar.gz   # 约 498 MB
dist/SHA256SUMS
```

**目标机（内网）**

```bash
cd /root
sha256sum -c SHA256SUMS
tar xzf qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64.tar.gz
cd qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64

./install.sh \
  --db-host 47.117.162.103 --db-port 13306 \
  --db-user <只读用户> --db-password '<密码>' --db-name ai_search_fj \
  --redis-host 47.117.162.103 --redis-port 16379 --redis-password '<密码>' \
  --port 8787 \
  --public-url http://192.168.139.144:8787
```

安装脚本会校验 → `docker load` → 镜像运行期自检 → 写配置 → 数据源/网络检查 →
启动 → 健康检查 → 冒烟。**全程不需要外网**。

> 如果目标机 8787 已被旧进程占用（本次服务器就是旧 python3 进程），先按 §7.1 停旧进程，
> 或把 `--port` 改为 `18790` 先并行验证。

---

## 1. 方案说明

### 1.1 为什么这样打包

本机没有 Docker，目标机又不能访问外网，因此不在目标机做 `docker build`。
`deploy/build_offline.sh` 在构建机上：

1. 从 Docker 镜像代理下载 `python:3.11-bookworm`（linux/amd64）的全部 layers；
2. 展开成完整 rootfs；
3. 用 `pip download` 拉取 **linux cp311 wheels**，再 `pip install --target /app/vendor`
   装进 rootfs（只写文件，不执行 Linux 二进制）；
4. 放入 Node 20 linux-x64、ripgrep 静态二进制、Noto Sans CJK 字体；
5. 复制应用源码、前端 `dist`、skills/plugins/scripts/deploy；
6. 生成 docker-save 兼容的**单层镜像**（`manifest.json` + `config.json` + `layer.tar`）。

目标机 `docker load` 后直接可用，不依赖任何外网。

### 1.2 镜像内容

| 内容 | 说明 |
| --- | --- |
| Python 3.11.18 | base 镜像自带 |
| Python 依赖 | `/app/vendor`，来自 linux cp311 wheels |
| 应用代码 | `/app/src`、`/app/ohmo`、`/app/qaapp` |
| 前端 | `/app/frontend/webapp/dist` |
| Node 20.19 | `/usr/local/bin/node`，供 `run_script` 的 .js |
| ripgrep 15.2 | `/usr/local/bin/rg` |
| 中文字体 | `/usr/share/fonts/opentype/noto/NotoSansCJK-*.ttc` |
| git / curl / bash / tzdata | 来自 `python:3.11-bookworm` 基础镜像 |
| 技能/插件 | `/app/skills`、`/app/plugins` |

**不包含**：`.env`、历史会话、MySQL/Redis、abc TUI、abcag 渠道、Docker 沙箱。
离线镜像也不含 `jq`/`less`（非必需；`git/curl/rg` 已包含）。

### 1.3 运行形态

```text
浏览器 ──HTTP/SSE/WS──> qaapp 容器 :8787
                         ├─ /app/frontend/webapp/dist
                         ├─ 模型网关（OPENHARNESS_BASE_URL）
                         ├─ 外部 Agent（agents.json）
                         ├─ MySQL（只读）
                         └─ Redis（db0，只读）
配置：./config/backend -> /app/config/backend:ro
数据：./data -> /data
```

- 单进程会话池，**只能跑 1 个副本**，不能 `--scale`。
- 配置只读挂载；改配置后重启容器。
- 数据持久化在 `data/`，容器重建不丢。

---

## 2. 环境准备

### 2.1 构建机（本机）

| 项 | 要求 |
| --- | --- |
| OS | macOS / Linux 均可 |
| Python | 3.9+，带 pip（`python3 -V && python3 -m pip -V`） |
| Docker | **不需要** |
| 磁盘 | 至少 8–10 GB 空闲（缓存 + layer + 压缩包） |
| 网络 | 能访问 Docker 镜像代理、PyPI、Node、Debian 镜像 |

检查：

```bash
python3 -V
python3 -m pip -V
uname -m
df -h .
```

默认使用的源：

| 用途 | 默认地址 |
| --- | --- |
| Docker 镜像代理 | `https://docker.m.daocloud.io` |
| 镜像认证 | `https://m.daocloud.io/auth/token` |
| PyPI | `https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple` |
| Node | `https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/` |
| 字体 | 同镜像站 Debian `fonts-noto-cjk` |

如果以上地址在你所在网络不可达，见 §4.6 自定义源。

### 2.2 目标机

| 项 | 要求 |
| --- | --- |
| OS / CPU | Linux x86_64（CentOS 7 / Ubuntu 均可；实测 CentOS 7.9） |
| Docker | Engine 20.10+（实测 26.1.4） |
| Compose | v2（`docker compose`，实测 v2.27.1） |
| 资源 | 建议 2–4 vCPU / 4 GB RAM / 20 GB 磁盘 |
| 权限 | root 或 sudo |
| 网络 | **不需要外网**；需要能访问 MySQL/Redis/模型网关/外部 Agent |

检查：

```bash
uname -m
docker version
docker compose version
df -h /
ss -ltnp | grep 8787 || true
```

### 2.3 外部服务（部署前确认）

| 服务 | 需要确认 |
| --- | --- |
| MySQL | 只读账号、密码、数据库名；允许目标机访问；账号有 `SELECT` |
| Redis | 地址、端口、密码；只读、`db0`；允许目标机访问 |
| 模型网关 | `OPENHARNESS_BASE_URL` 可达；Key 有效 |
| 外部 Agent | `agents.json` 的三个 endpoint 可达（当前 `192.168.139.144:18081/18094`） |
| Bing / 公网 | `web_search`/`web_fetch` 需要；没有则相关能力降级 |

目标机到外部服务的连通性，安装脚本会自动做 TCP 检查（`selfcheck --network`）。

---

## 3. 打包前检查（本机）

### 3.1 代码状态

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
git status --short
git log -1 --oneline
```

建议先提交当前改动，保证打包内容可追溯。本次交付提交为：

```text
b7671b9 feat: 增加 qaapp 离线单层镜像与部署包
```

### 3.2 前端产物

镜像里的前端是 `frontend/webapp/dist`，不是运行时 `npm run dev`。确认：

```bash
test -f frontend/webapp/dist/index.html && echo "dist OK"
```

如果不存在或前端代码有改动：

```bash
cp config/frontend/.env_prod config/frontend/.env
cd frontend/webapp
npm ci
npm run build
cd ../..
```

> 构建使用 `config/frontend/.env_prod`，`VITE_API_BASE_URL` 必须留空（同源）。

### 3.3 配置

确认 `config/backend/.env` 存在，至少有：

```bash
grep -E '^(OPENHARNESS_PROVIDER|OPENHARNESS_MODEL|OPENHARNESS_BASE_URL|OPENHARNESS_AUTH_SOURCE|OPENHARNESS_DASHSCOPE_API_KEY|OPENHARNESS_API_KEY)=' config/backend/.env
```

- 模型 Key 必须有效；否则镜像能启动，但问话失败。
- `DB_*`/`REDIS_*` 在打包时可以保持占位，安装时用 `--db-*`/`--redis-*` 覆盖。
- 外部 Agent 地址会随包带走；确认目标机网络可达。
- `.env` 含密钥；它只进入部署包，不进入镜像。

校验：

```bash
python -m qaapp config
python -m qaapp agents list
python -m qaapp dangerous validate
```

### 3.4 磁盘

```bash
df -h .
```

首次构建约需要：

- 基础镜像 layers：约 361 MB
- 展开 rootfs + 依赖安装 + layer：约 2.5–3 GB
- 镜像 tar：约 501 MB
- 部署包：约 498 MB

建议预留 8–10 GB；构建缓存默认在 `/tmp/openharness-offline`，可随时删除。

---

## 4. 构建离线镜像（本机）

### 4.1 标准命令

```bash
cd /Users/xulele/Documents/workspace/OpenHarness
./deploy/build_offline.sh --tag openharness-qaapp:0.1.9-offline
```

首次构建会下载基础镜像和 wheels，耗时取决于网络：

| 阶段 | 大致耗时 |
| --- | --- |
| 下载基础镜像（361 MB） | 1–5 分钟 |
| 下载 wheels（约 91 MB） | 1–3 分钟 |
| 下载 Node（26 MB）/字体（54 MB）/rg | 1–2 分钟 |
| 安装 vendor + 打包 rootfs | 2–5 分钟 |
| 生成镜像 tar + 部署包 | 2–4 分钟 |

> 实际耗时受网络和磁盘影响；慢时优先检查镜像代理/PyPI 速度。

### 4.2 产物

```text
dist/qaapp-image-openharness-qaapp_0.1.9-offline-amd64.tar.gz
dist/qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64.tar.gz
dist/SHA256SUMS
```

命名规则：

- `TAG` 中的 `/` 和 `:` 会被替换成 `_` 作为文件名；
- `qaapp-image-*` 是可直接 `docker load` 的单层镜像；
- `qaapp-deploy-*` 是完整部署包（内含镜像、compose、配置、脚本）。

### 4.3 构建脚本做了什么

1. 拉取 `python:3.11-bookworm` amd64 layers；
2. 展开 rootfs（处理 whiteout）；
3. `pip download` 下载 linux cp311 wheels；
4. `pip install --target rootfs/app/vendor` 安装依赖；
5. 放入 Node/rg/字体；
6. 复制代码、`dist`、skills/plugins/scripts；
7. 添加 `app` 用户（UID 1000）、`/data` 目录、`/etc/passwd`；
8. 写 `config.json`/`manifest.json`，生成单层镜像 tar；
9. 组装部署包、写 `SHA256SUMS`。

### 4.4 常用参数

| 参数 | 说明 |
| --- | --- |
| `--tag` | 镜像 tag；默认 `openharness-qaapp:<version>-offline` |
| `--out-dir` | 产物目录；默认 `dist` |
| `--work-dir` | 构建缓存目录；默认 `/tmp/openharness-offline` |
| `--refresh` | 重新下载/展开基础镜像与依赖 |
| `--clean-work` | 构建前清空 work-dir（会删除缓存） |
| `--reuse-image` | 复用已有镜像 tar，只重新组装部署包 |
| `--registry` / `--auth-url` | 自定义 Docker 镜像代理 |
| `--pip-index-url` | 自定义 PyPI |
| `--node-url` | 自定义 Node tar.xz |
| `--font-url` | 自定义 fonts-noto-cjk deb |
| `--base-image` | 替换基础镜像（默认 `python:3.11-bookworm`） |

### 4.5 只重新组装部署包

如果只改了 `deploy/bundle/install.sh`、`install` 参数、配置种子或文档，**不需要重建镜像**：

```bash
./deploy/build_offline.sh \
  --tag openharness-qaapp:0.1.9-offline \
  --reuse-image
```

如果改了应用 Python 代码、前端 `dist`、依赖、字体，或 `deploy/offline/build_image.py`，
必须重新构建镜像（去掉 `--reuse-image`）。

### 4.6 自定义镜像源（网络受限时）

```bash
./deploy/build_offline.sh \
  --tag openharness-qaapp:0.1.9-offline \
  --registry https://your-docker-mirror \
  --auth-url https://your-auth/token \
  --pip-index-url https://your-pypi/simple \
  --node-url https://your-node/v20.19.0/node-v20.19.0-linux-x64.tar.xz \
  --font-url https://your-debian/fonts-noto-cjk_20240730+repack1-1_all.deb
```

`--auth-url` 的 token 服务需要支持匿名拉取；如果镜像站需要账号，请先在构建机上
配置可用的代理/账号，或改用能匿名访问的镜像站。

### 4.7 构建失败时

| 现象 | 处理 |
| --- | --- |
| 下载基础镜像超时/403 | 换 `--registry`/`--auth-url`；确认能访问 `v2/` |
| `pip download` 找不到 wheel | 检查 `--pip-index-url`；不要用 `--no-binary` |
| `No matching distribution` | 确认目标平台是 `manylinux_2_28_x86_64`/`manylinux2014_x86_64` |
| 字体解包失败 | 换 `--font-url`；确认是 Debian `fonts-noto-cjk` deb |
| Node 解包失败 | 换 `--node-url`；确认文件名含 `linux-x64.tar.xz` |
| 磁盘不足 | 换 `--work-dir`，删 `/tmp/openharness-offline` 后重试 |
| 上次中断留下半包 | `--refresh`；或 `--clean-work` 后重试 |

---

## 5. 本机验收产物

### 5.1 校验和

macOS：

```bash
cd dist
shasum -a 256 -c SHA256SUMS
```

Linux：

```bash
cd dist
sha256sum -c SHA256SUMS
```

期望两行都 `OK`。本次构建：

```text
9b30253986bab93e2aade49c471c7d208732abf91da4cbc15f0483ccf6f4f916  qaapp-image-...tar.gz
5e5f5dcc6e2caad76fd453a5eed3336761203a123417860d2a3656a6888290d0  qaapp-deploy-...tar.gz
```

> 重新构建后哈希会变，以你本机 `dist/SHA256SUMS` 为准。

### 5.2 检查部署包结构

```bash
tar tzf dist/qaapp-deploy-*.tar.gz | head -30
```

应包含：

```text
config/backend/.env
config/backend/agents.json
config/backend/webapp.json
config/backend/datasources.json
config/backend/dangerous-actions.json
config.seed/backend/...
docker-compose.yaml
install.sh
upgrade.sh
backup.sh
cleanup.sh
uninstall.sh
smoke.sh
bundle.meta
qaapp-image-...tar.gz
```

### 5.3 检查镜像 tar（可选）

```bash
tar tzf dist/qaapp-image-*.tar.gz
# manifest.json
# config.json
# layer.tar
```

如果想确认 `diff_ids` 与 layer 一致：

```bash
python3 - <<'PY'
import hashlib, json, tarfile
p = "dist/qaapp-image-openharness-qaapp_0.1.9-offline-amd64.tar.gz"
with tarfile.open(p, "r:gz") as t:
    cfg = json.loads(t.extractfile("config.json").read())
    h = hashlib.sha256()
    f = t.extractfile("layer.tar")
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
    print("diff match:", "sha256:" + h.hexdigest() == cfg["rootfs"]["diff_ids"][0])
PY
```

### 5.4 本机快速验证镜像内容（可选）

本机没有 Docker，不能 `docker run`；可以只检查 tar 内容：

```bash
tar xOf dist/qaapp-image-*.tar.gz config.json | head
```

真正的运行验证在目标机安装时自动做（`install.sh` 会先跑
`selfcheck --runtime --strict`）。

---

## 6. 传输到目标机

### 6.1 scp

```bash
cd /Users/xulele/Documents/workspace/OpenHarness/dist
scp qaapp-image-*.tar.gz qaapp-deploy-*.tar.gz SHA256SUMS \
  root@192.168.139.144:/root/
```

本次实测：约 500 MB/个，内网约 30 秒/个。

### 6.2 目标机校验

```bash
cd /root
sha256sum -c SHA256SUMS
```

两行都必须是 `OK`；否则重新传输。

### 6.3 无 scp 时

用 U 盘/堡垒机/跳板机拷贝，保持两个 tar 和 `SHA256SUMS` 在同一目录即可。

---

## 7. 目标机安装

### 7.1 安装前检查

```bash
uname -m                 # 必须是 x86_64
docker version
docker compose version
df -h /
ss -ltnp | grep 8787 || true
```

**如果 8787 已被旧进程占用**（本机当前就是旧 python3 进程），先停旧进程：

```bash
ss -ltnp | grep :8787
# 确认 PID 属于旧 qaapp 后：
kill <PID>
# 或按实际 systemd/supervisor 方式停止
```

不确定时先用 `--port 18790` 并行验证，确认新服务正常后再切换 8787。

旧数据/配置建议先备份：

```bash
cp -a /root/workspace/workspace/OpenHarness/config/backend /root/config-backup-$(date +%F)
```

### 7.2 解压

```bash
cd /root
tar xzf qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64.tar.gz
cd qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64
ls -lh
```

> 目录名取决于 `--tag`；用实际解压出来的目录。

### 7.3 安装（交互式）

```bash
./install.sh
```

按提示输入：

- MySQL 主机/端口/库/用户/密码
- Redis 主机/端口/密码（留空则关闭 Redis）
- 浏览器访问地址（同源可留空）
- 宿主端口
- admin 初始密码（留空自动生成）

### 7.4 安装（非交互式，推荐生产）

```bash
./install.sh \
  --db-host 47.117.162.103 --db-port 13306 \
  --db-user <只读用户> --db-password '<密码>' --db-name ai_search_fj \
  --redis-host 47.117.162.103 --redis-port 16379 --redis-password '<密码>' \
  --port 8787 \
  --public-url http://192.168.139.144:8787 \
  --admin-password '<初始管理员密码>' \
  --viewer-password '<初始只读密码>' \
  --non-interactive
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `--db-host/--db-port/--db-user/--db-password/--db-name` | 现有 MySQL（只读账号） |
| `--redis-host/--redis-port/--redis-password` | 现有 Redis（db0） |
| `--no-redis` | 关闭 Redis 数据源 |
| `--bind` / `--port` | 宿主监听地址/端口，默认 `0.0.0.0` / `8787` |
| `--public-url` | 浏览器访问地址，写入 `QAAPP_CORS_ORIGINS` |
| `--trust-proxy` | 前置 HTTPS 反代时开启 |
| `--admin-password` / `--viewer-password` | 首次生成 `users.json` 的初始密码 |
| `--non-interactive` | 不做交互式询问，缺参直接失败 |
| `--skip-preflight` | 跳过 MySQL/Redis/外部服务连通性检查（仅排障） |
| `--skip-checksum` | 跳过镜像 SHA256 校验（仅排障） |
| `--force-config` | 用 `config.seed` 覆盖当前 `config/`（旧配置会备份） |
| `--image` / `--image-tar` | 手动指定镜像 tag / tar（一般不用） |

### 7.5 安装脚本做了什么

1. 检查 root、Docker、Compose v2、端口、镜像 tar；
2. 校验 `SHA256SUMS` 中的镜像行；
3. `docker load`；
4. 校验镜像架构 `linux/amd64`；
5. `docker run --rm ... selfcheck.py --runtime --strict`（依赖/字体/Node/静态资源）；
6. 初始化 `config/`、`data/`（只补缺失，不覆盖已有 `.env`）；
7. 设置 UID 1000 权限；
8. 容器内 `configure.py` 写 `.env`（DB/Redis/CORS/静态目录），按 `--no-redis` 改
   `datasources.json`；
9. `selfcheck --config --strict`；
10. `datasource check`（MySQL/Redis 鉴权与只读检查）；
11. `selfcheck --network`（模型/外部 Agent/DB/Redis TCP）；
12. `docker compose up -d --force-recreate --wait`；
13. `/healthz` 轮询 + `smoke.sh`；
14. 打印访问地址、初始密码、配置/数据路径。

`--skip-preflight` 只跳过第 10/11 步；第 5/9 步仍会执行。

### 7.6 期望输出

```text
[INFO] 校验镜像文件...
qaapp-image-...tar.gz: OK
[INFO] 加载镜像 ...
Loaded image: openharness-qaapp:0.1.9-offline
[INFO] 镜像运行期自检...
合计：46 项，FAIL=0，WARN=0
[INFO] 配置自检...
合计：13 项，FAIL=0，WARN=0
[INFO] 启动服务...
Container qaapp Started
Container qaapp Healthy
[INFO] 冒烟测试...
冒烟测试通过：http://127.0.0.1:8787

[INFO] 安装完成
  访问地址：http://...
  管理员：admin / ...
  只读账号：viewer / ...
```

如果 `users.json` 已存在，脚本不会覆盖密码，会提示“初始密码不覆盖现有账户”。

---

## 8. 安装后验收

以下命令在部署目录执行；先设置：

```bash
cd /root/qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64
alias qc='docker compose --env-file stack.env -f docker-compose.yaml'
```

### 8.1 基础端点

```bash
curl -fsS http://127.0.0.1:8787/healthz
# {"status":"ok","active_sessions":0}

curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
# login_required / auth_enabled ...

curl -fsS -o /dev/null -w "index=%{http_code}\n" http://127.0.0.1:8787/
curl -fsS -o /dev/null -w "js=%{http_code}\n" http://127.0.0.1:8787/assets/index-xxxx.js
```

### 8.2 容器状态

```bash
qc ps
qc logs --tail=100 qaapp
docker inspect --format '{{.State.Health.Status}}' qaapp
```

### 8.3 配置与数据源

```bash
qc exec -T qaapp python -m qaapp config \
  --cwd /app --config-dir /app/config/backend

qc exec -T qaapp python -m qaapp datasource check \
  --cwd /app --config-dir /app/config/backend --workspace /data
```

期望：`errors` 为空；MySQL/Redis 都 OK。

### 8.4 登录

```bash
curl -sS -c /tmp/cj -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<你的密码>"}' \
  http://127.0.0.1:8787/api/v1/auth/login; echo

curl -sS -b /tmp/cj http://127.0.0.1:8787/api/v1/auth/me; echo
```

### 8.5 模型问答（SSE）

```bash
CID=$(curl -sS -b /tmp/cj -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8787/api/v1/conversations | sed -n 's/.*"conversation_id":"\([^"]*\)".*/\1/p')
echo "CID=$CID"

curl -sS -N -b /tmp/cj -H 'Content-Type: application/json' \
  -d '{"content":"只回复 OK，不要调用工具","stream":true}' \
  http://127.0.0.1:8787/api/v1/conversations/$CID/messages
```

期望最后有 `assistant_complete` / `turn_complete`，文本为 `OK`。

### 8.6 附件

```bash
printf '售电量测试附件：2026年9月福建售电量 123.45 亿千瓦时。\n' > /tmp/qaapp-attach.txt
CID2=$(curl -sS -b /tmp/cj -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8787/api/v1/conversations | sed -n 's/.*"conversation_id":"\([^"]*\)".*/\1/p')
curl -sS -b /tmp/cj -F "files=@/tmp/qaapp-attach.txt" \
  http://127.0.0.1:8787/api/v1/conversations/$CID2/attachments
```

期望 `parse_status=ok`，并在 `extracted/` 下生成文本。

### 8.7 重启持久化

```bash
qc restart qaapp
sleep 5
curl -fsS http://127.0.0.1:8787/healthz
```

刷新页面，历史会话应仍在（会话快照在 `data/sessions`）。

### 8.8 前端人工检查

- 打开 `http://<IP>:<端口>`，登录页正常；
- admin 能登录，viewer 只读；
- 新建对话、SSE 流式输出、工具过程折叠；
- 上传 Word/Excel/TXT，解析成功；
- 右侧“会话文件”抽屉能看到 `uploads/extracted/outputs`；
- 审批弹窗、选项式澄清正常；
- 结果文件可下载。

---

## 9. 日常运维

### 9.1 目录

```text
<pack>/
├── config/backend/       # 当前配置（.env + JSON）；容器只读挂载
├── config.seed/backend/  # 初始模板；--force-config 用
├── data/                 # 唯一需要长期备份的数据
│   ├── users.json
│   ├── sessions/         # 会话快照 + traces
│   ├── conversations/    # 每会话 uploads/extracted/scripts/tmp/outputs
│   ├── data/             # openharness 数据
│   ├── logs/
│   └── home/             # HOME、matplotlib 缓存
├── stack.env             # 镜像 tag / 端口 / 初始密码（0600）
├── backups/
└── docker-compose.yaml
```

### 9.2 改配置并重启

```bash
vi config/backend/.env
qc restart qaapp
```

只改 `.env` 不需要重建镜像。改 `agents.json`/`dangerous-actions.json` 同样重启。

### 9.3 日志

```bash
qc logs -f qaapp
qc logs --tail=200 qaapp
```

Docker 日志已配置轮转（50 MB × 5）。

### 9.4 备份

```bash
./backup.sh --keep 14
ls -lh backups/
```

输出 `backups/qaapp-<时间>.tar.gz`（0600），包含 `config/`、`data/`、`stack.env`。

### 9.5 恢复

```bash
qc down
tar xzf backups/qaapp-<时间>.tar.gz -C .
chown -R 1000:1000 config data
qc up -d --wait
```

### 9.6 清理会话

```bash
./cleanup.sh --older-than-days 30          # dry-run
./cleanup.sh --older-than-days 30 --apply  # 确认后执行
```

建议 cron（每天 03:00）：

```cron
0 3 * * * cd /root/qaapp-deploy-openharness-qaapp_0.1.9-offline-amd64 && ./cleanup.sh --older-than-days 30 --apply >> /var/log/qaapp-cleanup.log 2>&1
```

### 9.7 升级

```bash
# 在构建机生成新包并传到目标机后
./upgrade.sh --bundle /root/qaapp-deploy-<new>
```

- 只替换镜像并重建容器；`config/`、`data/` 不动；
- 更新本地 `config.seed`、镜像 tar、`bundle.meta`；
- 失败会打印回滚命令。

### 9.8 回滚

```bash
# 把 stack.env 的 QAAPP_IMAGE 改回旧 tag
sed -i 's|^QAAPP_IMAGE=.*|QAAPP_IMAGE=openharness-qaapp:<old>|' stack.env
qc up -d --wait
```

### 9.9 卸载

```bash
./uninstall.sh              # 只删容器，保留 data/config
./uninstall.sh --purge --yes # 删 data/config/backups/stack.env
```

### 9.10 镜像备份

```bash
docker save openharness-qaapp:0.1.9-offline | gzip -9 > /root/qaapp-image-$(date +%F).tar.gz
```

---

## 10. 常见问题

| 现象 | 处理 |
| --- | --- |
| `permission denied` 连 Docker | 用 root/sudo；确认 docker daemon 运行 |
| `docker compose` 不存在 | 安装 Compose v2 插件；脚本要求 `docker compose` |
| 端口被占用 | `ss -ltnp \| grep :8787`；停旧进程或 `--port` 换端口 |
| `sha256sum -c` 失败 | 重新传输；不要改 tar；必要时 `--skip-checksum` 仅排障 |
| 镜像架构不一致 | 构建机必须 `linux/amd64`；本包已验证 amd64 |
| `docker load` 报错 | 确认 tar 完整、Docker 版本 ≥20.10；重试 |
| 运行期自检失败 | 看具体 `[FAIL]`；多为字体/Node/依赖缺失，重建镜像 |
| `healthz` 不通 | `qc logs --tail=200 qaapp`；等待启动；看端口映射 |
| 登录 401 | 密码错；`users.json` 已存在时初始密码不覆盖，删 `data/users.json` 重置（会丢账户） |
| 登录 403 `cross_site` | `--public-url` / `QAAPP_CORS_ORIGINS` 与实际入口不一致 |
| HTTPS 下 Cookie 丢失 | 设 `QAAPP_AUTH_TRUST_PROXY=true`，反代保留 `Set-Cookie` |
| `datasource check` 失败 | 确认目标机到 MySQL/Redis 网络、账号、密码；临时 `--skip-preflight` |
| 容器内连不上宿主 DB | 容器里的 `127.0.0.1` 是容器自身；填宿主内网 IP |
| 模型 401/超时 | `OPENHARNESS_BASE_URL`、Key、出口；`qc exec ... selfcheck --network` |
| 图表中文方框 | 镜像已带 Noto；确认没换基础镜像 |
| `run_script` Node 失败 | `docker exec qaapp node --version`；镜像是否正确 |
| 上传 `.doc` 失败 | 不支持旧版 `.doc`，请转 `.docx` |
| 数据目录权限 | `chown -R 1000:1000 config data` |
| SELinux 挂载被拒 | compose 卷加 `:z`，或 `security_opt: [label=disable]` |
| 磁盘涨满 | 日志轮转、`cleanup.sh --apply`、控制备份份数、删旧镜像 |
| 容器名 `qaapp` 冲突 | 先 `docker rm -f qaapp`，或改 compose 项目名 |
| 单实例不够用 | 当前架构内存态，不能多副本；需共享存储+消息总线改造 |

---

## 11. 安全清单

1. 部署包和 `config/backend/.env` 含模型 Key/DB/Redis 密码：`0600`，按密钥文件管理，
   不要进 git/工单/聊天记录。
2. 首次登录立即改密；`users.json` 只在首次启动生成，删除会重置账户。
3. 公网访问必须前置 HTTPS 反代，设 `QAAPP_AUTH_TRUST_PROXY=true`，只暴露 443。
4. `QAAPP_CORS_ORIGINS` 用显式来源；`*` 只适合可信内网/同源。
5. MySQL 用最小权限只读账号；Redis 只读 db0。
6. 部署包不要公开分发；镜像本身不含 `.env`，可进私有仓库。
7. 容器以 UID 1000 运行；agent 不能 apt/yum。
8. 建议定期轮换模型 Key、DB/Redis 密码，并更新 `config/backend/.env` 后重启。

---

## 12. 本次实测记录

| 项 | 结果 |
| --- | --- |
| 构建机 | macOS arm64、Python 3.11.5、无 Docker |
| 目标机 | CentOS 7.9、kernel 3.10、Docker 26.1.4、Compose v2.27.1 |
| `docker load` | 成功 |
| 镜像运行期自检 | 46 项，FAIL=0，WARN=0 |
| 安装（测试端口 18790） | 成功，healthz/首页/静态资源/冒烟通过 |
| 登录 | 自定义 admin 密码登录成功 |
| 模型 | SSE 返回 `assistant_complete: OK` |
| MySQL | 47.117.162.103:13306，142 张表 |
| Redis | 47.117.162.103:16379，36351 keys |
| 外部 Agent/模型网关 | `selfcheck --network` 全部 TCP OK |
| 附件 | `.txt` 上传解析成功 |
| 备份/清理 | `backup.sh`、`cleanup.sh` 正常 |
| 升级 | `upgrade.sh --bundle` 实测通过（同版本覆盖，容器重建 + 冒烟） |
| 测试 | 全量 1439 passed，部署相关 30 passed，ruff 通过 |
| 提交 | 代码 `b7671b9`，文档 `ee61b52` |
| 生产 8787 | 现有 python3 进程未动，测试容器已清理 |

---

## 13. 命令速查

```bash
# 构建（本机）
./deploy/build_offline.sh --tag openharness-qaapp:0.1.9-offline
./deploy/build_offline.sh --reuse-image          # 只重组部署包

# 校验
cd dist && shasum -a 256 -c SHA256SUMS           # macOS
cd dist && sha256sum -c SHA256SUMS               # Linux

# 传输
scp dist/qaapp-*offline-amd64.tar.gz dist/SHA256SUMS root@<host>:/root/

# 安装
tar xzf qaapp-deploy-*.tar.gz && cd qaapp-deploy-*
./install.sh --db-host ... --db-port ... --db-user ... --db-password ... \
  --db-name ... --redis-host ... --redis-port ... --redis-password ... \
  --port 8787 --public-url http://<host>:8787

# 运维
alias qc='docker compose --env-file stack.env -f docker-compose.yaml'
qc ps && qc logs -f qaapp
./backup.sh --keep 14
./cleanup.sh --older-than-days 30
./cleanup.sh --older-than-days 30 --apply
./upgrade.sh --bundle /root/qaapp-deploy-<new>
./uninstall.sh
```

---

## 14. 相关文件

- 构建脚本：[`deploy/build_offline.sh`](../../deploy/build_offline.sh)
- 镜像组装：[`deploy/offline/build_image.py`](../../deploy/offline/build_image.py)
- 安装脚本：[`deploy/bundle/install.sh`](../../deploy/bundle/install.sh)
- 配置改写：[`deploy/configure.py`](../../deploy/configure.py)
- 自检：[`deploy/selfcheck.py`](../../deploy/selfcheck.py)
- 设计说明：[`IMAGE_DEPLOY.zh-CN.md`](IMAGE_DEPLOY.zh-CN.md)
- Docker 构建路径（备用）：[`deploy/build.sh`](../../deploy/build.sh)
