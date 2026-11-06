#!/bin/sh
# qaapp 容器入口：无参数时启动 Web 服务；有参数时透传（便于 docker run ... python ... 诊断）。
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# 空密码环境变量会被 AuthManager 当成“显式空密码”，这里回退到内置默认值。
if [ -z "${QAAPP_DEFAULT_ADMIN_PASSWORD:-}" ]; then
    unset QAAPP_DEFAULT_ADMIN_PASSWORD
fi
if [ -z "${QAAPP_DEFAULT_VIEWER_PASSWORD:-}" ]; then
    unset QAAPP_DEFAULT_VIEWER_PASSWORD
fi

WORKSPACE="${OHMO_WORKSPACE:-/data}"
mkdir -p \
    "$WORKSPACE" \
    "$WORKSPACE/home" \
    "$WORKSPACE/home/.cache/matplotlib" \
    "$WORKSPACE/data" \
    "$WORKSPACE/logs" \
    "$WORKSPACE/sessions" \
    "$WORKSPACE/conversations"

exec python -m qaapp webapp run \
    --cwd /app \
    --config-dir "${QAAPP_CONFIG_DIR:-/app/config/backend}" \
    --workspace "$WORKSPACE"
