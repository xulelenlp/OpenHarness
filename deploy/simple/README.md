# qaapp 最简部署（镜像 tar + run.sh）

只需要三样东西：

- `qaapp-image-0.1.9-amd64.tar.gz`（镜像，约 501 MB）
- `run.sh`（本文件同目录）
- 项目配置目录 `config/backend/`（里面有 `webapp.json`、`agents.json` 等）

## 1. 改配置

编辑**项目里**的 `config/backend/.env`，只改需要改的：

```bash
DB_HOST=...
DB_PORT=...
DB_USER=...
DB_PASSWORD=...
DB_NAME=...

REDIS_HOST=...
REDIS_PORT=...
REDIS_PASSWORD=...
```

数据库/Redis 不部署，用现有的。模型 Key、外部 Agent 地址也在这个 `.env` 里。

## 2. 加载镜像

```bash
docker load -i qaapp-image-0.1.9-amd64.tar.gz
docker images | grep openharness-qaapp
```

## 3. 启动

```bash
# 默认：配置 /root/workspace/workspace/OpenHarness/config/backend，数据 /root/qaapp-data，端口 8787
./run.sh

# 自定义
HOST_PORT=18790 \
CONFIG=/root/workspace/workspace/OpenHarness/config/backend \
DATA=/root/qaapp-data \
./run.sh
```

首次启动会生成 `data/users.json`，默认账户 `admin / Admin@Qa2026`；请尽快改密。
配置 `.env` 是 root 0600，`run.sh` 用 `--user 0:0` 读取；如需非 root，
把配置目录 `chown 1000:1000` 后去掉 `run.sh` 里的 `--user 0:0`。

## 4. 验证

```bash
curl -fsS http://127.0.0.1:8787/healthz
curl -fsS http://127.0.0.1:8787/api/v1/bootstrap
docker logs --tail=100 qaapp
```

## 5. 改配置 / 升级 / 备份

```bash
# 改 DB/Redis 后
docker restart qaapp

# 升级：先 docker load 新镜像，再覆盖
FORCE=1 ./run.sh

# 备份
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/workspace/workspace/OpenHarness/config/backend /root/qaapp-data

# 停止/删除
docker rm -f qaapp
```

## 6. 注意

- 如果 8787 被旧进程占用，先停旧进程，或用 `HOST_PORT=18790` 先验证。
- 外网/多入口访问时，把 `QAAPP_CORS_ORIGINS` 改成实际来源；HTTPS 反代时设
  `QAAPP_AUTH_TRUST_PROXY=true`。
- 单实例；不能 `--scale`。
- 更完整的配置、依赖和排障见
  [`docs/qaapp/QUICK_DEPLOY.zh-CN.md`](../../docs/qaapp/QUICK_DEPLOY.zh-CN.md)。
