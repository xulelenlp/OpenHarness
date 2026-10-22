"""Config subcommands."""

from __future__ import annotations

import sys

import typer

from openharness.cli.app import config_app

# ---- config subcommands ----


def _config_resolve_target(settings: object, key: str) -> tuple[object, str]:
    target = settings
    parts = key.split(".")
    for part in parts[:-1]:
        if not hasattr(target, part):
            raise KeyError(key)
        target = getattr(target, part)
    leaf = parts[-1]
    if not hasattr(target, leaf):
        raise KeyError(key)
    return target, leaf


def _config_coerce_value(current: object, raw: str) -> object:
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid boolean value: {raw}")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [entry.strip() for entry in raw.split(",") if entry.strip()]
    return raw


@config_app.command("show")
def config_show() -> None:
    """Print the resolved settings JSON."""
    try:
        from openharness.commands.registry import _settings_json_for_display
    except ImportError:  # pragma: no cover - compatible with the registry split
        from openharness.commands.registry_helpers import _settings_json_for_display
    from openharness.config.settings import load_settings

    print(_settings_json_for_display(load_settings()), flush=True)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting key, including dotted nested keys"),
    value: str = typer.Argument(..., help="Value to store"),
) -> None:
    """Persist one setting in the effective settings file.

    Writes to the project's ``.openharness/settings.json`` when running inside a
    repo that has one, otherwise to ``~/.openharness/settings.json``.
    """
    from openharness.config.settings import load_settings, save_settings

    settings = load_settings()
    try:
        target, leaf = _config_resolve_target(settings, key)
    except KeyError:
        print(f"Unknown config key: {key}", file=sys.stderr)
        raise typer.Exit(1)
    try:
        coerced = _config_coerce_value(getattr(target, leaf), value)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    setattr(target, leaf, coerced)
    save_settings(settings)
    print(f"Updated {key}", flush=True)
