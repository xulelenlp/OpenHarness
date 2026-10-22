#!/usr/bin/env python3
"""把旧的 ``config/backend/settings.json`` 迁移到 ``config/backend/.env``。

背景：OpenHarness 的模型 / 凭据 / 品牌 / 运行参数已统一收敛到 ``.env``，
仓库不再提供 ``settings.json``。旧部署升级时用本脚本一次性搬运：

    python scripts/migrate_settings_to_env.py --dry-run   # 先看会改什么
    python scripts/migrate_settings_to_env.py             # 实际写入（自动备份 .env）

规则：
- 只搬运 .env 里仍为空/缺失的键；已有值默认不动（``--force`` 覆盖）。
- 密钥不会打印；密钥类字段（profile.api_key、误填进 auth_source 的 key）
  会写入 ``OPENHARNESS_API_KEY``，而不是留在 JSON 里。
- ``settings.json`` 不会被删除，确认迁移成功后可自行删除。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

MODEL_KEYS = (
    "OPENHARNESS_PROFILE",
    "OPENHARNESS_PROVIDER",
    "OPENHARNESS_API_FORMAT",
    "OPENHARNESS_MODEL",
    "OPENHARNESS_BASE_URL",
    "OPENHARNESS_AUTH_SOURCE",
)

_EXTRA_SECTION = "\n# ---- 迁移自 settings.json ----\n"


def _looks_like_key(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    return text.startswith(("sk-", "sk_", "Bearer ", "ghp_", "xoxb-")) or (
        "_" not in text and len(text) >= 32
    )


def _default_auth_source(provider: str, api_format: str) -> str:
    if provider == "anthropic":
        return "anthropic_api_key"
    if provider == "dashscope":
        return "dashscope_api_key"
    if provider == "moonshot":
        return "moonshot_api_key"
    if provider == "gemini":
        return "gemini_api_key"
    if api_format == "anthropic":
        return "anthropic_api_key"
    return "openai_api_key"


def _profile_from_settings(raw: dict) -> tuple[str, dict]:
    profiles = raw.get("profiles") or {}
    name = str(raw.get("active_profile") or "").strip()
    profile = profiles.get(name) if name else None
    if not isinstance(profile, dict):
        # 没有 profiles 时用顶层扁平字段构造一份
        name = name or "default"
        profile = {
            "provider": raw.get("provider") or "openai",
            "api_format": raw.get("api_format") or "openai",
            "auth_source": "",
            "default_model": raw.get("model") or "",
            "base_url": raw.get("base_url") or "",
        }
    return name, profile


def _read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def _plan(raw: dict, env_values: dict[str, str], *, force: bool) -> dict[str, str]:
    name, profile = _profile_from_settings(raw)
    provider = str(profile.get("provider") or "").strip()
    api_format = str(profile.get("api_format") or "").strip() or "openai"
    auth_source = str(profile.get("auth_source") or "").strip()
    api_key = str(profile.get("api_key") or "").strip()

    # 兼容历史上把密钥误填进 auth_source 的配置：迁移成 OPENHARNESS_API_KEY。
    if _looks_like_key(auth_source):
        api_key = api_key or auth_source
        auth_source = _default_auth_source(provider, api_format)
    if not auth_source:
        auth_source = _default_auth_source(provider, api_format)

    desired = {
        "OPENHARNESS_PROFILE": name,
        "OPENHARNESS_PROVIDER": provider or "openai",
        "OPENHARNESS_API_FORMAT": api_format,
        "OPENHARNESS_MODEL": str(profile.get("default_model") or raw.get("model") or "").strip(),
        "OPENHARNESS_BASE_URL": str(profile.get("base_url") or raw.get("base_url") or "").strip(),
        "OPENHARNESS_AUTH_SOURCE": auth_source,
    }
    if api_key:
        desired["OPENHARNESS_API_KEY"] = api_key

    branding = raw.get("branding") or {}
    if isinstance(branding, dict):
        if branding.get("product_name"):
            desired["OPENHARNESS_PRODUCT_NAME"] = str(branding["product_name"]).strip()
        if branding.get("slogan"):
            desired["OPENHARNESS_SLOGAN"] = str(branding["slogan"]).strip()

    for src_key, env_key in (
        ("max_tokens", "OPENHARNESS_MAX_TOKENS"),
        ("timeout", "OPENHARNESS_TIMEOUT"),
        ("max_turns", "OPENHARNESS_MAX_TURNS"),
    ):
        if raw.get(src_key) is not None:
            desired[env_key] = str(raw[src_key])

    if raw.get("allow_project_plugins") is not None:
        desired["OPENHARNESS_ALLOW_PROJECT_PLUGINS"] = (
            "true" if raw["allow_project_plugins"] else "false"
        )
    skill_dirs = raw.get("project_skill_dirs")
    if isinstance(skill_dirs, list) and skill_dirs:
        desired["OPENHARNESS_PROJECT_SKILL_DIRS"] = ",".join(str(item) for item in skill_dirs)

    return {
        key: value
        for key, value in desired.items()
        if value and (force or not env_values.get(key, "").strip())
    }


def _write_env(path: Path, lines: list[str], updates: dict[str, str]) -> None:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)

    missing = {key: value for key, value in updates.items() if key not in seen}
    if missing:
        if out and out[-1].strip():
            out.append("")
        out.append(_EXTRA_SECTION.strip())
        for key, value in missing.items():
            out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 settings.json 迁移到 .env")
    parser.add_argument(
        "--settings", default="config/backend/settings.json", help="旧 settings.json 路径"
    )
    parser.add_argument("--env", default="config/backend/.env", help="目标 .env 路径")
    parser.add_argument("--force", action="store_true", help="覆盖 .env 里已有的非空值")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要写入的键，不改文件")
    args = parser.parse_args(argv)

    settings_path = Path(args.settings).expanduser()
    env_path = Path(args.env).expanduser()
    if not settings_path.is_file():
        print(f"未找到 {settings_path}，无需迁移。")
        return 0

    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"读取 {settings_path} 失败：{exc}", file=sys.stderr)
        return 1

    lines, values = _read_env(env_path)
    updates = _plan(raw, values, force=args.force)
    if not updates:
        print(".env 已是迁移后的状态，无需修改。")
        return 0

    print(f"将写入 {env_path}：")
    for key in updates:
        is_secret = key.endswith(("_KEY", "_TOKENS_KEY", "_PASSWORD", "_TOKEN"))
        shown = "<已隐藏>" if is_secret else updates[key]
        print(f"  {key}={shown}")

    if args.dry_run:
        print("\n--dry-run：未修改任何文件。")
        return 0

    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.is_file():
        backup = env_path.with_name(
            f"{env_path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(env_path, backup)
        print(f"\n已备份：{backup}")
    _write_env(env_path, lines, updates)
    print(f"已更新：{env_path}")
    print("提示：确认服务正常后，可删除 settings.json；高级配置仍可放 ~/.openharness/settings.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
