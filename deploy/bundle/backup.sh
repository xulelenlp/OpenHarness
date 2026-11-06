#!/usr/bin/env bash
# qaapp 备份：config/ + data/ + stack.env（含密钥，输出 0600）。
#
# 用法：./backup.sh [--keep 14]
# 恢复：停止容器 -> 解包覆盖到部署目录 -> 重新 up -d
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/backups"
KEEP=14

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "[INFO] $*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep) KEEP="$2"; shift 2 ;;
        --help|-h) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

[[ "$KEEP" =~ ^[0-9]+$ && "$KEEP" -ge 1 ]] || die "--keep 需要正整数"
[[ -d "$SCRIPT_DIR/config" ]] || die "缺少 config 目录"
[[ -d "$SCRIPT_DIR/data" ]] || die "缺少 data 目录"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/qaapp-$STAMP.tar.gz"

info "打包 $TARGET ..."
tar -czf "$TARGET" \
    --exclude='data/home/.cache' \
    --exclude='data/logs/*.log' \
    -C "$SCRIPT_DIR" \
    config data stack.env 2>/dev/null || \
    tar -czf "$TARGET" -C "$SCRIPT_DIR" config data

chmod 600 "$TARGET"
info "完成：$TARGET ($(du -h "$TARGET" | awk '{print $1}'))"

# 只保留最近 N 份
while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    rm -f "$file"
    info "清理旧备份：$file"
done < <(ls -1t "$BACKUP_DIR"/qaapp-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) || true)
