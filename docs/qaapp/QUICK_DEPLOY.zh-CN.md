# qaapp 最简部署（镜像 tar + 项目配置）

> 目标机只做两件事：**`docker load` 镜像、运行 `run.sh`**。
>
> 构建时已经用 `--env dev` 或 `--env pro` 选好配置，部署包自带
> `config/backend/.env`，目标机**不需要 vim**。
>
> 不执行 `docker build`、不安装 Python/Node、不部署 MySQL/Redis、不需要外网。
>
> 如何构建镜像见
> [`BUILD_AND_DEPLOY.zh-CN.md`](BUILD_AND_DEPLOY.zh-CN.md)。

---

## 1. 产物

```text
/root/qaapp-simple/
├── qaapp-image-<version>-<timestamp>-amd64.tar.gz
├── config/backend/.env
├── run.sh
├── README.md
├── QUICK_DEPLOY.zh-CN.md
├── BUILD_AND_DEPLOY.zh-CN.md
└── SHA256SUMS
```

镜像里已包含代码、Python 依赖、Node、ripgrep、Noto CJK 字体、前端 `dist`。
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

## 3. 配置（打包时已确定）

构建命令 `./deploy/build.sh --env pro` 会把
`config/backend/env_files/env_pro` 复制成 `config/backend/.env`，并放进部署包。
目标机**不需要再改配置**。

如果后续确实要换库/改密码，直接编辑包内
`/root/qaapp-simple/config/backend/.env`，然后执行 `docker restart qaapp`。
这不是首次部署的必需步骤。

---

## 4. 加载镜像

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-<version>-<timestamp>-amd64.tar.gz
docker images | grep openharness-qaapp
```

如果有多个 `openharness-qaapp:<version>-<timestamp>` 镜像，`run.sh` 会自动
选择 `CreatedAt` 最新的那个部署镜像（排除 `*-offline` 基础镜像），不需要手动改 tag。

> 这是 gzip 压缩的 docker save 包，**不要**先 `tar xzf`；`docker load -i` 直接读。

---

## 5. 启动

```bash
cd /root/qaapp-simple
./run.sh
```

默认：

- 配置：`/root/qaapp-simple/config/backend`（打包时已选好）
- 数据：`/root/qaapp-data`
- 端口：`8787`
- 容器：`qaapp`

自定义：

```bash
HOST_PORT=18790 \
CONFIG=/root/qaapp-simple/config/backend \
DATA=/root/qaapp-data \
./run.sh
```

或不用脚本：

```bash
docker run -d --name qaapp --restart unless-stopped \
  --user 0:0 \
  -p 8787:8787 \
  -v /root/qaapp-simple/config/backend:/app/config/backend:ro \
  -v /root/qaapp-data:/data \
  openharness-qaapp:<tag>
```

> `--user 0:0` 用于读取项目里 root 0600 的 `.env`。
> 非 root：`chown -R 1000:1000 config/backend`，去掉该参数。

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
# 改包内 config/backend/.env 后
docker restart qaapp

# 升级：docker load 新镜像后覆盖
FORCE=1 ./run.sh

# 日志
docker logs -f qaapp

# 备份
tar czf /root/qaapp-backup-$(date +%F).tar.gz \
  /root/qaapp-simple/config/backend \
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
8. 基础镜像约 1.47 GB；增量镜像另计。

---

## 9. 本次实测

- 目标机：CentOS 7.9、Docker 26.1.4。
- 基础镜像加载后直接挂项目配置运行：healthz/首页/静态资源 200。
- admin 登录成功；模型 SSE 返回 `OK`。
- MySQL 142 张表、Redis 36351 keys 正常。

---

## 10. 相关文件

- 目标机启动：[`deploy/run.sh`](../../deploy/run.sh)
- 构建入口：[`deploy/build.sh`](../../deploy/build.sh)
- 增量 Dockerfile：[`deploy/Dockerfile`](../../deploy/Dockerfile)
- 构建+部署完整文档：[`BUILD_AND_DEPLOY.zh-CN.md`](BUILD_AND_DEPLOY.zh-CN.md)
