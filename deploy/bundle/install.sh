#!/usr/bin/env bash
# qaapp 部署包安装脚本（在目标机、部署包目录内执行）。
#
# 用法：
#   sudo ./install.sh --db-host 10.0.0.10 --db-port 3306 --db-user readonly \
#        --db-name ai_search_fj --db-password '***' \
#        --redis-host 10.0.0.11 --redis-port 6379 --redis-password '***' \
#        --port 8787 --public-url http://10.0.0.12:8787
#
# 不带参数运行会交互式询问；非交互环境请显式传参并加 --non-interactive。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
META="$SCRIPT_DIR/bundle.meta"
if [[ -f "$META" ]]; then
    # shellcheck disable=SC1090
    . "$META"
fi

IMAGE_REF="${IMAGE_REF:-openharness-qaapp:latest}"
IMAGE_TAR="${IMAGE_TAR:-}"
CONFIG_DIR="$SCRIPT_DIR/config/backend"
CONFIG_SEED="$SCRIPT_DIR/config.seed/backend"
DATA_DIR="$SCRIPT_DIR/data"
STACK_ENV="$SCRIPT_DIR/stack.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"

DB_HOST=""
DB_PORT="3306"
DB_USER=""
DB_PASSWORD=""
DB_NAME=""
REDIS_HOST=""
REDIS_PORT="6379"
REDIS_PASSWORD=""
NO_REDIS=0
PUBLIC_URL=""
TRUST_PROXY=""
BIND="0.0.0.0"
HOST_PORT="8787"
ADMIN_PASSWORD=""
VIEWER_PASSWORD=""
SKIP_PREFLIGHT=0
FORCE_CONFIG=0
NON_INTERACTIVE=0
SKIP_CHECKSUM=0

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

usage() {
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db-host) DB_HOST="$2"; shift 2 ;;
        --db-port) DB_PORT="$2"; shift 2 ;;
        --db-user) DB_USER="$2"; shift 2 ;;
        --db-password) DB_PASSWORD="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --redis-host) REDIS_HOST="$2"; shift 2 ;;
        --redis-port) REDIS_PORT="$2"; shift 2 ;;
        --redis-password) REDIS_PASSWORD="$2"; shift 2 ;;
        --no-redis) NO_REDIS=1; shift ;;
        --public-url) PUBLIC_URL="$2"; shift 2 ;;
        --trust-proxy) TRUST_PROXY="true"; shift ;;
        --no-trust-proxy) TRUST_PROXY="false"; shift ;;
        --bind) BIND="$2"; shift 2 ;;
        --port) HOST_PORT="$2"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
        --viewer-password) VIEWER_PASSWORD="$2"; shift 2 ;;
        --image) IMAGE_REF="$2"; shift 2 ;;
        --image-tar) IMAGE_TAR="$2"; shift 2 ;;
        --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
        --skip-checksum) SKIP_CHECKSUM=1; shift ;;
        --force-config) FORCE_CONFIG=1; shift ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --help|-h) usage ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

[[ "$(id -u)" -eq 0 ]] || die "请用 root（sudo）运行：需要 docker 与 chown"
command -v docker >/dev/null 2>&1 || die "未找到 docker"
command -v curl >/dev/null 2>&1 || die "未找到 curl（用于健康检查）"
docker info >/dev/null 2>&1 || die "Docker daemon 不可用"
docker compose version >/dev/null 2>&1 || die "缺少 Docker Compose v2（docker compose）"
[[ -f "$COMPOSE_FILE" ]] || die "缺少 $COMPOSE_FILE"
[[ -f "$CONFIG_SEED/webapp.json" ]] || die "缺少配置种子：$CONFIG_SEED"

if [[ -z "$IMAGE_TAR" ]]; then
    IMAGE_TAR="$(ls -1 "$SCRIPT_DIR"/qaapp-image-*.tar.gz 2>/dev/null | head -n1 || true)"
fi
[[ -n "$IMAGE_TAR" ]] || die "找不到镜像 tar（bundle.meta 的 IMAGE_TAR 为空）"
if [[ -f "$SCRIPT_DIR/$IMAGE_TAR" ]]; then
    IMAGE_TAR="$SCRIPT_DIR/$IMAGE_TAR"
fi
[[ -f "$IMAGE_TAR" ]] || die "镜像 tar 不存在：$IMAGE_TAR"

if [[ "$SKIP_CHECKSUM" -eq 0 && -f "$SCRIPT_DIR/SHA256SUMS" ]]; then
    image_basename="$(basename "$IMAGE_TAR")"
    if grep -q "  ${image_basename}$" "$SCRIPT_DIR/SHA256SUMS"; then
        info "校验镜像文件..."
        if command -v sha256sum >/dev/null 2>&1; then
            (cd "$SCRIPT_DIR" && grep "  ${image_basename}$" SHA256SUMS | sha256sum -c -) \
                || die "镜像校验失败；确认文件完整或使用 --skip-checksum"
        else
            (cd "$SCRIPT_DIR" && grep "  ${image_basename}$" SHA256SUMS | shasum -a 256 -c -) \
                || die "镜像校验失败；确认文件完整或使用 --skip-checksum"
        fi
    fi
fi

seed_value() {
    # shellcheck disable=SC2312
    awk -F= -v key="$1" '$1==key {sub(/^[^=]*=/, ""); print; exit}' \
        "$CONFIG_SEED/.env" 2>/dev/null || true
}

env_value() {
    awk -F= -v key="$2" '$1==key {sub(/^[^=]*=/, ""); print; exit}' "$1" 2>/dev/null || true
}

[[ -n "$DB_USER" ]] || DB_USER="$(seed_value DB_USER)"
[[ -n "$DB_USER" ]] || DB_USER="readonly_user"
[[ -n "$DB_NAME" ]] || DB_NAME="$(seed_value DB_NAME)"
[[ -n "$DB_NAME" ]] || DB_NAME="ai_search_fj"

generate_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 16
    else
        od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

check_password_charset() {
    local name="$1" value="$2"
    [[ "$value" =~ ^[A-Za-z0-9@._%+!-]{8,128}$ ]] \
        || die "$name 只允许字母/数字/@._%+!-，长度 8–128（避免 .env 解析问题）"
}

prompt_value() {
    local label="$1" default="$2" value=""
    read -r -p "$label [${default}]: " value || true
    printf '%s' "${value:-$default}"
}

prompt_secret() {
    local label="$1" value=""
    read -r -s -p "$label" value || true
    echo
    printf '%s' "$value"
}

if [[ "$NON_INTERACTIVE" -eq 0 ]]; then
    if [[ ! -t 0 ]]; then
        die "当前不是交互终端；请显式传参并加 --non-interactive"
    fi
    echo "== qaapp 安装配置（MySQL/Redis 使用现有实例）=="
    [[ -n "$DB_HOST" ]] || DB_HOST="$(prompt_value "MySQL 主机" "")"
    DB_PORT="$(prompt_value "MySQL 端口" "$DB_PORT")"
    DB_USER="$(prompt_value "MySQL 只读用户" "$DB_USER")"
    if [[ -z "$DB_PASSWORD" ]]; then
        DB_PASSWORD="$(prompt_secret "MySQL 密码（留空表示无密码）: ")"
    fi
    DB_NAME="$(prompt_value "MySQL 数据库名" "$DB_NAME")"
    if [[ "$NO_REDIS" -eq 0 ]]; then
        [[ -n "$REDIS_HOST" ]] || REDIS_HOST="$(prompt_value "Redis 主机（留空则关闭 Redis）" "")"
        if [[ -n "$REDIS_HOST" ]]; then
            REDIS_PORT="$(prompt_value "Redis 端口" "$REDIS_PORT")"
            if [[ -z "$REDIS_PASSWORD" ]]; then
                REDIS_PASSWORD="$(prompt_secret "Redis 密码（留空表示无密码）: ")"
            fi
        else
            NO_REDIS=1
        fi
    fi
    PUBLIC_URL="$(prompt_value "浏览器访问地址（同源可留空，如 http://10.0.0.12:8787）" "$PUBLIC_URL")"
    HOST_PORT="$(prompt_value "宿主端口" "$HOST_PORT")"
    if [[ -z "$ADMIN_PASSWORD" ]]; then
        ADMIN_PASSWORD="$(prompt_secret "admin 初始密码（留空自动生成）: ")"
    fi
fi

[[ -n "$DB_HOST" ]] || die "缺少 --db-host"
[[ -n "$DB_USER" ]] || die "缺少 --db-user"
[[ -n "$DB_NAME" ]] || die "缺少 --db-name"
[[ "$NO_REDIS" -eq 1 || -n "$REDIS_HOST" ]] || die "缺少 --redis-host（或用 --no-redis）"
for port_check in "DB_PORT:$DB_PORT" "REDIS_PORT:$REDIS_PORT" "HOST_PORT:$HOST_PORT"; do
    port_name="${port_check%%:*}"
    port_value="${port_check#*:}"
    [[ "$port_value" =~ ^[0-9]+$ ]] || die "$port_name 非法：$port_value"
    (( 10#$port_value >= 1 && 10#$port_value <= 65535 )) || die "$port_name 超出范围：$port_value"
done

if [[ -z "$DB_PASSWORD" && -n "$(seed_value DB_PASSWORD)" ]]; then
    warn "未提供 --db-password，将沿用配置中的旧密码；如新库不同请重新执行"
fi
if [[ "$NO_REDIS" -eq 0 && -z "$REDIS_PASSWORD" && -n "$(seed_value REDIS_PASSWORD)" ]]; then
    warn "未提供 --redis-password，将沿用配置中的旧密码；如新实例不同请重新执行"
fi

if [[ -f "$STACK_ENV" ]]; then
    [[ -z "$ADMIN_PASSWORD" ]] && ADMIN_PASSWORD="$(env_value "$STACK_ENV" QAAPP_DEFAULT_ADMIN_PASSWORD)"
    [[ -z "$VIEWER_PASSWORD" ]] && VIEWER_PASSWORD="$(env_value "$STACK_ENV" QAAPP_DEFAULT_VIEWER_PASSWORD)"
fi
if [[ -z "$ADMIN_PASSWORD" ]]; then
    ADMIN_PASSWORD="$(generate_password)"
fi
if [[ -z "$VIEWER_PASSWORD" ]]; then
    VIEWER_PASSWORD="$(generate_password)"
fi
check_password_charset "admin 密码" "$ADMIN_PASSWORD"
check_password_charset "viewer 密码" "$VIEWER_PASSWORD"

port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1$"
    else
        return 1
    fi
}

if port_busy "$HOST_PORT"; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'qaapp'; then
        info "端口 $HOST_PORT 由现有 qaapp 容器占用，将原地重建"
    else
        die "宿主端口 $HOST_PORT 已被其它进程占用"
    fi
fi

info "加载镜像 $IMAGE_REF ..."
gzip -dc "$IMAGE_TAR" | docker load
docker image inspect "$IMAGE_REF" >/dev/null 2>&1 || die "镜像加载失败：$IMAGE_REF"

IMAGE_ARCH="$(docker image inspect --format '{{.Architecture}}' "$IMAGE_REF" 2>/dev/null || true)"
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    x86_64|amd64) HOST_ARCH="amd64" ;;
    aarch64|arm64) HOST_ARCH="arm64" ;;
esac
if [[ -n "$IMAGE_ARCH" && "$IMAGE_ARCH" != "$HOST_ARCH" ]]; then
    die "镜像架构 $IMAGE_ARCH 与本机 $HOST_ARCH 不一致；请使用 --platform 对应构建的镜像包"
fi

info "镜像运行期自检（依赖/字体/Node/静态资源）..."
docker run --rm "$IMAGE_REF" python /app/deploy/selfcheck.py --runtime --strict

info "初始化目录..."
mkdir -p "$CONFIG_DIR" "$SCRIPT_DIR/backups" \
    "$DATA_DIR/home/.cache/matplotlib" "$DATA_DIR/data" \
    "$DATA_DIR/logs" "$DATA_DIR/sessions" "$DATA_DIR/conversations"
USERS_EXISTED=0
[[ -f "$DATA_DIR/users.json" ]] && USERS_EXISTED=1
if [[ "$FORCE_CONFIG" -eq 1 && -d "$CONFIG_DIR" ]]; then
    backup_dir="$SCRIPT_DIR/config.bak-$(date +%Y%m%d-%H%M%S)"
    mv "$CONFIG_DIR" "$backup_dir"
    info "旧配置已移到 $backup_dir"
fi
mkdir -p "$CONFIG_DIR"
# 只补缺失文件，绝不覆盖已有 .env / JSON；--force-config 时上面已把旧目录移走。
cp -an "$CONFIG_SEED/." "$CONFIG_DIR/" 2>/dev/null || cp -a "$CONFIG_SEED/." "$CONFIG_DIR/"
chown -R 1000:1000 "$CONFIG_DIR" "$DATA_DIR"
chmod 700 "$DATA_DIR"
chmod 600 "$CONFIG_DIR/.env"

umask 077
{
    echo "QAAPP_IMAGE=$IMAGE_REF"
    echo "QAAPP_BIND=$BIND"
    echo "QAAPP_HOST_PORT=$HOST_PORT"
    echo "QAAPP_CONFIG_DIR_HOST=./config/backend"
    echo "QAAPP_DATA_DIR_HOST=./data"
    echo "QAAPP_DEFAULT_ADMIN_PASSWORD=$ADMIN_PASSWORD"
    echo "QAAPP_DEFAULT_VIEWER_PASSWORD=$VIEWER_PASSWORD"
} > "$STACK_ENV"
chmod 600 "$STACK_ENV"

ANSWERS="$(mktemp "${TMPDIR:-/tmp}/qaapp-answers.XXXXXX")"
chmod 600 "$ANSWERS"
trap 'rm -f "$ANSWERS"' EXIT

json_escape() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

if [[ "$NO_REDIS" -eq 1 ]]; then
    NO_REDIS_JSON=true
else
    NO_REDIS_JSON=false
fi

cat > "$ANSWERS" <<EOF
{
  "db_host": "$(json_escape "$DB_HOST")",
  "db_port": "$(json_escape "$DB_PORT")",
  "db_user": "$(json_escape "$DB_USER")",
  "db_password": "$(json_escape "$DB_PASSWORD")",
  "db_name": "$(json_escape "$DB_NAME")",
  "redis_host": "$(json_escape "$REDIS_HOST")",
  "redis_port": "$(json_escape "$REDIS_PORT")",
  "redis_password": "$(json_escape "$REDIS_PASSWORD")",
  "no_redis": $NO_REDIS_JSON,
  "public_url": "$(json_escape "$PUBLIC_URL")",
  "trust_proxy": ${TRUST_PROXY:-null}
}
EOF

info "写入配置（.env / datasources.json）..."
docker run --rm --user 0:0 \
    -v "$CONFIG_DIR:/app/config/backend" \
    -v "$ANSWERS:/tmp/answers.json:ro" \
    -e QAAPP_CONFIG_DIR=/app/config/backend \
    "$IMAGE_REF" python /app/deploy/configure.py \
    --config-dir /app/config/backend \
    --answers-file /tmp/answers.json
chown -R 1000:1000 "$CONFIG_DIR"
chmod 600 "$CONFIG_DIR/.env"

compose() {
    docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" "$@"
}

info "配置自检..."
compose run --rm --no-deps qaapp python /app/deploy/selfcheck.py --config --strict

if [[ "$SKIP_PREFLIGHT" -eq 0 ]]; then
    info "数据源连通性检查..."
    compose run --rm --no-deps qaapp python -m qaapp datasource check \
        --cwd /app --config-dir /app/config/backend --workspace /data
    info "外部服务连通性检查..."
    compose run --rm --no-deps qaapp python /app/deploy/selfcheck.py --network \
        --config-dir /app/config/backend --workspace /data
else
    warn "已跳过 preflight（--skip-preflight）"
fi

info "启动服务..."
if compose up --help 2>&1 | grep -q -- '--wait'; then
    if command -v timeout >/dev/null 2>&1; then
        timeout 180 docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" up -d --force-recreate --wait || true
    else
        compose up -d --force-recreate --wait || true
    fi
else
    compose up -d --force-recreate
fi

HEALTH_HOST="127.0.0.1"
if [[ "$BIND" != "0.0.0.0" && "$BIND" != "::" && "$BIND" != "" ]]; then
    HEALTH_HOST="$BIND"
fi
BASE_URL="http://${HEALTH_HOST}:${HOST_PORT}"

healthy=0
for _ in $(seq 1 60); do
    if curl -fsS "$BASE_URL/healthz" >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 2
done

if [[ "$healthy" -ne 1 ]]; then
    compose ps || true
    compose logs --tail=200 qaapp || true
    die "服务未在 120s 内就绪：$BASE_URL/healthz"
fi

info "冒烟测试..."
"$SCRIPT_DIR/smoke.sh" "$BASE_URL" || {
    compose logs --tail=200 qaapp || true
    die "冒烟测试失败"
}

echo ""
info "安装完成"
echo "  访问地址：$BASE_URL"
if [[ "$USERS_EXISTED" -eq 1 ]]; then
    echo "  管理员：admin（users.json 已存在，初始密码不覆盖现有账户）"
    echo "  只读账号：viewer（同上）"
else
    echo "  管理员：admin / $ADMIN_PASSWORD"
    echo "  只读账号：viewer / $VIEWER_PASSWORD"
fi
echo "  配置目录：$CONFIG_DIR（改完执行：docker compose --env-file $STACK_ENV -f $COMPOSE_FILE restart qaapp）"
echo "  数据目录：$DATA_DIR"
echo "  日志：docker compose --env-file $STACK_ENV -f $COMPOSE_FILE logs -f qaapp"
echo "  备份：$SCRIPT_DIR/backup.sh"
echo ""
echo "  首次登录后请立即修改默认密码；公网部署请前置 HTTPS 反向代理。"
