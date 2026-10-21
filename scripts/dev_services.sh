#!/usr/bin/env bash
#
# 前后端开发服务管理（macOS / Linux）
#
#   ./scripts/dev_services.sh start            # 后台静默启动后端 + 前端
#   ./scripts/dev_services.sh stop             # 停止后端 + 前端
#   ./scripts/dev_services.sh restart          # 重启后端 + 前端
#   ./scripts/dev_services.sh status           # 查看运行状态与健康检查
#   ./scripts/dev_services.sh logs backend     # 查看后端日志（-f 持续跟随）
#   ./scripts/dev_services.sh logs frontend -f # 查看前端日志（持续跟随）
#
# 也可以只操作其中一端：
#   ./scripts/dev_services.sh start backend
#   ./scripts/dev_services.sh stop frontend
#
# 说明：
# - 后端复用 qaapp 自带的守护进程命令：python -m qaapp webapp start/stop，
#   pid 文件在 $OHMO_WORKSPACE/webapp.pid（默认 ~/.ohmo/webapp.pid），
#   日志在 $OHMO_WORKSPACE/logs/webapp.log（默认 ~/.ohmo/logs/webapp.log）。
# - 前端用 nohup 后台启动 `npm run dev`，pid 与日志放在仓库内的
#   .openharness/run/ 下，不会污染 git。
# - 停止时会先按 pid 结束进程树，再按端口兜底清理残留的 qaapp / vite 进程，
#   避免 restart 时端口仍被占用。
#
# 可选环境变量：
#   PYTHON           指定 Python（默认优先 .venv/bin/python）
#   OHMO_WORKSPACE   指定后端 workspace（默认 ~/.ohmo）
#   FRONTEND_PORT    覆盖前端端口（默认 5173）
#   START_TIMEOUT    启动健康检查超时秒数（默认 40）
#
# 后端端口不在本脚本里配置：它取自 webapp.json 的 port（默认 8787），
# 与 abcqa webapp run 实际监听的端口保持一致。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend/webapp"
RUN_DIR="$ROOT_DIR/.openharness/run"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
FRONTEND_LOG="$RUN_DIR/frontend.log"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT=""
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
START_TIMEOUT="${START_TIMEOUT:-40}"

cd "$ROOT_DIR"

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

info() { printf '[services] %s\n' "$*"; }
warn() { printf '[services] 警告：%s\n' "$*" >&2; }
err()  { printf '[services] 错误：%s\n' "$*" >&2; }

usage() {
    sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
}

pid_alive() {
    [[ -n "${1:-}" ]] && kill -0 "$1" 2>/dev/null
}

# 递归结束进程树（npm -> sh -> vite 这类多级进程）
kill_tree() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
        kill_tree "$child"
    done
    kill -TERM "$pid" 2>/dev/null || true
}

# 列出监听指定端口的 pid
listener_pids() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true
}

# 端口仍被占用时，只清理命令行里匹配给定关键字的进程，避免误杀无关服务
kill_port_listeners() {
    local port="$1"; shift
    local pid cmd pat matched
    for pid in $(listener_pids "$port"); do
        cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
        matched=0
        for pat in "$@"; do
            if [[ "$cmd" == *"$pat"* ]]; then
                matched=1
                break
            fi
        done
        if [[ "$matched" == "1" ]]; then
            warn "端口 $port 仍被占用，结束残留进程 $pid（$cmd）"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
}

wait_port_free() {
    local port="$1" timeout="${2:-10}" i=0
    while [[ -n "$(listener_pids "$port")" ]]; do
        if [[ "$i" -ge $((timeout * 2)) ]]; then
            return 1
        fi
        sleep 0.5
        i=$((i + 1))
    done
    return 0
}

wait_http_ok() {
    local url="$1" timeout="${2:-$START_TIMEOUT}" i=0
    while [[ "$i" -lt $((timeout * 2)) ]]; do
        if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
        i=$((i + 1))
    done
    return 1
}

resolve_python() {
    if [[ -n "${PYTHON:-}" ]]; then
        printf '%s' "$PYTHON"
    elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
        printf '%s' "$ROOT_DIR/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        command -v python
    fi
}

PYTHON="$(resolve_python)"
[[ -n "$PYTHON" ]] || err "找不到 Python 解释器，请先创建 .venv 或设置 PYTHON 环境变量"

WORKSPACE_ARGS=()
if [[ -n "${OHMO_WORKSPACE:-}" ]]; then
    WORKSPACE_ARGS=(--workspace "$OHMO_WORKSPACE")
fi

backend_workspace() {
    if [[ -n "${OHMO_WORKSPACE:-}" ]]; then
        printf '%s' "$OHMO_WORKSPACE"
    else
        printf '%s' "$HOME/.ohmo"
    fi
}

backend_log() {
    printf '%s' "$(backend_workspace)/logs/webapp.log"
}

# 后端端口：优先环境变量，其次读 webapp 配置（与 qaapp 的解析逻辑保持一致）
resolve_backend_port() {
    local port
    port="$("$PYTHON" -m qaapp webapp status \
        ${WORKSPACE_ARGS[@]+"${WORKSPACE_ARGS[@]}"} --cwd "$ROOT_DIR" 2>/dev/null \
        | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["port"])' 2>/dev/null || true)"
    printf '%s' "${port:-8787}"
}

# ---------------------------------------------------------------------------
# 后端
# ---------------------------------------------------------------------------

backend_pid() {
    local pid_file
    pid_file="$(backend_workspace)/webapp.pid"
    [[ -f "$pid_file" ]] && tr -d '[:space:]' < "$pid_file" || true
}

backend_running() {
    local pid
    pid="$(backend_pid)"
    pid_alive "$pid"
}

start_backend() {
    local port="$1" pid

    if backend_running; then
        info "后端已在运行（pid=$(backend_pid)，端口 $port），跳过启动"
        return 0
    fi

    # pid 文件残留但进程已死：清掉，避免 qaapp start 误判
    rm -f "$(backend_workspace)/webapp.pid"

    if [[ -n "$(listener_pids "$port")" ]]; then
        warn "端口 $port 已被占用，先清理残留的后端进程"
        kill_port_listeners "$port" qaapp uvicorn
        if ! wait_port_free "$port" 10; then
            err "端口 $port 仍被占用（可能不是本项目进程），请手动处理后重试"
            return 1
        fi
    fi

    info "启动后端：$PYTHON -m qaapp webapp start（端口 $port）"
    "$PYTHON" -m qaapp webapp start \
        ${WORKSPACE_ARGS[@]+"${WORKSPACE_ARGS[@]}"} --cwd "$ROOT_DIR" >/dev/null

    if wait_http_ok "http://$BACKEND_HOST:$port/healthz" "$START_TIMEOUT"; then
        info "后端已就绪：http://$BACKEND_HOST:$port（日志：$(backend_log)）"
    else
        err "后端启动超时，请查看日志：$(backend_log)"
        return 1
    fi
}

stop_backend() {
    local port="$1" pid
    pid="$(backend_pid)"

    if pid_alive "$pid"; then
        info "停止后端（pid=$pid）"
        "$PYTHON" -m qaapp webapp stop \
            ${WORKSPACE_ARGS[@]+"${WORKSPACE_ARGS[@]}"} >/dev/null 2>&1 || true
        # qaapp stop 发送 SIGTERM 后立即返回，等它真正退出
        local i=0
        while pid_alive "$pid"; do
            if [[ "$i" -ge 20 ]]; then
                warn "后端进程 $pid 未在 10 秒内退出，强制结束"
                kill -KILL "$pid" 2>/dev/null || true
                break
            fi
            sleep 0.5
            i=$((i + 1))
        done
    fi

    # 兜底：pid 文件丢失 / 前台 run 启动的残留进程
    kill_port_listeners "$port" qaapp uvicorn
    if wait_port_free "$port" 10; then
        info "后端已停止（端口 $port 已释放）"
    else
        err "后端端口 $port 仍未释放，请手动检查：lsof -nP -iTCP:$port -sTCP:LISTEN"
        return 1
    fi
}

backend_status() {
    local port="$1" pid state
    pid="$(backend_pid)"

    if pid_alive "$pid"; then
        state="运行中（pid=$pid）"
    else
        state="未运行"
    fi

    if curl -fsS -o /dev/null --max-time 2 "http://$BACKEND_HOST:$port/healthz" 2>/dev/null; then
        state="$state，健康检查通过"
    elif pid_alive "$pid"; then
        state="$state，健康检查失败"
    fi

    printf '  后端：%s\n' "$state"
    printf '        地址：http://%s:%s\n' "$BACKEND_HOST" "$port"
    printf '        日志：%s\n' "$(backend_log)"
}

# ---------------------------------------------------------------------------
# 前端
# ---------------------------------------------------------------------------

frontend_pid() {
    [[ -f "$FRONTEND_PID_FILE" ]] && tr -d '[:space:]' < "$FRONTEND_PID_FILE" || true
}

frontend_running() {
    local pid
    pid="$(frontend_pid)"
    pid_alive "$pid"
}

start_frontend() {
    local port="$1" pid

    if frontend_running; then
        info "前端已在运行（pid=$(frontend_pid)，端口 $port），跳过启动"
        return 0
    fi

    rm -f "$FRONTEND_PID_FILE"

    if [[ -n "$(listener_pids "$port")" ]]; then
        warn "端口 $port 已被占用，先清理残留的前端进程"
        kill_port_listeners "$port" vite
        if ! wait_port_free "$port" 10; then
            err "端口 $port 仍被占用（可能不是本项目进程），请手动处理后重试"
            return 1
        fi
    fi

    if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
        err "前端依赖未安装，请先执行：cd frontend/webapp && npm install"
        return 1
    fi

    mkdir -p "$RUN_DIR"
    {
        printf '\n=== %s 启动前端（端口 %s）===\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$port"
    } >>"$FRONTEND_LOG"

    info "启动前端：npm run dev -- --port $port（日志：$FRONTEND_LOG）"
    (
        cd "$FRONTEND_DIR"
        nohup npm run dev -- --host 0.0.0.0 --port "$port" >>"$FRONTEND_LOG" 2>&1 &
        echo $! >"$FRONTEND_PID_FILE"
    )

    if wait_http_ok "http://127.0.0.1:$port/" "$START_TIMEOUT"; then
        info "前端已就绪：http://localhost:$port"
    else
        err "前端启动超时，请查看日志：$FRONTEND_LOG"
        return 1
    fi
}

stop_frontend() {
    local port="$1" pid
    pid="$(frontend_pid)"

    if pid_alive "$pid"; then
        info "停止前端（pid=$pid）"
        kill_tree "$pid"
        local i=0
        while pid_alive "$pid"; do
            if [[ "$i" -ge 20 ]]; then
                warn "前端进程 $pid 未在 10 秒内退出，强制结束"
                kill -KILL "$pid" 2>/dev/null || true
                break
            fi
            sleep 0.5
            i=$((i + 1))
        done
    fi
    rm -f "$FRONTEND_PID_FILE"

    # 兜底：npm 子进程（vite）可能比父进程活得更久
    kill_port_listeners "$port" vite
    if wait_port_free "$port" 10; then
        info "前端已停止（端口 $port 已释放）"
    else
        err "前端端口 $port 仍未释放，请手动检查：lsof -nP -iTCP:$port -sTCP:LISTEN"
        return 1
    fi
}

frontend_status() {
    local port="$1" pid state
    pid="$(frontend_pid)"

    if pid_alive "$pid"; then
        state="运行中（pid=$pid）"
    else
        state="未运行"
    fi

    if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$port/" 2>/dev/null; then
        state="$state，健康检查通过"
    elif pid_alive "$pid"; then
        state="$state，健康检查失败"
    fi

    printf '  前端：%s\n' "$state"
    printf '        地址：http://localhost:%s\n' "$port"
    printf '        日志：%s\n' "$FRONTEND_LOG"
}

# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

start_services() {
    local target="${1:-all}"
    local backend_port
    backend_port="$(resolve_backend_port)"

    case "$target" in
        backend)  start_backend "$backend_port" ;;
        frontend) start_frontend "$FRONTEND_PORT" ;;
        all|"")
            start_backend "$backend_port"
            start_frontend "$FRONTEND_PORT"
            ;;
        *) err "未知目标：$target（可选 all / backend / frontend）"; return 1 ;;
    esac
}

stop_services() {
    local target="${1:-all}"
    local backend_port
    backend_port="$(resolve_backend_port)"

    case "$target" in
        backend)  stop_backend "$backend_port" ;;
        frontend) stop_frontend "$FRONTEND_PORT" ;;
        all|"")
            # 两端都尝试停止，任一失败也不阻断另一端
            stop_backend "$backend_port" || true
            stop_frontend "$FRONTEND_PORT" || true
            ;;
        *) err "未知目标：$target（可选 all / backend / frontend）"; return 1 ;;
    esac
}

status_services() {
    local backend_port
    backend_port="$(resolve_backend_port)"
    printf '工作目录：%s\n' "$ROOT_DIR"
    printf '后端端口：%s\n' "$backend_port"
    printf '前端端口：%s\n' "$FRONTEND_PORT"
    backend_status "$backend_port"
    frontend_status "$FRONTEND_PORT"
}

logs_cmd() {
    local target="${1:-all}" follow="${2:-}"
    local tail_args=(-n 100)
    if [[ "$follow" == "-f" || "$follow" == "--follow" ]]; then
        tail_args+=(-f)
    fi

    case "$target" in
        backend)
            info "后端日志：$(backend_log)"
            tail "${tail_args[@]}" "$(backend_log)"
            ;;
        frontend)
            info "前端日志：$FRONTEND_LOG"
            tail "${tail_args[@]}" "$FRONTEND_LOG"
            ;;
        all|"")
            local files=()
            if [[ -f "$(backend_log)" ]]; then files+=("$(backend_log)"); fi
            if [[ -f "$FRONTEND_LOG" ]]; then files+=("$FRONTEND_LOG"); fi
            if [[ "${#files[@]}" -eq 0 ]]; then
                warn "日志尚不存在，服务可能还没启动"
                return 0
            fi
            tail "${tail_args[@]}" "${files[@]}"
            ;;
        *)
            err "未知日志目标：$target（可选 all / backend / frontend）"
            return 1
            ;;
    esac
}

main() {
    local cmd="${1:-}"
    [[ $# -gt 0 ]] && shift

    case "$cmd" in
        start)   start_services "${1:-all}" ;;
        stop)    stop_services "${1:-all}" ;;
        restart)
            stop_services "${1:-all}"
            start_services "${1:-all}"
            ;;
        status)  status_services ;;
        logs)    logs_cmd "${1:-all}" "${2:-}" ;;
        help|-h|--help|"") usage ;;
        *) err "未知命令：$cmd"; usage ;;
    esac
}

main "$@"
