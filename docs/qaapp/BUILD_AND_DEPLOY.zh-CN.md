# qaapp 镜像构建与内网部署

> 场景：打包服务器上已经导入一个**完整基础镜像**（代码 + Python 依赖 + Node +
> 字体 + 系统库），以后只在这台服务器上用 Docker 做**增量构建**，不下载基础镜像、
> 不下载 pip 依赖、不需要外网。
>
> 目标机只做：`docker load` + `run.sh`。部署包自带
> `config/backend/.env`，**不需要 vim**。
>
> `deploy/` 只有 5 个文件（含 README）：
>
> ```text
> deploy/
> ├── build.sh          # 唯一构建入口：docker build + docker save
> ├── Dockerfile        # FROM 本地基础镜像，COPY 当前代码
> ├── entrypoint.sh     # 容器入口
> ├── run.sh            # 目标机启动（挂载包内 config/backend）
> └── README.md
> ```

---

## 1. 原理

```text
基础镜像 openharness-qaapp:0.1.9-offline
  ├─ Python 3.11 + 全部依赖（/app/vendor）
  ├─ Node 20、ripgrep、Noto CJK、git/curl/bash
  └─ 旧代码

docker build（FROM 基础镜像，--network none）
  └─ COPY 当前 src/ohmo/qaapp/skills/plugins/scripts/deploy/frontend dist
       → 新镜像（只多一个代码层）

docker save → dist/qaapp-image-<version>-<timestamp>-amd64.tar.gz
```

- **基础镜像**是“环境”，只需导入一次；保留好它的 tar。
- **代码更新**只 `COPY` 覆盖，秒级~分钟级完成，不联网。
- **环境配置**由构建时 `--env dev|pro` 选定，复制到部署包
  `config/backend/.env`；目标机直接使用，不需要手工改。
- **依赖/Node/字体变更**不属于增量构建范围，需要重新准备基础镜像。

---

## 2. 产物

```text
dist/
├── qaapp-image-<version>-<timestamp>-amd64.tar.gz
└── qaapp-simple/
    ├── qaapp-image-<version>-<timestamp>-amd64.tar.gz
    ├── run.sh
    ├── README.md
    ├── QUICK_DEPLOY.zh-CN.md
    ├── BUILD_AND_DEPLOY.zh-CN.md
    └── SHA256SUMS
```

镜像 tag 默认：`openharness-qaapp:<version>-<timestamp>`。

---

## 3. 前置：导入基础镜像（只一次）

在打包服务器：

```bash
mkdir -p /root/qaapp-simple
cd /root/qaapp-simple
docker load -i qaapp-base-image.tar.gz
docker images | grep openharness-qaapp
```

默认基础镜像 tag：`openharness-qaapp:0.1.9-offline`。

如果导入后的 tag 不同，构建时用 `--base <tag>` 指定。
基础镜像 tar 请保留，后续换机器/重装时还要用。

检查：

```bash
docker image inspect openharness-qaapp:0.1.9-offline --format '{{.Id}} {{.Size}}'
```

---

## 4. 构建前检查

```bash
cd /root/workspace/workspace/OpenHarness
git pull
git log -1 --oneline

test -f frontend/webapp/dist/index.html && echo "dist OK"
docker image inspect openharness-qaapp:0.1.9-offline >/dev/null && echo "base OK"
```

### 4.1 前端产物

镜像使用**构建好的** `frontend/webapp/dist`。如果前端代码有改动：

```bash
cp config/frontend/.env_prod config/frontend/.env
cd frontend/webapp
npm ci
npm run build
cd ../..
```

> `VITE_API_BASE_URL` 必须留空（同源）。后端代码改动不需要 npm。

### 4.2 环境文件

```text
config/backend/env_files/
├── env_dev    # 开发环境
└── env_pro    # 生产环境
```

这两个文件含 DB/Redis/模型 Key，已加入 `.gitignore`，不提交。
构建时 `--env dev|pro` 把选中的文件复制为 `config/backend/.env`，
并放入部署包 `dist/qaapp-simple/config/backend/.env`。

`.env` 不进入镜像；镜像只带 `config/backend/*.json` 和 `.env_prod` 模板。

---

## 5. 构建（打包服务器）

```bash
cd /root/workspace/workspace/OpenHarness
./deploy/build.sh --env pro
# 或
./deploy/build.sh --env dev
```

脚本执行：

1. 检查 `docker` 和本地基础镜像；
2. 检查 `--env dev|pro`，把 `config/backend/env_files/env_<env>`
   复制为 `config/backend/.env`；
3. 检查 `frontend/webapp/dist/index.html`；
4. `docker build --network none`：
   - `FROM openharness-qaapp:0.1.9-offline`
   - `COPY` 当前 `src/`、`ohmo/`、`qaapp/`、`skills/`、`plugins/`、`scripts/`、`deploy/`
   - `COPY frontend/webapp/dist`
   - `COPY config/backend/*.json`（不含 `.env`）
   - 覆盖 `ENTRYPOINT /app/deploy/entrypoint.sh`
5. `docker run --rm ... python -c "import qaapp, openharness, ohmo"` 做导入检查；
6. `docker save | gzip` 输出镜像 tar；
7. 整理 `dist/qaapp-simple/`：镜像、`config/backend/.env`、`run.sh`、
   文档、校验和。

**不下载任何东西**；`--network none` 保证构建过程无外网。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--env` | **必填**：`dev` 或 `pro`，选择 `env_files/env_dev`/`env_pro` |
| `--base` | 基础镜像 tag；默认 `openharness-qaapp:0.1.9-offline` |
| `--tag` | 输出镜像 tag；默认 `openharness-qaapp:<version>-<timestamp>` |
| `--output` | 镜像 tar；默认 `dist/qaapp-image-<version>-<timestamp>-amd64.tar.gz` |

示例：

```bash
./deploy/build.sh \
  --env pro \
  --base openharness-qaapp:0.1.9-offline \
  --tag openharness-qaapp:0.1.9-20261106 \
  --output dist/qaapp-image-0.1.9-20261106-amd64.tar.gz
```

> 输出 tag 不能等于基础镜像 tag，脚本会拒绝，避免覆盖基础镜像。

---

## 6. 本机校验

```bash
cd dist/qaapp-simple
sha256sum -c SHA256SUMS      # Linux
shasum -a 256 -c SHA256SUMS  # macOS
```

检查镜像：

```bash
docker image inspect openharness-qaapp:<tag> --format '{{.Config.Entrypoint}}'
# [/app/deploy/entrypoint.sh]
```

不需要在打包服务器启动服务；目标机 `docker load` 后启动。

---

## 7. 部署（目标机两步）

### 第 1 步：加载镜像

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-<version>-<timestamp>-amd64.tar.gz
docker images | grep openharness-qaapp
```

### 第 2 步：启动

```bash
cd /root/qaapp-simple
./run.sh
```

`run.sh` 默认挂载同目录的 `config/backend`；构建时已经选好 dev/pro，
**不需要再改 `.env`**。

默认：

- 配置：`/root/qaapp-simple/config/backend`（打包时已选好）
- 数据：`/root/qaapp-data`
- 端口：`8787`
- 容器：`qaapp`

8787 被旧进程占用：

```bash
HOST_PORT=18790 ./run.sh
```

或直接：

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/qaapp-simple/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:<tag>
```

> `--user 0:0` 用于读取包内 root 0600 的 `.env`。
> 非 root：`chown -R 1000:1000 config/backend`，去掉 `--user 0:0`。

### 可选：部署后改配置

只有确实要换库/改密码时，编辑 `/root/qaapp-simple/config/backend/.env`，
然后 `docker restart qaapp`。首次部署不需要这一步。

## 8. 验证

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

## 9. 改动后怎么做

| 改了什么 | 怎么做 |
| --- | --- |
| `src/`、`ohmo/`、`qaapp/` | `git pull` → `./deploy/build.sh --env pro` |
| `frontend/webapp` | `npm run build` → `./deploy/build.sh --env pro` |
| `skills/`、`plugins/`、`scripts/` | `git pull` → `./deploy/build.sh --env pro` |
| `config/backend/env_files/env_dev`/`env_pro` | 改文件 → 重新 `--env dev/pro` 构建 |
| 部署包内 `config/backend/.env`（临时换库） | 改包内文件 → `docker restart qaapp` |
| `config/backend/*.json` | 改配置 → 重新构建或改包内文件后重启 |
| `run.sh` | 重新拷贝脚本，不重建镜像 |
| 依赖/Node/字体/系统库 | 需要新的基础镜像（增量构建不处理） |

**环境配置在打包时选定；首次部署不需要改目标机配置。**

---

## 10. 升级 / 回滚 / 备份

### 升级

```bash
# 打包服务器
git pull
./deploy/build.sh --env pro

# 目标机
cd /root/qaapp-simple
docker load -i qaapp-image-<new>.tar.gz
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
  /root/qaapp-simple/config/backend \
  /root/qaapp-data
```

---

## 11. 构建常见问题

| 现象 | 处理 |
| --- | --- |
| `基础镜像不在本地` | `docker load -i qaapp-base-image.tar.gz`；或 `--base <tag>` |
| `frontend/webapp/dist/index.html` 不存在 | 先 `npm ci && npm run build` |
| `docker build` 报 `--network none` | 确认 Docker 支持；本构建不需要网络 |
| 输出 tag 不能等于基础镜像 | 换 `--tag`；不要覆盖基础镜像 |
| 构建很慢 | 首次 COPY 层较大；后续代码层缓存会快 |
| 代码没更新 | 确认 `git pull`；确认 `frontend dist` 已重建；检查 `docker build` 输出 |
| 磁盘不足 | `docker image prune`；清理 `dist/` 旧镜像 |

## 12. 部署常见问题

| 现象 | 处理 |
| --- | --- |
| 读不到配置 `Permission denied` | `--user 0:0`，或 `chown -R 1000:1000 config/backend` |
| 8787 被占用 | 停旧进程，或 `HOST_PORT=18790` |
| `healthz` 不通 | `docker logs --tail=100 qaapp` |
| DB/Redis 连不上 | 改 `.env` 后 `docker restart qaapp`；检查网络 |
| 模型 401/超时 | 检查 Key/`OPENHARNESS_BASE_URL` |
| 登录 403 `cross_site` | 改 `QAAPP_CORS_ORIGINS` 为实际来源 |
| HTTPS 登录掉线 | 设 `QAAPP_AUTH_TRUST_PROXY=true` |
| 中文图表方框 | 使用本项目基础镜像（已带 Noto CJK） |
| 默认密码不生效 | `data/users.json` 已存在；删除会重置账户 |

---

## 13. 安全与限制

- 镜像不含 `.env`；配置挂载，按密钥文件管理。
- 首次登录立即改密；默认 `admin / Admin@Qa2026`。
- 公网访问前置 HTTPS 反代，设 `QAAPP_AUTH_TRUST_PROXY=true`。
- `QAAPP_CORS_ORIGINS` 用显式来源。
- MySQL 只读账号；Redis 只读 db0。
- 单实例，不能 `--scale`。
- 不迁移旧 `~/.ohmo` 历史。
- 增量构建只更新代码；依赖/Node/字体变更需要新的基础镜像。
- 基础镜像 tar 要保留，换机器/重装时先 `docker load`。

---

## 14. 本次实测

| 项 | 结果 |
| --- | --- |
| 目标机 | CentOS 7.9、Docker 26.1.4 |
| 基础镜像 | `openharness-qaapp:0.1.9-offline`，约 1.47 GB |
| 镜像加载 | 成功；entrypoint `/app/deploy/entrypoint.sh` |
| 直接挂项目配置运行 | healthz/首页/静态资源 200 |
| 登录/模型 | admin 登录成功；SSE 返回 `OK` |
| MySQL/Redis | 142 张表 / 36351 keys，正常 |

---

## 15. 相关文件

- 构建入口：[`deploy/build.sh`](../../deploy/build.sh)
- 增量 Dockerfile：[`deploy/Dockerfile`](../../deploy/Dockerfile)
- 容器入口：[`deploy/entrypoint.sh`](../../deploy/entrypoint.sh)
- 目标机启动：[`deploy/run.sh`](../../deploy/run.sh)
- 快速部署：[`QUICK_DEPLOY.zh-CN.md`](QUICK_DEPLOY.zh-CN.md)
- 原有裸机部署：[`DEPLOYMENT.zh-CN.md`](DEPLOYMENT.zh-CN.md)
