#!/usr/bin/env bash
# 从当前仓库构建“代码 + 环境”的 qaapp 镜像，并整理成最简部署目录。
#
# 构建机不需要 Docker，只需要 Python 3.9+（带 pip）和外网。
# 目标机只需要 Docker：docker load + 改项目 config/backend/.env + run.sh。
#
# 用法：
#   ./deploy/simple/build.sh
#   ./deploy/simple/build.sh --tag openharness-qaapp:0.1.9-offline
#   ./deploy/simple/build.sh --reuse-image      # 复用已有镜像，只整理部署目录
#   ./deploy/simple/build.sh --from-image dist/qaapp-image-0.1.9-amd64.tar.gz
#                                               # 日常改代码：保留环境/依赖，只替换代码（无需外网）
#   ./deploy/simple/build.sh --refresh          # 重新下载基础镜像/依赖
#
# 产物：
#   dist/qaapp-image-<version>-amd64.tar.gz
#   dist/qaapp-simple/
#   ├── qaapp-image-<version>-amd64.tar.gz
#   ├── run.sh
#   ├── README.md
#   ├── QUICK_DEPLOY.zh-CN.md
#   └── SHA256SUMS
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TAG=""
OUTPUT=""
WORK_DIR="${WORK_DIR:-/tmp/openharness-offline}"
REUSE_IMAGE=0
REFRESH=0
CLEAN_WORK=0
FROM_IMAGE=""
STAGE=""

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --reuse-image) REUSE_IMAGE=1; shift ;;
        --from-image) FROM_IMAGE="$2"; shift 2 ;;
        --refresh) REFRESH=1; shift ;;
        --clean-work) CLEAN_WORK=1; shift ;;
        --help|-h) usage ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "未找到 $PYTHON_BIN"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "需要 Python 3.9+"
if [[ -z "$FROM_IMAGE" ]]; then
    "$PYTHON_BIN" -m pip -V >/dev/null 2>&1 || die "当前 Python 没有 pip（完整构建需要）"
fi
if [[ "$REUSE_IMAGE" -eq 1 && -n "$FROM_IMAGE" ]]; then
    die "--reuse-image 与 --from-image 不能同时使用"
fi
if [[ "$REUSE_IMAGE" -eq 1 && ! -s "$OUTPUT" ]]; then
    die "--reuse-image 指定的镜像不存在：$OUTPUT"
fi
if [[ -n "$FROM_IMAGE" && ! -f "$FROM_IMAGE" ]]; then
    die "--from-image 不存在：$FROM_IMAGE"
fi

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
[[ -n "$TAG" ]] || TAG="openharness-qaapp:${VERSION}-offline"
[[ -n "$OUTPUT" ]] || OUTPUT="$ROOT/dist/qaapp-image-${VERSION}-amd64.tar.gz"
STAGE="$ROOT/dist/qaapp-simple"

[[ -f "$ROOT/frontend/webapp/dist/index.html" ]] || die \
    "缺少 frontend/webapp/dist/index.html；请先在 frontend/webapp 执行 npm ci && npm run build"

info "版本 $VERSION / commit $COMMIT / tag $TAG"
info "构建机：$("$PYTHON_BIN" -V 2>&1) / 不需要 Docker"

if [[ -n "$FROM_IMAGE" ]]; then
    BUILD_ARGS=(--tag "$TAG" --output "$OUTPUT" --work-dir "$WORK_DIR" --from-image "$FROM_IMAGE")
    [[ "$CLEAN_WORK" -eq 1 ]] && BUILD_ARGS+=(--clean)
    info "从已有镜像离线重建（保留环境/依赖，只替换代码，约 2–4 分钟）..."
    "$PYTHON_BIN" "$ROOT/deploy/offline/build_image.py" "${BUILD_ARGS[@]}"
elif [[ "$REUSE_IMAGE" -eq 1 && -s "$OUTPUT" && "$REFRESH" -eq 0 ]]; then
    info "复用已有镜像：$OUTPUT"
else
    BUILD_ARGS=(--tag "$TAG" --output "$OUTPUT" --work-dir "$WORK_DIR")
    [[ "$REFRESH" -eq 1 ]] && BUILD_ARGS+=(--refresh)
    [[ "$CLEAN_WORK" -eq 1 ]] && BUILD_ARGS+=(--clean)
    info "开始完整构建（首次需要下载基础镜像/依赖，约 10–20 分钟）..."
    "$PYTHON_BIN" "$ROOT/deploy/offline/build_image.py" "${BUILD_ARGS[@]}"
fi
[[ -s "$OUTPUT" ]] || die "镜像未生成：$OUTPUT"

info "整理最简部署目录：$STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ln "$OUTPUT" "$STAGE/$(basename "$OUTPUT")" 2>/dev/null || cp "$OUTPUT" "$STAGE/$(basename "$OUTPUT")"
# 让 run.sh 的默认镜像 tag 与本次构建一致（自定义 --tag 时也能直接 ./run.sh）
sed "s|^IMAGE=.*|IMAGE=\"\${IMAGE:-$TAG}\"|" "$ROOT/deploy/simple/run.sh" > "$STAGE/run.sh"
cp "$ROOT/deploy/simple/README.md" "$STAGE/README.md"
cp "$ROOT/docs/qaapp/QUICK_DEPLOY.zh-CN.md" "$STAGE/QUICK_DEPLOY.zh-CN.md"
chmod 755 "$STAGE/run.sh"
chmod 644 "$STAGE/README.md" "$STAGE/QUICK_DEPLOY.zh-CN.md"

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
echo "    └── SHA256SUMS"
echo ""
echo "拷贝到内网服务器："
echo "  scp -r $STAGE root@<server>:/root/"
echo ""
echo "目标机："
echo "  cd /root/qaapp-simple"
echo "  docker load -i $(basename "$OUTPUT")"
echo "  vi /root/workspace/workspace/OpenHarness/config/backend/.env   # 只改 DB/Redis"
echo "  ./run.sh"
