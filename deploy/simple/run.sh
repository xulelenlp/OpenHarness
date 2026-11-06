#!/usr/bin/env bash
# qaapp 最简部署：加载镜像后，直接挂载项目配置 + 数据目录运行。
#
# 前置：
#   1) docker load -i qaapp-image-0.1.9-amd64.tar.gz
#   2) 在项目 config/backend/.env 中改好 DB_* / REDIS_*（以及模型 Key / CORS）
#
# 用法：
#   ./run.sh
#   HOST_PORT=18790 ./run.sh
#   FORCE=1 ./run.sh          # 覆盖已存在的 qaapp 容器（升级/重启）
set -euo pipefail

IMAGE="${IMAGE:-openharness-qaapp:0.1.9-offline}"
NAME="${NAME:-qaapp}"
CONFIG="${CONFIG:-/root/workspace/workspace/OpenHarness/config/backend}"
DATA="${DATA:-/root/qaapp-data}"
HOST_PORT="${HOST_PORT:-8787}"
FORCE="${FORCE:-0}"

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

command -v docker >/dev/null 2>&1 || die "未找到 docker"
command -v curl >/dev/null 2>&1 || die "未找到 curl（用于健康检查）"
[[ -f "$CONFIG/.env" ]] || die "找不到配置文件：$CONFIG/.env"
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "镜像不存在：$IMAGE（先 docker load -i ...）"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    if [[ "$FORCE" == "1" ]]; then
        info "删除已存在的容器 $NAME ..."
        docker rm -f "$NAME" >/dev/null
    else
        die "容器 $NAME 已存在；升级/重建请用 FORCE=1 ./run.sh"
    fi
fi

# 项目配置 .env 通常是 root:root 0600，这里用 root 运行容器以便直接读取；
# 如需非 root，可把 config/backend chown 到 1000:1000 后去掉 --user 0:0。
mkdir -p "$DATA"
info "启动 $NAME（$IMAGE）..."
docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --user 0:0 \
    -p "${HOST_PORT}:8787" \
    -v "${CONFIG}:/app/config/backend:ro" \
    -v "${DATA}:/data" \
    "$IMAGE" >/dev/null

info "等待服务就绪..."
for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
        echo ""
        echo "qaapp 已启动"
        echo "  访问：http://<服务器IP>:${HOST_PORT}"
        echo "  配置：$CONFIG/.env（改 DB/Redis 后执行 docker restart $NAME）"
        echo "  数据：$DATA"
        echo "  日志：docker logs -f $NAME"
        echo "  首次登录：admin（默认 Admin@Qa2026，或首次启动前设置 QAAPP_DEFAULT_ADMIN_PASSWORD）"
        exit 0
    fi
    sleep 2
done

docker logs --tail=100 "$NAME" || true
die "120s 内未就绪，请检查上面日志"
