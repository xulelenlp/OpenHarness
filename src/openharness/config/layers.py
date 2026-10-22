"""Layered configuration primitives shared by the CLI and the web service.

Configuration lives in one root (``config/``) and is composed from several
layers, lowest priority first::

    base/  ->  <env>/  ->  local/

JSON files are single source files; ``${VAR}`` placeholders are expanded from
the supplied environment.

The module has no dependency on ``qaapp`` so both the core CLI and the web
service can share exactly the same merge / expansion semantics.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

__all__ = [
    "ConfigLayerError",
    "expand_env",
    "load_env_file",
    "load_json_file",
    "read_env_file",
]


class ConfigLayerError(ValueError):
    """A config layer could not be read or parsed."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def _expand(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, env) for item in value]
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: env.get(match.group(1), ""), value)
    return value


def expand_env(value: Any, env: Mapping[str, str] | None = None) -> Any:
    """Recursively replace ``${VAR}`` placeholders with environment values.

    Missing variables expand to an empty string so callers can validate the
    resulting model and report a precise configuration error.
    """
    return _expand(value, dict(os.environ) if env is None else env)


def load_json_file(path: str | Path) -> dict[str, Any]:
    """Read one JSON object, raising :class:`ConfigLayerError` on failure."""
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigLayerError(file_path, f"无法读取：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigLayerError(file_path, f"无法读取：JSON 非法：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigLayerError(file_path, "配置必须是 JSON 对象")
    return raw


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a minimal ``.env`` file (``KEY=VALUE`` / ``export KEY=VALUE``)."""
    values: dict[str, str] = {}
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
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


def load_env_file(path: str | Path) -> dict[str, str]:
    """Alias kept for readability at call sites."""
    return read_env_file(path)
