# qaapp 最简部署（镜像 tar + 项目配置）

> 目标机只做三件事：**`docker load` 镜像、改项目 `config/backend/.env` 的
> DB/Redis、运行 `run.sh`**。
>
> 不执行 `docker build`、不安装 Python/Node、不部署 MySQL/Redis、不需要外网。
>
> 如何从源码构建镜像见
> [`BUILD_AND_DEPLOY.zh-CN.md`](BUILD_AND_DEPLOY.zh-CN.md)。

---

## 1. 产物

```text
/root/qaapp-simple/
├── qaapp-image-0.1.9-amd64.tar.gz    # 镜像，约 501 MB
├── run.sh                            # 启动脚本
├── README.md
├── QUICK_DEPLOY.zh-CN.md
├── BUILD_AND_DEPLOY.zh-CN.md
└── SHA256SUMS
```

镜像 tag：`openharness-qaapp:0.1.9-offline`。

镜像内已包含代码、Python 依赖、Node、ripgrep、Noto CJK 字体、前端 `dist`。
**不含** DB/Redis/`.env`/历史会话。

---

## 2. 目标机准备

```bash
uname -m                 # x86_64
docker version           # 20.10+
df -h /                  # 建议 >= 20G
ss -ltnp | grep 8787 || true
```

8787 被旧进程占用时，先停旧进程；或先用 `HOST_PORT=18790` 并行验证。

---

## 3. 改项目配置（只改这里）

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

模型 Key、外部 Agent 地址也在这个文件；沿用现有配置即可。
改完不需要重建镜像。

---

## 4. 加载镜像

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-0.1.9-amd64.tar.gz
docker images | grep openharness-qaapp
```

> 这是 gzip 压缩的 docker save 包，**不要**先 `tar xzf`；`docker load -i` 直接读。

---

## 5. 启动

```bash
cd /root/qaapp-simple
./run.sh
```

默认：

- 配置：`/root/workspace/workspace/OpenHarness/config/backend`
- 数据：`/root/qaapp-data`
- 端口：`8787`
- 容器：`qaapp`

自定义：

```bash
HOST_PORT=18790 \
CONFIG=/root/workspace/workspace/OpenHarness/config/backend \
DATA=/root/qaapp-data \
./run.sh
```

或不用脚本：

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/workspace/workspace/OpenHarness/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:0.1.9-offline
```

说明：

- `--user 0:0`：项目 `.env` 是 root 0600，用 root 读最简单。
  非 root：`chown -R 1000:1000 config/backend`，去掉该参数。
- `config/backend` 只读挂载，容器不改配置。
- `/data` 持久化会话、users.json、附件、日志。

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

---

## 7. 改配置 / 升级 / 备份

```bash
# 改 DB/Redis 后
docker restart qaapp

# 升级：docker load 新镜像后覆盖
FORCE=1 ./run.sh

# 日志
docker logs -f qaapp

# 备份
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/workspace/workspace/OpenHarness/config/backend \
  /root/qaapp-data

# 停止/删除（数据保留）
docker rm -f qaapp
```

---

## 8. 注意

1. 单实例，不能 `--scale`。
2. 同源访问（`http://IP:8787`）无需改 CORS；分域名/多入口时改
   `QAAPP_CORS_ORIGINS`。
3. HTTPS 反代后设 `QAAPP_AUTH_TRUST_PROXY=true`。
4. 首次登录立即改密；删 `data/users.json` 会重置账户。
5. 新 `data` 是全新数据；旧 `~/.ohmo` 历史不在里面。
6. `.env` 含密钥，按密钥文件管理。
7. 8787 被旧进程占用时先停旧进程，或 `HOST_PORT=18790`。
8. 镜像加载后约 1.47 GB。

---

## 9. 本次实测

- 目标机：CentOS 7.9、Docker 26.1.4。
- `docker load` 成功。
- 用项目现有 `config/backend/.env` 直接运行：healthz/首页/静态资源 200。
- admin 登录成功；模型 SSE 返回 `OK`。
- MySQL 142 张表、Redis 36351 keys 正常。
- 生产 8787 旧进程未动；测试容器已清理。

---

## 10. 相关文件

- 目标机启动：[`deploy/run.sh`](../../deploy/run.sh)
- 构建入口：[`deploy/build.sh`](../../deploy/build.sh)
- 镜像组装：[`deploy/build_image.py`](../../deploy/build_image.py)
- 构建+部署完整文档：[`BUILD_AND_DEPLOY.zh-CN.md`](BUILD_AND_DEPLOY.zh-CN.md)
