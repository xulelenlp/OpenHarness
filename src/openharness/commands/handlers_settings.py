# ruff: noqa: F403, F405
"""Built-in slash command handlers: settings."""

from __future__ import annotations

from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandResult,
)


async def _onboarding_handler(_: str, context: CommandContext) -> CommandResult:
    del context
    return CommandResult(
        message=(
            "OpenHarness quickstart:\n"
            "1. Ask for a coding task in plain language.\n"
            "2. Use /help to inspect commands.\n"
            "3. Use /doctor to inspect runtime state.\n"
            "4. Use /tasks for background work and /memory for project memory.\n"
            "5. Use /login to store an API key if needed."
        )
    )

async def _fast_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    current = (
        context.app_state.get().fast_mode
        if context.app_state is not None
        else settings.fast_mode
    )
    action = args.strip() or "show"
    if action == "show":
        return CommandResult(message=f"Fast mode: {'on' if current else 'off'}")
    enabled = {"on": True, "off": False, "toggle": not current}.get(action)
    if enabled is None:
        return CommandResult(message="Usage: /fast [show|on|off|toggle]")
    settings.fast_mode = enabled
    save_settings(settings)
    if context.app_state is not None:
        context.app_state.set(fast_mode=enabled)
    return CommandResult(message=f"Fast mode {'enabled' if enabled else 'disabled'}.")

async def _effort_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    current = context.app_state.get().effort if context.app_state is not None else settings.effort
    value = args.strip() or "show"
    if value == "show":
        return CommandResult(message=f"Reasoning effort: {current}")
    if value == "max":
        value = "xhigh"
    if value not in {"low", "medium", "high", "xhigh"}:
        return CommandResult(message="Usage: /effort [show|low|medium|high|xhigh]")
    settings.effort = value
    save_settings(settings)
    context.engine.set_effort(value)
    context.engine.set_system_prompt(
        build_runtime_system_prompt(
            settings,
            cwd=context.cwd,
            include_project_memory=context.include_project_memory,
        )
    )
    if context.app_state is not None:
        context.app_state.set(effort=value)
    return CommandResult(message=f"Reasoning effort set to {value}.")

async def _passes_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    current = context.app_state.get().passes if context.app_state is not None else settings.passes
    value = args.strip() or "show"
    if value == "show":
        return CommandResult(message=f"Passes: {current}")
    try:
        passes = max(1, min(int(value), 8))
    except ValueError:
        return CommandResult(message="Usage: /passes [show|COUNT]")
    settings.passes = passes
    save_settings(settings)
    context.engine.set_system_prompt(
        build_runtime_system_prompt(
            settings,
            cwd=context.cwd,
            include_project_memory=context.include_project_memory,
        )
    )
    if context.app_state is not None:
        context.app_state.set(passes=passes)
    return CommandResult(message=f"Pass count set to {passes}.")

async def _turns_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    engine_turns = "unlimited" if context.engine.max_turns is None else str(context.engine.max_turns)
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        return CommandResult(
            message=(
                f"Max turns (engine): {engine_turns}\n"
                f"Max turns (config): {settings.max_turns}\n"
                "Usage: /turns [show|unlimited|COUNT]"
            )
        )
    if tokens[0] == "set" and len(tokens) == 2:
        raw = tokens[1]
    elif len(tokens) == 1:
        raw = tokens[0]
    else:
        return CommandResult(message="Usage: /turns [show|unlimited|COUNT]")
    if raw.lower() == "unlimited":
        context.engine.set_max_turns(None)
        return CommandResult(
            message=(
                "Max turns set to unlimited for this session. "
                f"Saved config remains {settings.max_turns}."
            )
        )
    try:
        turns = int(raw)
    except ValueError:
        return CommandResult(message="Usage: /turns [show|unlimited|COUNT]")
    turns = max(1, min(turns, 512))
    settings.max_turns = turns
    save_settings(settings)
    context.engine.set_max_turns(turns)
    return CommandResult(message=f"Max turns set to {turns}.")

async def _continue_handler(args: str, context: CommandContext) -> CommandResult:
    raw = args.strip()
    if not context.engine.has_pending_continuation():
        return CommandResult(message="Nothing to continue (no pending tool results).")

    turns: int | None = None
    if raw:
        tokens = raw.split()
        if tokens[0] == "set" and len(tokens) == 2:
            raw = tokens[1]
        try:
            turns = int(raw)
        except ValueError:
            return CommandResult(message="Usage: /continue [COUNT]")
        turns = max(1, min(turns, 512))

    return CommandResult(
        message="Continuing pending tool loop...",
        continue_pending=True,
        continue_turns=turns,
    )

async def _stop_handler(_: str, _context: CommandContext) -> CommandResult:
    return CommandResult(
        message=(
            "No active turn is running in this command handler. "
            "While the TUI is running, type /stop or press Esc/Ctrl+C to interrupt the current turn. "
            "In ohmo remote channels, send /stop."
        )
    )

async def _issue_handler(args: str, context: CommandContext) -> CommandResult:
    path = get_project_issue_file(context.cwd)
    tokens = args.split(maxsplit=1)
    action = tokens[0] if tokens else "show"
    rest = tokens[1] if len(tokens) == 2 else ""
    if action == "show":
        if not path.exists():
            return CommandResult(message=f"No issue context. File path: {path}")
        return CommandResult(message=path.read_text(encoding="utf-8"))
    if action == "set" and rest:
        title, separator, body = rest.partition("::")
        if not separator or not title.strip() or not body.strip():
            return CommandResult(message="Usage: /issue set TITLE :: BODY")
        content = f"# {title.strip()}\n\n{body.strip()}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return CommandResult(message=f"Saved issue context to {path}")
    if action == "clear":
        if path.exists():
            path.unlink()
            return CommandResult(message="Cleared issue context.")
        return CommandResult(message="No issue context to clear.")
    return CommandResult(message="Usage: /issue [show|set TITLE :: BODY|clear]")

async def _pr_comments_handler(args: str, context: CommandContext) -> CommandResult:
    path = get_project_pr_comments_file(context.cwd)
    tokens = args.split(maxsplit=1)
    action = tokens[0] if tokens else "show"
    rest = tokens[1] if len(tokens) == 2 else ""
    if action == "show":
        if not path.exists():
            return CommandResult(message=f"No PR comments context. File path: {path}")
        return CommandResult(message=path.read_text(encoding="utf-8"))
    if action == "add" and rest:
        location, separator, comment = rest.partition("::")
        if not separator or not location.strip() or not comment.strip():
            return CommandResult(message="Usage: /pr_comments add FILE[:LINE] :: COMMENT")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# PR Comments\n"
        if not existing.endswith("\n"):
            existing += "\n"
        existing += f"- {location.strip()}: {comment.strip()}\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(existing, encoding="utf-8")
        return CommandResult(message=f"Added PR comment to {path}")
    if action == "clear":
        if path.exists():
            path.unlink()
            return CommandResult(message="Cleared PR comments context.")
        return CommandResult(message="No PR comments context to clear.")
    return CommandResult(message="Usage: /pr_comments [show|add FILE[:LINE] :: COMMENT|clear]")

async def _mcp_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    tokens = args.split()
    if tokens and tokens[0] == "auth" and len(tokens) >= 3:
        server_name = tokens[1]
        config = settings.mcp_servers.get(server_name)
        if config is None:
            return CommandResult(message=f"Unknown MCP server: {server_name}")

        if len(tokens) == 3:
            mode = "bearer"
            key = None
            value = tokens[2]
        elif len(tokens) == 4:
            mode = tokens[2]
            key = None
            value = tokens[3]
        elif len(tokens) == 5:
            mode = tokens[2]
            key = tokens[3]
            value = tokens[4]
        else:
            return CommandResult(
                message="Usage: /mcp auth SERVER TOKEN | /mcp auth SERVER [bearer|env] VALUE | /mcp auth SERVER header KEY VALUE"
            )

        if hasattr(config, "headers"):
            if mode not in {"bearer", "header"}:
                return CommandResult(message="HTTP/WS MCP auth supports bearer or header modes.")
            header_key = key or "Authorization"
            header_value = (
                f"Bearer {value}" if mode == "bearer" and header_key == "Authorization" else value
            )
            headers = dict(getattr(config, "headers", {}) or {})
            headers[header_key] = header_value
            settings.mcp_servers[server_name] = config.model_copy(update={"headers": headers})
        elif hasattr(config, "env"):
            if mode not in {"bearer", "env"}:
                return CommandResult(message="stdio MCP auth supports bearer or env modes.")
            env_key = key or "MCP_AUTH_TOKEN"
            env_value = f"Bearer {value}" if mode == "bearer" else value
            env = dict(getattr(config, "env", {}) or {})
            env[env_key] = env_value
            settings.mcp_servers[server_name] = config.model_copy(update={"env": env})
        else:
            return CommandResult(message=f"Server {server_name} does not support auth updates")
        save_settings(settings)
        return CommandResult(message=f"Saved MCP auth for {server_name}. Restart session to reconnect.")
    return CommandResult(message=context.mcp_summary or "No MCP servers configured.")

async def _plugin_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    tokens = args.split()
    if not tokens or tokens[0] == "list":
        return CommandResult(message=context.plugin_summary or "No plugins discovered.")
    if tokens[0] == "enable" and len(tokens) == 2:
        settings.enabled_plugins[tokens[1]] = True
        save_settings(settings)
        return CommandResult(message=f"Enabled plugin '{tokens[1]}'. Restart session to reload.")
    if tokens[0] == "disable" and len(tokens) == 2:
        settings.enabled_plugins[tokens[1]] = False
        save_settings(settings)
        return CommandResult(message=f"Disabled plugin '{tokens[1]}'. Restart session to reload.")
    if tokens[0] == "install" and len(tokens) == 2:
        path = install_plugin_from_path(tokens[1])
        return CommandResult(message=f"Installed plugin to {path}")
    if tokens[0] == "uninstall" and len(tokens) == 2:
        try:
            removed = uninstall_plugin(tokens[1])
        except ValueError:
            return CommandResult(message=f"Invalid plugin name '{tokens[1]}'")
        if removed:
            return CommandResult(message=f"Uninstalled plugin '{tokens[1]}'")
        return CommandResult(message=f"Plugin '{tokens[1]}' not found")
    plugins = load_plugins(settings, context.cwd, extra_roots=context.extra_plugin_roots)
    if plugins:
        return CommandResult(message=context.plugin_summary)
    return CommandResult(message="Usage: /plugin [list|enable NAME|disable NAME|install PATH|uninstall NAME]")

_MODE_LABELS = {"default": "Default", "plan": "Plan Mode", "full_auto": "Auto"}

async def _permissions_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        permission = settings.permission
        label = _MODE_LABELS.get(permission.mode.value, permission.mode.value)
        return CommandResult(
            message=(
                f"Mode: {label}\n"
                f"Allowed tools: {permission.allowed_tools}\n"
                f"Denied tools: {permission.denied_tools}"
            )
        )
    target_mode: str | None = None
    if tokens[0] == "set" and len(tokens) == 2:
        target_mode = tokens[1]
    elif len(tokens) == 1 and tokens[0] in _MODE_LABELS:
        target_mode = tokens[0]
    if target_mode is not None:
        settings.permission.mode = PermissionMode(target_mode)
        save_settings(settings)
        context.engine.set_permission_checker(PermissionChecker(settings.permission))
        if context.app_state is not None:
            context.app_state.set(permission_mode=settings.permission.mode.value)
        label = _MODE_LABELS.get(target_mode, target_mode)
        return CommandResult(message=f"Permission mode set to {label}", refresh_runtime=True)
    return CommandResult(message="Usage: /permissions [show|default|full_auto|plan]")

async def _plan_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    mode = args.strip() or "on"
    if mode in {"on", "enter"}:
        settings.permission.mode = PermissionMode.PLAN
        save_settings(settings)
        context.engine.set_permission_checker(PermissionChecker(settings.permission))
        if context.app_state is not None:
            context.app_state.set(permission_mode=settings.permission.mode.value)
        return CommandResult(message="Plan mode enabled.", refresh_runtime=True)
    if mode in {"off", "exit"}:
        settings.permission.mode = PermissionMode.DEFAULT
        save_settings(settings)
        context.engine.set_permission_checker(PermissionChecker(settings.permission))
        if context.app_state is not None:
            context.app_state.set(permission_mode=settings.permission.mode.value)
        return CommandResult(message="Plan mode disabled.", refresh_runtime=True)
    return CommandResult(message="Usage: /plan [on|off]")

def _dedupe_model_values(values: Iterable[str]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for value in values:
        model = value.strip()
        if not model or model in seen:
            continue
        models.append(model)
        seen.add(model)
    return models

def _seed_model_values(profile: "ProviderProfile") -> list[str]:
    existing = _dedupe_model_values(profile.allowed_models)
    if existing:
        return existing
    return _dedupe_model_values([display_model_setting(profile)])

def _format_model_status(active_profile: str, profile: "ProviderProfile") -> str:
    lines = [
        f"Model: {display_model_setting(profile)}",
        f"Profile: {active_profile}",
    ]
    if profile.allowed_models:
        lines.append("Available models:")
        lines.extend(f"- {model}" for model in profile.allowed_models)
    else:
        lines.append("Available models: unrestricted for this profile")
        lines.append("Use /model add MODEL to pin switchable models for the TUI selector.")
    return "\n".join(lines)

async def _model_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    manager = AuthManager(settings)
    active_profile = manager.get_active_profile()
    _, profile = settings.resolve_profile(active_profile)
    tokens = args.split(maxsplit=1)
    if not tokens or tokens[0] == "show":
        return CommandResult(message=_format_model_status(active_profile, profile))
    if tokens[0] == "list":
        if profile.allowed_models:
            return CommandResult(
                message=(
                    f"Switchable models for profile '{active_profile}':\n"
                    + "\n".join(f"- {model}" for model in profile.allowed_models)
                )
            )
        return CommandResult(
            message=(
                f"Profile '{active_profile}' has no pinned model list. "
                "Any model value is accepted. Use /model add MODEL to add one."
            )
        )
    if tokens[0] == "add" and len(tokens) == 2:
        model_name = tokens[1].strip()
        models = _dedupe_model_values([*_seed_model_values(profile), model_name])
        if not model_name:
            return CommandResult(message="Usage: /model add MODEL")
        manager.update_profile(active_profile, allowed_models=models)
        return CommandResult(
            message=f"Added model '{model_name}' to profile '{active_profile}'.",
            refresh_runtime=True,
        )
    if tokens[0] == "add":
        return CommandResult(message="Usage: /model add MODEL")
    if tokens[0] in {"remove", "rm"} and len(tokens) == 2:
        model_name = tokens[1].strip()
        models = [model for model in _dedupe_model_values(profile.allowed_models) if model != model_name]
        if len(models) == len(_dedupe_model_values(profile.allowed_models)):
            return CommandResult(message=f"Model '{model_name}' is not pinned for profile '{active_profile}'.")
        reset_current = (profile.last_model or "").strip() == model_name
        manager.update_profile(
            active_profile,
            allowed_models=models,
            last_model="" if reset_current else None,
        )
        updated = load_settings()
        if reset_current:
            context.engine.set_model(updated.model)
            if context.app_state is not None:
                updated_profile = updated.resolve_profile()[1]
                context.app_state.set(model=display_model_setting(updated_profile))
        return CommandResult(
            message=f"Removed model '{model_name}' from profile '{active_profile}'.",
            refresh_runtime=True,
        )
    if tokens[0] in {"remove", "rm"}:
        return CommandResult(message="Usage: /model remove MODEL")
    if tokens[0] == "clear":
        manager.update_profile(active_profile, allowed_models=[])
        return CommandResult(
            message=f"Cleared pinned models for profile '{active_profile}'. Any model value is now accepted.",
            refresh_runtime=True,
        )
    if tokens[0] == "set" and len(tokens) == 2:
        model_name = tokens[1].strip()
    elif args.strip():
        model_name = args.strip()
    else:
        model_name = None
    if model_name:
        if profile.allowed_models and model_name.lower() != "default" and model_name not in profile.allowed_models:
            allowed = ", ".join(profile.allowed_models)
            return CommandResult(message=f"Model '{model_name}' is not allowed for profile '{active_profile}'. Allowed models: {allowed}")
        if model_name.lower() == "default":
            manager.update_profile(active_profile, last_model="")
            message = "Model reset to default."
        else:
            manager.update_profile(active_profile, last_model=model_name)
            message = f"Model set to {model_name}."
        updated = load_settings()
        context.engine.set_model(updated.model)
        if context.app_state is not None:
            updated_profile = updated.resolve_profile()[1]
            context.app_state.set(model=display_model_setting(updated_profile))
        return CommandResult(message=message, refresh_runtime=True)
    return CommandResult(message="Usage: /model [show|list|add MODEL|remove MODEL|clear|MODEL]")
