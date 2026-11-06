# qaapp 最简部署（镜像 tar + 项目配置）

> 目标机只做两件事：**`docker load` 镜像**、**改项目 `config/backend/.env` 的
> DB/Redis**，然后运行。不执行 `docker build`、不安装 Python/Node、不部署
> MySQL/Redis、不需要外网。
>
> 更完整的构建原理、排障、备份/升级说明见
> [`OFFLINE_PACKAGING.zh-CN.md`](OFFLINE_PACKAGING.zh-CN.md)；
> `deploy/bundle/install.sh` 是“一键安装包”方案，**日常不需要**。

---

## 1. 产物

在构建机 `dist/` 下：

| 文件 | 说明 |
| --- | --- |
| `qaapp-image-0.1.9-amd64.tar.gz` | 完整环境 + qaapp 服务，约 501 MB |
| `run.sh` | 最简启动脚本（可选，也可手写 `docker run`） |

镜像内已包含：Python 3.11、全部 Python 依赖、Node 20、ripgrep、Noto CJK 字体、
git/curl/bash、前端 `dist`、skills/plugins。**不含** DB/Redis/配置/历史数据。

镜像 tag：`openharness-qaapp:0.1.9-offline`。

---

## 2. 目标机准备

```bash
uname -m                 # x86_64
docker version           # 20.10+
df -h /                  # 建议 >= 20G
ss -ltnp | grep 8787 || true
```

如果 8787 被旧服务占用，先停旧服务；或先用 `HOST_PORT=18790` 并行验证。

---

## 3. 改项目配置（唯一需要改的地方）

```bash
cd /root/workspace/workspace/OpenHarness
vi config/backend/.env
```

至少确认/修改：

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

模型 Key、外部 Agent 地址也在这个文件里；如果沿用现有配置，通常不用动。
改完不需要重新构建镜像；容器启动时读取。

---

## 4. 加载镜像

```bash
cd /root/qaapp-simple        # 或镜像所在目录
docker load -i qaapp-image-0.1.9-amd64.tar.gz

docker images | grep openharness-qaapp
```

> 本次产物是 gzip 压缩的 docker save 包，`docker load -i` 直接支持；**不要**先 `tar xzf`。
> 如果拿到的是未压缩 `.tar`，同样 `docker load -i`。

---

## 5. 启动

### 方式 A：run.sh（推荐）

```bash
cd /root/qaapp-simple
./run.sh
```

默认：

- 配置：`/root/workspace/workspace/OpenHarness/config/backend`
- 数据：`/root/qaapp-data`
- 端口：`8787`

自定义：

```bash
HOST_PORT=18790 \
CONFIG=/root/workspace/workspace/OpenHarness/config/backend \
DATA=/root/qaapp-data \
./run.sh
```

### 方式 B：一条 docker run

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/workspace/workspace/OpenHarness/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:0.1.9-offline
```

说明：

- `--user 0:0`：项目 `.env` 是 root 0600，用 root 读最简单；如需非 root，
  把 `config/backend` `chown 1000:1000`，去掉该参数。
- `config/backend` 只读挂载；容器不会改你的配置。
- `/data` 持久化会话、users.json、附件、日志；容器重建不丢。

---

## 6. 验证

```bash
curl -fsS http://127.0.0.1:8787/healthz
# {"status":"ok","active_sessions":0}

curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
curl -fsS -o /dev/null -w "index=%{http_code}\n" http://127.0.0.1:8787/

docker logs --tail=100 qaapp
```

登录：

```bash
curl -sS -c /tmp/cj -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Admin@Qa2026"}' \
  http://127.0.0.1:8787/api/v1/auth/login; echo
```

首次启动生成 `data/users.json`，默认 `admin / Admin@Qa2026`；
**登录后立即改密**。

数据源检查：

```bash
docker exec qaapp python -m qaapp datasource check \
  --cwd /app --config-dir /app/config/backend --workspace /data
```

期望 MySQL/Redis 都 OK。模型问答、附件、图表、报告等在前端验证。

---

## 7. 改配置 / 升级 / 备份

```bash
# 改 DB/Redis 后
docker restart qaapp

# 升级：docker load 新镜像后覆盖容器
FORCE=1 ./run.sh

# 日志
docker logs -f qaapp

# 备份（配置 + 数据）
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/workspace/workspace/OpenHarness/config/backend \
  /root/qaapp-data

# 停止/删除（数据保留）
docker rm -f qaapp
```

升级只替换镜像/容器，`config/backend` 和 `/root/qaapp-data` 不动。

---

## 8. 注意事项

1. **单实例**：会话/审批状态在进程内存，不能多副本/`--scale`。
2. **CORS**：同源访问（`http://IP:8787`）无需改；分域名/多入口时把
   `QAAPP_CORS_ORIGINS` 改成显式来源。
3. **HTTPS**：反代后设 `QAAPP_AUTH_TRUST_PROXY=true`，只暴露 443。
4. **默认密码**：首次启动后用 UI 改密；删 `data/users.json` 会重置账户。
5. **历史数据**：新 `/root/qaapp-data` 是全新数据；旧 `~/.ohmo` 历史不在里面。
6. **密钥**：`config/backend/.env` 含模型 Key/DB/Redis 密码，按密钥文件管理。
7. **端口占用**：8787 被旧进程占用时先停旧进程，或 `HOST_PORT=18790`。
8. **镜像大小**：加载后约 1.47 GB；压缩包约 501 MB。

---

## 9. 本次实测

- 目标机：CentOS 7.9、Docker 26.1.4、Compose v2.27.1（本方案不需要 Compose）。
- `docker load` 成功；镜像运行期自检 46/46。
- 用**项目现有** `config/backend/.env` 直接 `docker run`（`--user 0:0`）：
  - `/healthz`、`/`、静态资源 200；
  - admin 登录成功；
  - 模型 SSE 返回 `OK`；
  - MySQL 142 张表、Redis 36351 keys 正常。
- 生产 8787 旧进程未动；测试容器已清理。

---

## 10. 相关文件

- 启动脚本：[`deploy/simple/run.sh`](../../deploy/simple/run.sh)
- 启动说明：[`deploy/simple/README.md`](../../deploy/simple/README.md)
- 镜像构建（无 Docker 构建机）：[`deploy/build_offline.sh`](../../deploy/build_offline.sh)
- 镜像组装：[`deploy/offline/build_image.py`](../../deploy/offline/build_image.py)
- 完整手册：[`OFFLINE_PACKAGING.zh-CN.md`](OFFLINE_PACKAGING.zh-CN.md)
