#!/usr/bin/env python3
"""对 .env 中指定的密码 / Token 做 ``ENC[...]`` 编解码。

    python scripts/env_crypt.py encrypt config/backend/.env
    python scripts/env_crypt.py decrypt config/backend/.env
    python scripts/env_crypt.py encrypt --dry-run config/backend/.env .env

服务读取 .env 时会自动还原（qaapp / abc / abcag），业务代码无需改动。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

from openharness.config.env_crypt import (
    ENCRYPTED_ENV_KEYS,
    decrypt_value,
    encrypt_value,
)

# 注意：不能用 \s（会跨行吞掉空值行的换行），只能用水平空白。
_ENV_LINE_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*)"
    r"(?P<value>[^\r\n]*?)(?P<newline>\r?\n?)$"
)


def _normalize_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _rewrite_env_file(
    path: Path, keys: set[str], decrypt: bool, dry_run: bool
) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    changed = 0
    matched = 0
    rewritten: list[str] = []

    for line in lines:
        match = _ENV_LINE_RE.match(line)
        if not match or match.group("key") not in keys:
            rewritten.append(line)
            continue

        matched += 1
        current = _normalize_env_value(match.group("value"))
        if not current:
            # 空值不需要编解码（否则空行换行会被吞掉）。
            rewritten.append(line)
            continue
        new_value = decrypt_value(current) if decrypt else encrypt_value(current)
        if new_value == current:
            rewritten.append(line)
            continue

        changed += 1
        rewritten.append(f"{match.group('prefix')}{new_value}{match.group('newline')}")

    if changed and not dry_run:
        path.write_text("".join(rewritten), encoding="utf-8")
    return matched, changed


def _existing_paths(paths: Iterable[str]) -> list[Path]:
    result = []
    for item in paths:
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(f"env file not found: {path}")
        result.append(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Encode/decode selected .env values")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("encrypt", "decrypt"):
        command_parser = subparsers.add_parser(
            command, help=f"{command.title()} selected env files in place"
        )
        command_parser.add_argument("paths", nargs="+", help="Env files to process")
        command_parser.add_argument(
            "--dry-run", action="store_true", help="Show counts without writing files"
        )

    args = parser.parse_args(argv)
    decrypt = args.command == "decrypt"
    for path in _existing_paths(args.paths):
        matched, changed = _rewrite_env_file(
            path, set(ENCRYPTED_ENV_KEYS), decrypt, args.dry_run
        )
        action = "would change" if args.dry_run else "changed"
        print(f"{path}: matched={matched}, {action}={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
