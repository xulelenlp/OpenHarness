"""``.env`` 与 ``${VAR}`` 占位符解析（数据源配置专用）。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ohmo.workspace import get_workspace_root
from qaapp.config import ENV_CONFIG_DIR

#: ``${DB_HOST}`` 形式的占位符。
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _read_env_file(path: Path) -> dict[str, str]:
    """解析最简单的 ``.env``（KEY=VALUE / export KEY=VALUE / # 注释）。"""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _dotenv_candidates(
    workspace: str | Path | None,
    *,
    cwd: str | Path | None,
    config_dir: str | Path | None,
) -> list[Path]:
    """按“配置目录 → workspace → cwd”返回候选 .env（只读第一个存在的）。"""
    candidates: list[Path] = []
    explicit = str(config_dir or os.environ.get(ENV_CONFIG_DIR) or "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve() / ".env")
    if cwd:
        candidates.append(Path(cwd).expanduser().resolve() / "config" / ".env")
    candidates.append(get_workspace_root(workspace) / ".env")
    if cwd:
        candidates.append(Path(cwd).expanduser().resolve() / ".env")
    return candidates


def load_dotenv(
    workspace: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    config_dir: str | Path | None = None,
) -> dict[str, str]:
    """读取第一个存在的 ``.env``；进程环境变量优先级更高（由调用方合并）。"""
    for path in _dotenv_candidates(workspace, cwd=cwd, config_dir=config_dir):
        if path.is_file():
            return _read_env_file(path)
    return {}


def _substitute(value: Any, env: dict[str, str]) -> Any:
    """递归替换 ``${VAR}``；缺失变量替换为空串（由校验给出明确错误）。"""
    if isinstance(value, dict):
        return {key: _substitute(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, env) for item in value]
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: env.get(m.group(1), ""), value)
    return value


def _env_with_dotenv(
    workspace: str | Path | None,
    *,
    cwd: str | Path | None,
    config_dir: str | Path | None,
) -> dict[str, str]:
    env = load_dotenv(workspace, cwd=cwd, config_dir=config_dir)
    env.update({key: value for key, value in os.environ.items() if value is not None})
    return env
