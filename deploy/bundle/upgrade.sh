#!/usr/bin/env bash
# qaapp 升级：只替换镜像，保留 config/ 与 data/。
#
# 用法：
#   ./upgrade.sh --bundle /path/to/qaapp-deploy-<new>
#   ./upgrade.sh --image openharness-qaapp:<new> --tar /path/to/qaapp-image-<new>.tar.gz
#
# 升级前会备份 stack.env；失败时打印回滚命令。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ENV="$SCRIPT_DIR/stack.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"
NEW_BUNDLE=""
NEW_IMAGE=""
NEW_TAR=""
SKIP_CHECKSUM=0

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bundle) NEW_BUNDLE="$2"; shift 2 ;;
        --image) NEW_IMAGE="$2"; shift 2 ;;
        --tar) NEW_TAR="$2"; shift 2 ;;
        --skip-checksum) SKIP_CHECKSUM=1; shift ;;
        --help|-h) usage ;;
        *) die "未知参数：$1" ;;
    esac
done

[[ -f "$STACK_ENV" ]] || die "未找到 $STACK_ENV（先执行 install.sh）"
[[ -f "$COMPOSE_FILE" ]] || die "未找到 $COMPOSE_FILE"
command -v docker >/dev/null 2>&1 || die "未找到 docker"
docker compose version >/dev/null 2>&1 || die "缺少 Docker Compose v2"

if [[ -n "$NEW_BUNDLE" ]]; then
    [[ -f "$NEW_BUNDLE/bundle.meta" ]] || die "不是有效的部署包：$NEW_BUNDLE"
    # shellcheck disable=SC1090
    . "$NEW_BUNDLE/bundle.meta"
    NEW_IMAGE="${IMAGE_REF:?bundle.meta 缺少 IMAGE_REF}"
    NEW_TAR="$NEW_BUNDLE/${IMAGE_TAR:?bundle.meta 缺少 IMAGE_TAR}"
fi

[[ -n "$NEW_IMAGE" ]] || die "需要 --bundle 或 --image"
if [[ -z "$NEW_TAR" ]]; then
    die "需要 --tar（或在 --bundle 中使用包内镜像）"
fi
[[ -f "$NEW_TAR" ]] || die "镜像 tar 不存在：$NEW_TAR"

if [[ "$SKIP_CHECKSUM" -eq 0 ]]; then
    if [[ -n "$NEW_BUNDLE" && -f "$NEW_BUNDLE/SHA256SUMS" ]]; then
        info "校验新部署包..."
        (
            cd "$NEW_BUNDLE"
            if command -v sha256sum >/dev/null 2>&1; then
                sha256sum -c SHA256SUMS
            else
                shasum -a 256 -c SHA256SUMS
            fi
        ) || die "校验失败；确认文件完整或使用 --skip-checksum"
    fi
fi

OLD_IMAGE="$(awk -F= '$1=="QAAPP_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' "$STACK_ENV")"
[[ -n "$OLD_IMAGE" ]] || die "stack.env 缺少 QAAPP_IMAGE"

info "加载新镜像 $NEW_IMAGE ..."
gzip -dc "$NEW_TAR" | docker load
docker image inspect "$NEW_IMAGE" >/dev/null 2>&1 || die "镜像加载后仍不存在：$NEW_IMAGE"

BACKUP="$STACK_ENV.bak-$(date +%Y%m%d-%H%M%S)"
cp -p "$STACK_ENV" "$BACKUP"
info "已备份 stack.env：$BACKUP"

tmp_env="$(mktemp "${TMPDIR:-/tmp}/qaapp-stack.XXXXXX")"
awk -v ref="$NEW_IMAGE" '
    BEGIN { done = 0 }
    /^QAAPP_IMAGE=/ { print "QAAPP_IMAGE=" ref; done = 1; next }
    { print }
    END { if (!done) print "QAAPP_IMAGE=" ref }
' "$STACK_ENV" > "$tmp_env"
chmod 600 "$tmp_env"
mv "$tmp_env" "$STACK_ENV"

compose() {
    docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" "$@"
}

info "重建容器..."
if compose up --help 2>&1 | grep -q -- '--wait'; then
    if command -v timeout >/dev/null 2>&1; then
        timeout 180 docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" up -d --wait || true
    else
        compose up -d --wait || true
    fi
else
    compose up -d
fi

PORT="$(awk -F= '$1=="QAAPP_HOST_PORT" {sub(/^[^=]*=/, ""); print; exit}' "$STACK_ENV")"
PORT="${PORT:-8787}"
BIND="$(awk -F= '$1=="QAAPP_BIND" {sub(/^[^=]*=/, ""); print; exit}' "$STACK_ENV")"
HEALTH_HOST="127.0.0.1"
if [[ -n "$BIND" && "$BIND" != "0.0.0.0" && "$BIND" != "::" ]]; then
    HEALTH_HOST="$BIND"
fi
BASE_URL="http://${HEALTH_HOST}:${PORT}"

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
    echo ""
    echo "升级失败。回滚命令：" >&2
    echo "  sed -i 's|^QAAPP_IMAGE=.*|QAAPP_IMAGE=$OLD_IMAGE|' '$STACK_ENV'" >&2
    echo "  docker compose --env-file '$STACK_ENV' -f '$COMPOSE_FILE' up -d --wait" >&2
    die "新版本未就绪"
fi

"$SCRIPT_DIR/smoke.sh" "$BASE_URL" || {
    compose logs --tail=200 qaapp || true
    echo ""
    echo "冒烟失败。回滚命令：" >&2
    echo "  sed -i 's|^QAAPP_IMAGE=.*|QAAPP_IMAGE=$OLD_IMAGE|' '$STACK_ENV'" >&2
    echo "  docker compose --env-file '$STACK_ENV' -f '$COMPOSE_FILE' up -d --wait" >&2
    die "新版本冒烟失败"
}

if [[ -n "$NEW_BUNDLE" && -d "$NEW_BUNDLE/config.seed/backend" ]]; then
    if [[ "$(cd "$NEW_BUNDLE" && pwd)" != "$SCRIPT_DIR" ]]; then
        rm -rf "$SCRIPT_DIR/config.seed/backend"
        cp -a "$NEW_BUNDLE/config.seed/backend" "$SCRIPT_DIR/config.seed/backend"
        info "已更新 config.seed（用于以后 --force-config，不影响当前配置）"
    fi
fi

if [[ -n "$NEW_BUNDLE" ]]; then
    # 让以后重新执行 install.sh 时仍然使用新版本，而不是退回旧 bundle.meta。
    new_tar_name="$(basename "$NEW_TAR")"
    if [[ "$(cd "$(dirname "$NEW_TAR")" && pwd)" != "$SCRIPT_DIR" ]]; then
        cp -a "$NEW_TAR" "$SCRIPT_DIR/$new_tar_name"
    fi
    if [[ "$(cd "$NEW_BUNDLE" && pwd)" != "$SCRIPT_DIR" ]]; then
        cp "$NEW_BUNDLE/bundle.meta" "$SCRIPT_DIR/bundle.meta"
    fi
    info "已更新本地镜像包与 bundle.meta：$new_tar_name"
fi

info "升级完成：$OLD_IMAGE -> $NEW_IMAGE"
echo "  回滚：把 $STACK_ENV 的 QAAPP_IMAGE 改回 $OLD_IMAGE 后重新 up -d"
