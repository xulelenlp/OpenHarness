#!/usr/bin/env python3
"""Safely rewrite qaapp 的 ``config/backend/.env``（以及可选的 datasources.json）。

设计目标：

- 部署机不需要安装 Python：脚本在镜像内执行（``docker run ... python /app/deploy/configure.py``）；
- 密码不出现在命令行 / ``ps`` 里：安装脚本把答案写进 0600 的临时 JSON，用 ``--answers-file`` 传入；
- 只改指定 key，保留注释、顺序与其它配置；原子写 + 0600；
- 写入前做必填项校验，错误列出来源 key（密码脱敏）。

支持的输入（命令行优先于 answers 文件）：

    --db-host/--db-port/--db-user/--db-password/--db-name
    --redis-host/--redis-port/--redis-password/--no-redis/--redis
    --public-url/--trust-proxy/--no-trust-proxy
    --qaapp-host/--qaapp-port
    --set KEY=VALUE（可重复，用于高级项）

answers 文件是 JSON，键名与上面一致（下划线形式，如 ``db_host``、``no_redis``）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ENV_FILENAME = ".env"
DATASOURCES_FILENAME = "datasources.json"

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_HINTS = ("PASSWORD", "API_KEY", "TOKEN", "SECRET")

_REQUIRED_OPENHARNESS = (
    "OPENHARNESS_PROVIDER",
    "OPENHARNESS_API_FORMAT",
    "OPENHARNESS_MODEL",
    "OPENHARNESS_BASE_URL",
    "OPENHARNESS_AUTH_SOURCE",
)

_AUTH_ENV_MAP = {
    "dashscope_api_key": "OPENHARNESS_DASHSCOPE_API_KEY",
    "openai_api_key": "OPENHARNESS_OPENAI_API_KEY",
}


# ---------------------------------------------------------------------------
# .env 读写
# ---------------------------------------------------------------------------


def _assignment(line: str) -> tuple[str, str] | None:
    """Return ``(key, raw_value)`` for an assignment line, else ``None``."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    body = stripped[len("export ") :].strip() if stripped.startswith("export ") else stripped
    if "=" not in body:
        return None
    key, value = body.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines the same way ``openharness.config.layers`` does."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        pair = _assignment(line)
        if pair:
            values[pair[0]] = _strip_quotes(pair[1])
    return values


def _validate_assignment(key: str, value: str) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(f"非法的环境变量名：{key!r}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} 的值不能包含换行")
    if value != value.strip():
        raise ValueError(
            f"{key} 的值首尾不能有空白（.env 解析会去掉首尾空白，避免密码被静默截断）"
        )
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        raise ValueError(f"{key} 的值不要用引号包裹（.env 解析会把引号去掉）")


def set_env_value(text: str, key: str, value: str) -> str:
    """Replace the first assignment of ``key`` in place; append it when missing.

    Duplicate assignments of the same key are dropped so the effective value is
    unambiguous (the runtime parser keeps the last occurrence).
    """
    _validate_assignment(key, value)
    lines = text.splitlines()
    replaced = False
    output: list[str] = []
    for line in lines:
        pair = _assignment(line)
        if pair and pair[0] == key:
            if not replaced:
                output.append(f"{key}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"{key}={value}")
    return "\n".join(output).rstrip("\n") + "\n"


def write_text_atomic(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Atomically write ``text`` to ``path`` with the requested mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _backup(path: Path) -> Path | None:
    """Copy ``path`` to a timestamped 0600 sibling and return the backup path."""
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    shutil.copy2(path, candidate)
    os.chmod(candidate, 0o600)
    return candidate


# ---------------------------------------------------------------------------
# 值处理
# ---------------------------------------------------------------------------


def is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


def mask_value(key: str, value: str) -> str:
    if value and is_secret_key(key):
        return "***"
    return value


def normalize_origin(url: str) -> str:
    """Turn ``http://host:port/path`` (or bare ``host:port``) into its origin."""
    raw = (url or "").strip()
    if not raw or raw == "*":
        return "*"
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"无法解析公开访问地址：{url!r}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"公开访问地址只支持 http/https：{url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _as_bool(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{key} 需要布尔值（true/false），收到 {value!r}")


def _pick(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# 数据源 JSON
# ---------------------------------------------------------------------------


def update_datasources(path: Path, *, redis_enabled: bool | None, dry_run: bool) -> list[str]:
    """Set ``redis.enabled`` in datasources.json (backup + atomic write)."""
    if redis_enabled is None or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象")
    redis = raw.setdefault("redis", {})
    if not isinstance(redis, dict):
        raise ValueError(f"{path}: redis 必须是 JSON 对象")
    current = redis.get("enabled")
    redis["enabled"] = redis_enabled
    if dry_run:
        return [f"datasources.json: redis.enabled {current!r} -> {redis_enabled!r}"]
    _backup(path)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    write_text_atomic(
        path,
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        mode=mode or 0o600,
    )
    return [f"datasources.json: redis.enabled {current!r} -> {redis_enabled!r}"]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_values(
    values: dict[str, str], *, redis_enabled: bool
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for the effective .env values."""
    errors: list[str] = []
    warnings: list[str] = []

    for key in _REQUIRED_OPENHARNESS:
        if not values.get(key, "").strip():
            errors.append(f"{key} 未配置")

    base_url = values.get("OPENHARNESS_BASE_URL", "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        errors.append("OPENHARNESS_BASE_URL 必须是 http(s) 地址")

    auth_source = values.get("OPENHARNESS_AUTH_SOURCE", "").strip()
    credential_key = _AUTH_ENV_MAP.get(auth_source)
    has_credential = bool(values.get("OPENHARNESS_API_KEY", "").strip()) or bool(
        credential_key and values.get(credential_key, "").strip()
    )
    if not has_credential:
        warnings.append(
            "未检测到模型密钥（OPENHARNESS_API_KEY 或 AUTH_SOURCE 对应的变量）；"
            "如果由进程环境变量注入可忽略"
        )

    for key in ("DB_HOST", "DB_USER", "DB_NAME"):
        if not values.get(key, "").strip():
            errors.append(f"{key} 未配置")
    db_port = values.get("DB_PORT", "").strip()
    if not db_port:
        errors.append("DB_PORT 未配置")
    else:
        try:
            if not 1 <= int(db_port) <= 65535:
                raise ValueError
        except ValueError:
            errors.append(f"DB_PORT 不是合法端口：{db_port!r}")

    if redis_enabled:
        if not values.get("REDIS_HOST", "").strip():
            errors.append("REDIS_HOST 未配置（或用 --no-redis 关闭 Redis）")
        redis_port = values.get("REDIS_PORT", "").strip()
        if not redis_port:
            errors.append("REDIS_PORT 未配置")
        else:
            try:
                if not 1 <= int(redis_port) <= 65535:
                    raise ValueError
            except ValueError:
                errors.append(f"REDIS_PORT 不是合法端口：{redis_port!r}")

    qaapp_port = values.get("QAAPP_PORT", "").strip()
    if qaapp_port:
        try:
            if not 1 <= int(qaapp_port) <= 65535:
                raise ValueError
        except ValueError:
            errors.append(f"QAAPP_PORT 不是合法端口：{qaapp_port!r}")

    if not values.get("QAAPP_STATIC_DIR", "").strip():
        warnings.append("QAAPP_STATIC_DIR 为空：容器内由 compose 注入，裸机运行时将没有前端页面")
    return errors, warnings


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _default_config_dir() -> Path:
    env_dir = os.environ.get("QAAPP_CONFIG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    container_dir = Path("/app/config/backend")
    if container_dir.is_dir():
        return container_dir
    return Path(__file__).resolve().parents[1] / "config" / "backend"


def _load_answers(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("answers 文件顶层必须是 JSON 对象")
    return raw


def _collect_updates(args: argparse.Namespace, answers: dict[str, Any]) -> dict[str, str]:
    updates: dict[str, str] = {}

    def put(key: str, value: Any) -> None:
        # None = 未提供（保留原值）；空串 = 显式清空（例如无密码的数据库）。
        if value is None:
            return
        updates[key] = str(value).strip()

    put("DB_HOST", _pick(args.db_host, answers.get("db_host")))
    put("DB_PORT", _pick(args.db_port, answers.get("db_port")))
    put("DB_USER", _pick(args.db_user, answers.get("db_user")))
    put("DB_PASSWORD", _pick(args.db_password, answers.get("db_password")))
    put("DB_NAME", _pick(args.db_name, answers.get("db_name")))
    put("REDIS_HOST", _pick(args.redis_host, answers.get("redis_host")))
    put("REDIS_PORT", _pick(args.redis_port, answers.get("redis_port")))
    put("REDIS_PASSWORD", _pick(args.redis_password, answers.get("redis_password")))
    put("QAAPP_HOST", _pick(args.qaapp_host, answers.get("qaapp_host")))
    put("QAAPP_PORT", _pick(args.qaapp_port, answers.get("qaapp_port")))

    public_url = _pick(args.public_url, answers.get("public_url"))
    if public_url is not None:
        updates["QAAPP_CORS_ORIGINS"] = normalize_origin(str(public_url))

    trust_proxy = _pick(args.trust_proxy, answers.get("trust_proxy"))
    if trust_proxy is not None:
        updates["QAAPP_AUTH_TRUST_PROXY"] = (
            "true" if _as_bool(trust_proxy, key="trust_proxy") else "false"
        )

    for item in args.set_values or []:
        if "=" not in item:
            raise ValueError(f"--set 需要 KEY=VALUE，收到 {item!r}")
        key, value = item.split("=", 1)
        updates[key.strip()] = value.strip()

    return updates


def _redis_enabled(args: argparse.Namespace, answers: dict[str, Any]) -> bool | None:
    if getattr(args, "no_redis", False):
        return False
    if getattr(args, "redis", False):
        return True
    if "no_redis" in answers and _as_bool(answers.get("no_redis"), key="no_redis"):
        return False
    return None


def _print_changes(
    before: dict[str, str], updates: dict[str, str], *, dry_run: bool
) -> None:
    header = "将写入的变更：" if dry_run else "已写入的变更："
    print(header)
    printed = False
    for key in sorted(updates):
        old = before.get(key, "")
        new = updates[key]
        if old == new:
            continue
        printed = True
        print(
            f"  {key}: {mask_value(key, old) or '(空)'} -> "
            f"{mask_value(key, new) or '(空)'}"
        )
    if not printed:
        print("  （无变化）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="改写 qaapp config/backend/.env")
    parser.add_argument("--config-dir", default=None, help="配置目录（默认 QAAPP_CONFIG_DIR 或仓库目录）")
    parser.add_argument("--answers-file", default=None, help="0600 的 JSON 答案文件")
    parser.add_argument("--db-host")
    parser.add_argument("--db-port")
    parser.add_argument("--db-user")
    parser.add_argument("--db-password")
    parser.add_argument("--db-name")
    parser.add_argument("--redis-host")
    parser.add_argument("--redis-port")
    parser.add_argument("--redis-password")
    parser.add_argument("--no-redis", action="store_true", help="关闭 Redis 数据源")
    parser.add_argument("--redis", action="store_true", help="启用 Redis 数据源")
    parser.add_argument("--public-url", help="浏览器访问地址；写入 QAAPP_CORS_ORIGINS 的 origin")
    parser.add_argument("--trust-proxy", dest="trust_proxy", action="store_true", default=None)
    parser.add_argument("--no-trust-proxy", dest="trust_proxy", action="store_false")
    parser.add_argument("--qaapp-host")
    parser.add_argument("--qaapp-port")
    parser.add_argument("--set", dest="set_values", action="append", metavar="KEY=VALUE")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--dry-run", action="store_true", help="只打印变更，不写文件")
    args = parser.parse_args(argv)

    config_dir = Path(args.config_dir).expanduser() if args.config_dir else _default_config_dir()
    env_path = config_dir / ENV_FILENAME
    datasources_path = config_dir / DATASOURCES_FILENAME

    if not env_path.is_file():
        print(f"错误：找不到 {env_path}", file=sys.stderr)
        return 2

    try:
        answers = _load_answers(args.answers_file)
        updates = _collect_updates(args, answers)
        redis_override = _redis_enabled(args, answers)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    before_text = env_path.read_text(encoding="utf-8")
    before = parse_env(before_text)

    for key, value in updates.items():
        try:
            _validate_assignment(key, value)
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

    merged = dict(before)
    merged.update(updates)

    # 部署包默认由后端托管前端产物；空值会让裸机启动时没有页面。
    if not merged.get("QAAPP_STATIC_DIR", "").strip():
        updates["QAAPP_STATIC_DIR"] = "/app/frontend/webapp/dist"
        merged["QAAPP_STATIC_DIR"] = updates["QAAPP_STATIC_DIR"]

    redis_enabled = True
    if datasources_path.is_file():
        try:
            raw_datasources = json.loads(datasources_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"错误：{datasources_path} 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        configured = raw_datasources.get("redis", {})
        if isinstance(configured, dict) and "enabled" in configured:
            try:
                redis_enabled = _as_bool(configured.get("enabled"), key="redis.enabled")
            except ValueError as exc:
                print(f"错误：{exc}", file=sys.stderr)
                return 2
    if redis_override is not None:
        redis_enabled = redis_override

    errors, warnings = validate_values(merged, redis_enabled=redis_enabled)
    for warning in warnings:
        print(f"警告：{warning}")
    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 2

    print(f"配置目录：{config_dir}")
    print(f".env：{env_path}")
    print(f"Redis：{'启用' if redis_enabled else '关闭'}")

    if args.check:
        print("校验通过（未写文件）。")
        return 0

    new_text = before_text
    apply_updates = {} if args.check else updates
    for key, value in apply_updates.items():
        new_text = set_env_value(new_text, key, value)

    datasource_changes: list[str] = []
    if redis_override is not None and datasources_path.is_file():
        try:
            datasource_changes = update_datasources(
                datasources_path, redis_enabled=redis_override, dry_run=args.dry_run
            )
        except (OSError, ValueError) as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2

    if args.dry_run:
        _print_changes(before, updates, dry_run=True)
        for change in datasource_changes:
            print(f"  {change}")
        return 0

    backup = _backup(env_path)
    if backup is not None:
        print(f"已备份：{backup}")
    write_text_atomic(env_path, new_text, mode=0o600)
    _print_changes(before, updates, dry_run=False)
    for change in datasource_changes:
        print(f"  {change}")
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
