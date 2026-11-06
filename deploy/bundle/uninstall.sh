#!/usr/bin/env bash
# qaapp 卸载：停止并删除容器；默认保留 config/ 与 data/。
#
# 用法：./uninstall.sh [--purge] [--yes]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ENV="$SCRIPT_DIR/stack.env"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yaml"
PURGE=0
YES=0

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=1; shift ;;
        --yes|-y) YES=1; shift ;;
        --help|-h) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[[ -f "$COMPOSE_FILE" ]] || die "未找到 $COMPOSE_FILE"
[[ -f "$SCRIPT_DIR/bundle.meta" ]] || die "当前目录不像 qaapp 部署目录：$SCRIPT_DIR"

if [[ -f "$STACK_ENV" ]]; then
    docker compose --env-file "$STACK_ENV" -f "$COMPOSE_FILE" down || true
else
    docker compose -f "$COMPOSE_FILE" down || true
fi
info "容器已停止并删除（数据未动）。"

if [[ "$PURGE" -eq 1 ]]; then
    echo ""
    echo "将删除：$SCRIPT_DIR/config  $SCRIPT_DIR/data  $SCRIPT_DIR/backups  $SCRIPT_DIR/stack.env"
    if [[ "$YES" -ne 1 ]]; then
        read -r -p "确认删除以上数据？输入 yes 继续： " answer || true
        [[ "$answer" == "yes" ]] || die "已取消"
    fi
    # 防御：只允许删除本部署目录下的固定子目录。
    for target in "$SCRIPT_DIR/config" "$SCRIPT_DIR/data" "$SCRIPT_DIR/backups"; do
        case "$target" in
            "$SCRIPT_DIR"/config|"$SCRIPT_DIR"/data|"$SCRIPT_DIR"/backups)
                rm -rf -- "$target"
                info "已删除 $target"
                ;;
            *) die "拒绝删除非预期路径：$target" ;;
        esac
    done
    rm -f "$STACK_ENV"
    info "已清除 stack.env"
fi
