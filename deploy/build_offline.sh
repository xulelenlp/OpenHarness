#!/usr/bin/env bash
# 在“没有 Docker、但能访问外网”的构建机上，生成可直接 docker load 的 qaapp 镜像
# 以及完整部署包。目标机（内网、无外网）只需要 Docker，不需要 build。
#
# 用法：
#   ./deploy/build_offline.sh [--tag openharness-qaapp:0.1.9-offline]
#                             [--out-dir dist] [--work-dir /tmp/openharness-offline]
#                             [--refresh] [--clean-work]
#
# 产物：
#   dist/qaapp-image-<tag>-amd64.tar.gz   单层镜像（约 500 MB）
#   dist/qaapp-deploy-<tag>-amd64.tar.gz  部署包（镜像 + compose + 配置 + 脚本）
#   dist/SHA256SUMS
#
# 构建机要求：Python 3.9+（带 pip）、外网（PyPI / Docker 镜像代理 / Node / Debian 镜像）。
# 不需要 Docker、不需要 Linux。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
OUT_DIR=""
WORK_DIR="${WORK_DIR:-/tmp/openharness-offline}"
TAG="${TAG:-}"
REFRESH=0
CLEAN_WORK=0
REUSE_IMAGE=0
PYTHON_BIN="${PYTHON_BIN:-python3}"
REGISTRY=""
AUTH_URL=""
PIP_INDEX_URL=""
NODE_URL=""
FONT_URL=""
BASE_IMAGE=""

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

usage() {
    sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --out-dir) OUT_DIR="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --refresh) REFRESH=1; shift ;;
        --clean-work) CLEAN_WORK=1; shift ;;
        --reuse-image) REUSE_IMAGE=1; shift ;;
        --registry) REGISTRY="$2"; shift 2 ;;
        --auth-url) AUTH_URL="$2"; shift 2 ;;
        --pip-index-url) PIP_INDEX_URL="$2"; shift 2 ;;
        --node-url) NODE_URL="$2"; shift 2 ;;
        --font-url) FONT_URL="$2"; shift 2 ;;
        --base-image) BASE_IMAGE="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

[[ -n "$OUT_DIR" ]] && DIST_DIR="$OUT_DIR"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "未找到 $PYTHON_BIN"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "需要 Python 3.9+"

VERSION="$("$PYTHON_BIN" - "$ROOT" <<'PY'
import re
import sys
from pathlib import Path
text = (Path(sys.argv[1]) / "pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(m.group(1) if m else "0.0.0")
PY
)"
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ -z "$TAG" ]]; then
    TAG="openharness-qaapp:${VERSION}-offline"
fi
SLUG="$(printf '%s' "$TAG" | tr '/:' '__')"
ARCH="amd64"
IMAGE_NAME="qaapp-image-${SLUG}-${ARCH}.tar.gz"
BUNDLE_NAME="qaapp-deploy-${SLUG}-${ARCH}.tar.gz"
IMAGE_PATH="$DIST_DIR/$IMAGE_NAME"
BUNDLE_PATH="$DIST_DIR/$BUNDLE_NAME"
STAGE="$DIST_DIR/qaapp-deploy-${SLUG}-${ARCH}"

mkdir -p "$DIST_DIR"

info "版本 $VERSION / commit $COMMIT / tag $TAG"
info "构建机 Python：$($PYTHON_BIN -V 2>&1)"
info "工作目录：$WORK_DIR"

if [[ "$REUSE_IMAGE" -eq 1 && -s "$IMAGE_PATH" && "$REFRESH" -eq 0 ]]; then
    info "复用已有镜像：$IMAGE_PATH"
else
    BUILD_ARGS=(--tag "$TAG" --output "$IMAGE_PATH" --work-dir "$WORK_DIR")
    [[ "$REFRESH" -eq 1 ]] && BUILD_ARGS+=(--refresh)
    [[ "$CLEAN_WORK" -eq 1 ]] && BUILD_ARGS+=(--clean)
    [[ -n "$REGISTRY" ]] && BUILD_ARGS+=(--registry "$REGISTRY")
    [[ -n "$AUTH_URL" ]] && BUILD_ARGS+=(--auth-url "$AUTH_URL")
    [[ -n "$PIP_INDEX_URL" ]] && BUILD_ARGS+=(--pip-index-url "$PIP_INDEX_URL")
    [[ -n "$NODE_URL" ]] && BUILD_ARGS+=(--node-url "$NODE_URL")
    [[ -n "$FONT_URL" ]] && BUILD_ARGS+=(--font-url "$FONT_URL")
    [[ -n "$BASE_IMAGE" ]] && BUILD_ARGS+=(--base-image "$BASE_IMAGE")
    "$PYTHON_BIN" "$ROOT/deploy/offline/build_image.py" "${BUILD_ARGS[@]}"
fi
[[ -s "$IMAGE_PATH" ]] || die "镜像未生成：$IMAGE_PATH"

# ---------------------------------------------------------------------------
# 组装部署包（与 deploy/build.sh 的 bundle 结构保持一致）
# ---------------------------------------------------------------------------
info "组装部署包..."
rm -rf "$STAGE"
mkdir -p "$STAGE/config.seed/backend" "$STAGE/config/backend"
cp -a "$ROOT/deploy/bundle/." "$STAGE/"

ENV_SOURCE="$ROOT/config/backend/.env"
if [[ ! -f "$ENV_SOURCE" ]]; then
    warn "未找到 config/backend/.env，使用 .env_prod 作为种子；部署时必须填写真实密钥与数据库连接"
    ENV_SOURCE="$ROOT/config/backend/.env_prod"
fi
[[ -f "$ENV_SOURCE" ]] || die "缺少 config/backend/.env 与 .env_prod"

copy_config() {
    local dest="$1"
    mkdir -p "$dest"
    cp -a "$ROOT/config/backend/." "$dest/"
    rm -f "$dest/.env_dev" "$dest/.DS_Store"
    rm -f "$dest"/.env.bak-* 2>/dev/null || true
    if [[ ! -f "$dest/.env" ]]; then
        cp "$ENV_SOURCE" "$dest/.env"
    fi
    chmod 600 "$dest/.env"
}

copy_config "$STAGE/config.seed/backend"
copy_config "$STAGE/config/backend"

{
    echo "IMAGE_REF=$TAG"
    echo "IMAGE_TAR=$IMAGE_NAME"
    echo "VERSION=$VERSION"
    echo "COMMIT=$COMMIT"
    echo "BUILD_DATE=$BUILD_DATE"
    echo "PLATFORM=linux/$ARCH"
    echo "MODE=offline-single-layer"
} > "$STAGE/bundle.meta"

{
    echo "version=$VERSION"
    echo "commit=$COMMIT"
    echo "build_date=$BUILD_DATE"
    echo "platform=linux/$ARCH"
    echo "image=$TAG"
    echo "mode=offline-single-layer"
    echo "base=python:3.11-bookworm"
} > "$STAGE/BUILD_INFO"
if [[ -d "$WORK_DIR/wheels" ]]; then
    ls -1 "$WORK_DIR/wheels"/*.whl 2>/dev/null | sed 's|.*/||' > "$STAGE/BUILD_INFO.wheels.txt" || true
fi

ln "$IMAGE_PATH" "$STAGE/$IMAGE_NAME" 2>/dev/null || cp "$IMAGE_PATH" "$STAGE/$IMAGE_NAME"

chmod +x "$STAGE"/*.sh
(
    cd "$STAGE"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$IMAGE_NAME" config.seed/backend/.env config/backend/.env > SHA256SUMS
    else
        shasum -a 256 "$IMAGE_NAME" config.seed/backend/.env config/backend/.env > SHA256SUMS
    fi
)

info "打包 $BUNDLE_PATH ..."
if [[ "$(uname -s)" == "Darwin" ]] && command -v xattr >/dev/null 2>&1; then
    xattr -cr "$STAGE" 2>/dev/null || true
fi
COPYFILE_DISABLE=1 tar -C "$DIST_DIR" -czf "$BUNDLE_PATH" "$(basename "$STAGE")"
[[ -s "$BUNDLE_PATH" ]] || die "部署包生成失败：$BUNDLE_PATH"
(
    cd "$DIST_DIR"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$IMAGE_NAME" "$BUNDLE_NAME" > SHA256SUMS
    else
        shasum -a 256 "$IMAGE_NAME" "$BUNDLE_NAME" > SHA256SUMS
    fi
)
rm -rf "$STAGE"

echo ""
info "构建完成"
echo "  镜像：$IMAGE_PATH ($(du -h "$IMAGE_PATH" | awk '{print $1}'))"
echo "  部署包：$BUNDLE_PATH ($(du -h "$BUNDLE_PATH" | awk '{print $1}'))"
echo "  校验：cd $DIST_DIR && sha256sum -c SHA256SUMS"
echo ""
echo "目标机（内网、无外网）："
echo "  tar xzf $(basename "$BUNDLE_PATH") && cd $(basename "$BUNDLE_PATH" .tar.gz)"
echo "  sudo ./install.sh --db-host <host> --db-port <port> --db-user <user> --db-name <db> --redis-host <host>"
