# qaapp 离线部署包

本目录由 `deploy/build.sh` 或 `deploy/build_offline.sh` 生成。镜像已完整打包，
目标机只需要 **Docker Engine + Compose v2**，**不需要外网、不需要 docker build**，
不需要 Python/Node，也不部署 MySQL/Redis（使用你现有的实例）。

## 1. 最短安装

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

不带参数运行会交互式询问。非交互环境请加 `--non-interactive` 并显式传参。
安装脚本会先校验镜像 SHA256/架构，再 `docker load`，然后做镜像运行期自检
（依赖/字体/Node/静态资源），最后写配置、启动并冒烟。

安装完成后访问 `http://<服务器IP>:8787`，用脚本输出的 `admin` 密码登录。
**首次登录后立即改密**；公网访问请前置 HTTPS 反向代理。

## 2. 目录与数据

```text
config/backend/     当前生效配置（.env + 4 个 JSON）；容器只读挂载
config.seed/backend 原始模板；install.sh --force-config 时用来重置
data/               会话快照、轨迹、会话工作区、users.json、日志、HOME
stack.env           compose 变量（镜像 tag、端口、初始密码），0600
backups/            backup.sh 的输出
```

`data/` 是唯一需要长期备份的运行数据；`config/` 含密钥，按密钥文件管理。

## 3. 常用操作

```bash
# 查看状态 / 日志
docker compose --env-file stack.env -f docker-compose.yaml ps
docker compose --env-file stack.env -f docker-compose.yaml logs -f qaapp

# 改完 config/backend/.env 后重启
docker compose --env-file stack.env -f docker-compose.yaml restart qaapp

# 健康检查 / 冒烟
curl -fsS http://127.0.0.1:8787/healthz
./smoke.sh http://127.0.0.1:8787

# 备份 / 清理 / 卸载
./backup.sh --keep 14
./cleanup.sh --older-than-days 30          # dry-run
./cleanup.sh --older-than-days 30 --apply
./uninstall.sh                             # 保留数据
./uninstall.sh --purge --yes               # 连数据一起删
```

## 4. 升级与回滚

```bash
# 新包解压到另一个目录后
./upgrade.sh --bundle /path/to/qaapp-deploy-<new>
```

升级只替换镜像并重建容器，`config/` 与 `data/` 不动。失败时脚本会打印回滚命令：
把 `stack.env` 的 `QAAPP_IMAGE` 改回旧 tag，再 `up -d --wait`。

## 5. 安装参数

| 参数 | 说明 |
| --- | --- |
| `--db-host/--db-port/--db-user/--db-password/--db-name` | 现有 MySQL（只读账号） |
| `--redis-host/--redis-port/--redis-password` | 现有 Redis（db0，只读） |
| `--no-redis` | 关闭 Redis 数据源 |
| `--port` / `--bind` | 宿主端口/监听地址，默认 `8787` / `0.0.0.0` |
| `--public-url` | 浏览器访问地址，写入 `QAAPP_CORS_ORIGINS`；同源可留空 |
| `--trust-proxy` | 前面有 HTTPS 反向代理时开启（`QAAPP_AUTH_TRUST_PROXY`） |
| `--admin-password/--viewer-password` | 首次生成 `users.json` 时的初始密码（会做字符集校验） |
| `--skip-preflight` | 跳过 MySQL/Redis/外部服务连通性检查（通用问答仍可用） |
| `--skip-checksum` | 跳过镜像 tar 的 SHA256 校验 |
| `--force-config` | 用 `config.seed` 覆盖当前 `config/backend`（旧配置会备份） |
| `--non-interactive` | 不做交互式询问，缺参直接失败 |

## 6. 排障

- **healthz 不通**：`docker compose ... logs --tail=200 qaapp`；确认端口未被占用、`stack.env` 的镜像 tag 存在。
- **登录后 403 cross_site**：`--public-url` 与实际访问地址不一致；改 `.env` 的 `QAAPP_CORS_ORIGINS` 后重启。
- **datasource check 失败**：确认目标机到 MySQL/Redis 的网络、账号授权与密码；临时可 `--skip-preflight`。
- **图表中文乱码**：镜像已内置 `fonts-noto-cjk`；若仍乱码，检查是否用了自定义镜像。
- **数据目录权限**：`data/` 与 `config/` 需属于 UID/GID 1000；`chown -R 1000:1000 config data`。

完整说明见仓库 `docs/qaapp/IMAGE_DEPLOY.zh-CN.md`。
