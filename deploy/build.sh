#!/usr/bin/env bash
# 在打包服务器上，用本地已导入的基础镜像做增量构建。
#
# 前置（只需一次）：
#   docker load -i qaapp-image-0.1.9-amd64.tar.gz
#   # 默认基础镜像 tag：openharness-qaapp:0.1.9-offline
#
# 用法：
#   ./deploy/build.sh
#   ./deploy/build.sh --base openharness-qaapp:0.1.9-offline
#   ./deploy/build.sh --tag openharness-qaapp:0.1.9-20261106
#   ./deploy/build.sh --output dist/qaapp-image-0.1.9-amd64.tar.gz
#
# 产物：
#   dist/qaapp-image-<version>-<timestamp>-amd64.tar.gz
#   dist/qaapp-simple/
#   ├── qaapp-image-...tar.gz
#   ├── run.sh
#   ├── README.md
#   ├── QUICK_DEPLOY.zh-CN.md
#   ├── BUILD_AND_DEPLOY.zh-CN.md
#   └── SHA256SUMS
#
# 不下载基础镜像/依赖，不需要 pip；只要求 Docker 和本地基础镜像。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-openharness-qaapp:0.1.9-offline}"
TAG=""
OUTPUT=""

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

usage() {
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base) BASE="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

command -v docker >/dev/null 2>&1 || die "未找到 docker"
docker info >/dev/null 2>&1 || die "Docker daemon 不可用"
docker image inspect "$BASE" >/dev/null 2>&1 || die \
    "基础镜像不在本地：$BASE
先在打包服务器执行：docker load -i qaapp-image-0.1.9-amd64.tar.gz
如果导入后的 tag 不同，用 --base <tag> 指定。"

[[ -f "$ROOT/frontend/webapp/dist/index.html" ]] || die \
    "缺少 frontend/webapp/dist/index.html；请先在 frontend/webapp 执行 npm ci && npm run build"

VERSION="$(python3 - "$ROOT" <<'PY'
import re
import sys
from pathlib import Path
text = (Path(sys.argv[1]) / "pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
print(m.group(1) if m else "0.0.0")
PY
)"
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
STAMP="$(date +%Y%m%d-%H%M%S)"
[[ -n "$TAG" ]] || TAG="openharness-qaapp:${VERSION}-${STAMP}"
[[ -n "$OUTPUT" ]] || OUTPUT="$ROOT/dist/qaapp-image-${VERSION}-${STAMP}-amd64.tar.gz"
[[ "$TAG" != "$BASE" ]] || die "输出 tag 不能等于基础镜像 tag：$BASE"

STAGE="$ROOT/dist/qaapp-simple"
info "基础镜像：$BASE"
info "输出镜像：$TAG"
info "版本 $VERSION / commit $COMMIT"

info "增量构建（--network none，不下载任何依赖）..."
docker build \
    --network none \
    -f "$ROOT/deploy/Dockerfile" \
    --build-arg "BASE_IMAGE=$BASE" \
    --build-arg "VERSION=$VERSION" \
    --build-arg "COMMIT=$COMMIT" \
    -t "$TAG" \
    "$ROOT"

info "导入检查..."
docker run --rm "$TAG" python -c "import qaapp, openharness, ohmo; print('imports ok')"

info "导出镜像：$OUTPUT"
mkdir -p "$(dirname "$OUTPUT")"
docker save "$TAG" | gzip -9 > "$OUTPUT"
[[ -s "$OUTPUT" ]] || die "镜像导出失败：$OUTPUT"

info "整理部署目录：$STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ln "$OUTPUT" "$STAGE/$(basename "$OUTPUT")" 2>/dev/null || cp "$OUTPUT" "$STAGE/$(basename "$OUTPUT")"
# run.sh 默认镜像 tag 与本次构建一致
sed "s|^IMAGE=.*|IMAGE=\"\${IMAGE:-$TAG}\"|" "$ROOT/deploy/run.sh" > "$STAGE/run.sh"
cp "$ROOT/deploy/README.md" "$STAGE/README.md"
cp "$ROOT/docs/qaapp/QUICK_DEPLOY.zh-CN.md" "$STAGE/QUICK_DEPLOY.zh-CN.md"
cp "$ROOT/docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md" "$STAGE/BUILD_AND_DEPLOY.zh-CN.md"
sed -i.bak -e 's|../../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md|BUILD_AND_DEPLOY.zh-CN.md|g' \
    -e 's|../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md|BUILD_AND_DEPLOY.zh-CN.md|g' "$STAGE/README.md" 2>/dev/null || \
    sed -i '' -e 's|../../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md|BUILD_AND_DEPLOY.zh-CN.md|g' \
    -e 's|../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md|BUILD_AND_DEPLOY.zh-CN.md|g' "$STAGE/README.md"
rm -f "$STAGE/README.md.bak"
chmod 755 "$STAGE/run.sh"
chmod 644 "$STAGE/README.md" "$STAGE/QUICK_DEPLOY.zh-CN.md" "$STAGE/BUILD_AND_DEPLOY.zh-CN.md"
(
    cd "$STAGE"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$(basename "$OUTPUT")" run.sh > SHA256SUMS
    else
        shasum -a 256 "$(basename "$OUTPUT")" run.sh > SHA256SUMS
    fi
)

echo ""
info "构建完成"
echo "  镜像：$OUTPUT ($(du -h "$OUTPUT" | awk '{print $1}'))"
echo "  部署目录：$STAGE"
echo "    ├── $(basename "$OUTPUT")"
echo "    ├── run.sh"
echo "    ├── README.md"
echo "    ├── QUICK_DEPLOY.zh-CN.md"
echo "    ├── BUILD_AND_DEPLOY.zh-CN.md"
echo "    └── SHA256SUMS"
echo ""
echo "目标机："
echo "  cd /root/qaapp-simple"
echo "  docker load -i $(basename "$OUTPUT")"
echo "  vi /root/workspace/workspace/OpenHarness/config/backend/.env   # 只改 DB/Redis"
echo "  ./run.sh"
