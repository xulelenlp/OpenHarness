#!/usr/bin/env bash
# qaapp 冒烟测试：健康检查、引导接口、登录页与静态资源。
# 用法：./smoke.sh [http://127.0.0.1:8787]
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8787}"
BASE_URL="${BASE_URL%/}"

fail() { echo "[FAIL] $*" >&2; exit 1; }
ok() { echo "[ OK ] $*"; }

command -v curl >/dev/null 2>&1 || fail "未找到 curl"

health="$(curl -fsS --max-time 10 "$BASE_URL/healthz")" || fail "GET /healthz 失败"
printf '%s' "$health" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' || fail "健康检查返回异常：$health"
ok "GET /healthz -> $health"

bootstrap="$(curl -fsS --max-time 10 "$BASE_URL/api/v1/bootstrap")" || fail "GET /api/v1/bootstrap 失败"
printf '%s' "$bootstrap" | grep -q '{' || fail "bootstrap 返回不是 JSON：$bootstrap"
ok "GET /api/v1/bootstrap 正常"

index="$(curl -fsS --max-time 10 "$BASE_URL/")" || fail "GET / 失败（前端静态资源未托管？）"
printf '%s' "$index" | grep -qi '<div id="root"' || fail "首页不是预期的前端 HTML"
ok "GET / 返回前端首页"

asset="$(printf '%s' "$index" | grep -oE '/assets/[A-Za-z0-9._-]+\.js' | head -n1 || true)"
if [[ -n "$asset" ]]; then
    curl -fsS --max-time 10 "$BASE_URL$asset" >/dev/null || fail "静态资源不可访问：$asset"
    ok "GET $asset 正常"
else
    echo "[WARN] 首页未找到 /assets/*.js（构建产物可能不同，跳过）"
fi

echo "冒烟测试通过：$BASE_URL"
