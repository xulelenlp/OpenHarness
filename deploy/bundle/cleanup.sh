#!/usr/bin/env bash
# qaapp 会话清理（默认 dry-run，确认后加 --apply）。
#
# 用法：./cleanup.sh [--older-than-days 30] [--keep-latest 100] [--apply]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ENV="$SCRIPT_DIR/stack.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"

die() { echo "错误：$*" >&2; exit 1; }

[[ -f "$STACK_ENV" ]] || die "未找到 $STACK_ENV（先执行 install.sh）"
command -v docker >/dev/null 2>&1 || die "未找到 docker"

docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" exec -T qaapp \
    python -m qaapp cleanup \
    --cwd /app \
    --config-dir /app/config/backend \
    --workspace /data \
    "$@"
