#!/usr/bin/env bash
# 构建 qaapp 运行镜像，并组装可在离线服务器安装的部署包。
#
# 用法：
#   ./deploy/build.sh [--tag 0.1.9-abc1234] [--platform linux/amd64]
#                     [--pip-index-url URL] [--npm-registry URL]
#                     [--python-image IMAGE] [--node-image IMAGE]
#                     [--no-constraints] [--push] [--no-save] [--keep-stage]
#
# 产物（默认在 dist/）：
#   qaapp-image-<tag>-<arch>.tar.gz     docker save 的镜像
#   qaapp-deploy-<tag>-<arch>.tar.gz    部署包（镜像 + compose + config + 安装脚本）
#   SHA256SUMS
#
# 构建机需要：Docker（建议 24+，含 buildx）、外网或内网 PyPI/npm/镜像仓库。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
PLATFORM="${PLATFORM:-linux/amd64}"
TAG="${TAG:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.11-slim-bookworm}"
NODE_IMAGE="${NODE_IMAGE:-node:20-bookworm-slim}"
USE_CONSTRAINTS=1
PUSH=0
SAVE=1
KEEP_STAGE=0

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

usage() {
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        --pip-index-url) PIP_INDEX_URL="$2"; shift 2 ;;
        --npm-registry) NPM_CONFIG_REGISTRY="$2"; shift 2 ;;
        --python-image) PYTHON_IMAGE="$2"; shift 2 ;;
        --node-image) NODE_IMAGE="$2"; shift 2 ;;
        --no-constraints) USE_CONSTRAINTS=0; shift ;;
        --push) PUSH=1; shift ;;
        --no-save) SAVE=0; shift ;;
        --keep-stage) KEEP_STAGE=1; shift ;;
        --help|-h) usage ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "未找到 docker"
docker info >/dev/null 2>&1 || die "Docker daemon 不可用（检查 docker 权限/服务）"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -n1)"
[[ -n "$VERSION" ]] || VERSION="0.0.0"
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null | head -n1)" ]]; then
    DIRTY="-dirty"
else
    DIRTY=""
fi
if [[ -z "$TAG" ]]; then
    TAG="openharness-qaapp:${VERSION}-${COMMIT}${DIRTY}"
fi
ARCH="${PLATFORM##*/}"
TAG_SLUG="$(printf '%s' "$TAG" | tr '/:' '__')"
IMAGE_NAME="qaapp-image-${TAG_SLUG}-${ARCH}.tar.gz"
BUNDLE_NAME="qaapp-deploy-${TAG_SLUG}-${ARCH}.tar.gz"
IMAGE_PATH="$DIST_DIR/$IMAGE_NAME"
BUNDLE_PATH="$DIST_DIR/$BUNDLE_NAME"
STAGE="$DIST_DIR/qaapp-deploy-${TAG_SLUG}-${ARCH}"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$@"
    else
        shasum -a 256 "$@"
    fi
}

info "版本：$VERSION  commit：$COMMIT  tag：$TAG"
info "平台：$PLATFORM"

# ---------------------------------------------------------------------------
# 1. 构建镜像
# ---------------------------------------------------------------------------
BUILD_ARGS=(
    --build-arg "APP_VERSION=$VERSION"
    --build-arg "GIT_COMMIT=$COMMIT"
    --build-arg "BUILD_DATE=$BUILD_DATE"
    --build-arg "CONSTRAINTS=$USE_CONSTRAINTS"
    --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE"
    --build-arg "NODE_IMAGE=$NODE_IMAGE"
)
[[ -n "$PIP_INDEX_URL" ]] && BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
[[ -n "$NPM_CONFIG_REGISTRY" ]] && BUILD_ARGS+=(--build-arg "NPM_CONFIG_REGISTRY=$NPM_CONFIG_REGISTRY")

DOCKER_BUILD=(docker build)
if docker buildx version >/dev/null 2>&1; then
    DOCKER_BUILD=(docker buildx build --load)
else
    warn "未检测到 buildx；跨架构构建可能失败（建议安装 docker-buildx）"
fi

info "开始构建镜像（首次构建需要下载基础镜像与依赖，耗时较长）..."
"${DOCKER_BUILD[@]}" \
    --platform "$PLATFORM" \
    -f "$ROOT/deploy/docker/Dockerfile" \
    -t "$TAG" \
    "${BUILD_ARGS[@]}" \
    "$ROOT"

IMAGE_PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$TAG" 2>/dev/null || true)"
info "镜像平台：${IMAGE_PLATFORM:-unknown}"
if [[ -n "$IMAGE_PLATFORM" && "$IMAGE_PLATFORM" != "$PLATFORM" ]]; then
    warn "镜像平台 $IMAGE_PLATFORM 与请求的 $PLATFORM 不一致，拷贝到目标机前请确认"
fi

info "镜像内自检..."
docker run --rm "$TAG" python /app/deploy/selfcheck.py --runtime --strict

# ---------------------------------------------------------------------------
# 2. 保存镜像
# ---------------------------------------------------------------------------
mkdir -p "$DIST_DIR"
if [[ "$SAVE" -eq 1 ]]; then
    info "导出镜像到 $IMAGE_PATH ..."
    docker save "$TAG" | gzip -9 > "$IMAGE_PATH"
    [[ -s "$IMAGE_PATH" ]] || die "镜像导出失败：$IMAGE_PATH"
fi

if [[ "$PUSH" -eq 1 ]]; then
    info "推送镜像 $TAG ..."
    docker push "$TAG"
fi

if [[ "$SAVE" -eq 0 ]]; then
    info "--no-save：跳过部署包。镜像 tag：$TAG"
    exit 0
fi

# ---------------------------------------------------------------------------
# 3. 组装部署包
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
[[ -f "$ENV_SOURCE" ]] || die "缺少 config/backend/.env 与 .env_prod，无法生成部署包"

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

if ! grep -q '^DB_HOST=.' "$STAGE/config/backend/.env"; then
    warn "config/backend/.env 的 DB_HOST 为空；安装脚本会要求填写"
fi

{
    echo "IMAGE_REF=$TAG"
    echo "IMAGE_TAR=$IMAGE_NAME"
    echo "VERSION=$VERSION"
    echo "COMMIT=$COMMIT"
    echo "BUILD_DATE=$BUILD_DATE"
    echo "PLATFORM=$PLATFORM"
} > "$STAGE/bundle.meta"

{
    echo "version=$VERSION"
    echo "commit=$COMMIT"
    echo "build_date=$BUILD_DATE"
    echo "platform=$PLATFORM"
    echo "image=$TAG"
    echo "python=$(docker run --rm "$TAG" python -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo unknown)"
} > "$STAGE/BUILD_INFO"
if ! docker run --rm "$TAG" python -m pip freeze > "$STAGE/BUILD_INFO.pip-freeze.txt" 2>/dev/null; then
    warn "无法记录镜像内 pip freeze"
    rm -f "$STAGE/BUILD_INFO.pip-freeze.txt"
fi

ln "$IMAGE_PATH" "$STAGE/$IMAGE_NAME" 2>/dev/null || cp "$IMAGE_PATH" "$STAGE/$IMAGE_NAME"

chmod +x "$STAGE"/*.sh
(
    cd "$STAGE"
    sha256 "$IMAGE_NAME" config.seed/backend/.env config/backend/.env > SHA256SUMS
)

info "打包 $BUNDLE_PATH ..."
if [[ "$(uname -s)" == "Darwin" ]] && command -v xattr >/dev/null 2>&1; then
    xattr -cr "$STAGE" 2>/dev/null || true
fi
COPYFILE_DISABLE=1 tar -C "$DIST_DIR" -czf "$BUNDLE_PATH" "$(basename "$STAGE")"
[[ -s "$BUNDLE_PATH" ]] || die "部署包生成失败：$BUNDLE_PATH"

(
    cd "$DIST_DIR"
    sha256 "$IMAGE_NAME" "$BUNDLE_NAME" > SHA256SUMS
)

if [[ "$KEEP_STAGE" -eq 0 ]]; then
    rm -rf "$STAGE"
fi

echo ""
info "构建完成"
echo "  镜像：$IMAGE_PATH ($(du -h "$IMAGE_PATH" | awk '{print $1}'))"
echo "  部署包：$BUNDLE_PATH ($(du -h "$BUNDLE_PATH" | awk '{print $1}'))"
echo "  校验：cd $DIST_DIR && sha256sum -c SHA256SUMS"
echo ""
echo "目标机安装："
echo "  tar xzf $(basename "$BUNDLE_PATH") && cd $(basename "$BUNDLE_PATH" .tar.gz)"
echo "  sudo ./install.sh --db-host <host> --db-port <port> --db-user <user> --db-name <db> --redis-host <host>"
