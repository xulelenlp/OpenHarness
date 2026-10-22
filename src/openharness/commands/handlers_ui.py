# ruff: noqa: F403, F405
"""Built-in slash command handlers: ui."""

from __future__ import annotations

from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandResult,
)
from openharness.commands.registry_helpers import (
    _run_git_command,
)


async def _provider_handler(args: str, context: CommandContext) -> CommandResult:
    manager = AuthManager()
    profiles = manager.get_profile_statuses()
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        active_name = manager.get_active_profile()
        active = profiles[active_name]
        lines = [
            f"Active profile: {active_name}",
            f"Label: {active['label']}",
            f"Provider: {active['provider']}",
            f"Auth source: {active['auth_source']}",
            f"Configured: {'yes' if active['configured'] else 'no'}",
            f"Base URL: {active['base_url'] or '(default)'}",
            f"Model: {active['model']}",
        ]
        return CommandResult(message="\n".join(lines))
    if tokens[0] == "list":
        lines = ["Provider profiles:"]
        for name, info in profiles.items():
            marker = "*" if info["active"] else " "
            configured = "configured" if info["configured"] else "missing auth"
            lines.append(f"{marker} {name} [{configured}] {info['label']} -> {info['model']}")
        return CommandResult(message="\n".join(lines))
    target = tokens[1] if tokens[0] == "use" and len(tokens) == 2 else (tokens[0] if len(tokens) == 1 else None)
    if target is None:
        return CommandResult(message="Usage: /provider [show|list|PROFILE]")
    manager.use_profile(target)
    updated = load_settings()
    profile = updated.resolve_profile()[1]
    context.engine.set_model(updated.model)
    if context.app_state is not None:
        context.app_state.set(
            model=display_model_setting(profile),
            provider=detect_provider(updated).name,
            auth_status=auth_status(updated),
            base_url=updated.base_url or "",
        )
    return CommandResult(
        message=f"Switched provider profile to {target} ({profile.label}).",
        refresh_runtime=True,
    )

async def _theme_handler(args: str, context: CommandContext) -> CommandResult:
    from openharness.themes import list_themes, load_theme

    settings = load_settings()
    tokens = args.split(maxsplit=1)
    current = (
        context.app_state.get().theme
        if context.app_state is not None and hasattr(context.app_state.get(), "theme")
        else settings.theme
    )

    if not tokens or tokens[0] == "show":
        try:
            theme = load_theme(current)
            lines = [
                f"Theme: {theme.name}",
                f"  Colors:  primary={theme.colors.primary}  secondary={theme.colors.secondary}"
                f"  accent={theme.colors.accent}  error={theme.colors.error}"
                f"  muted={theme.colors.muted}",
                f"           background={theme.colors.background}  foreground={theme.colors.foreground}",
                f"  Borders: style={theme.borders.style}",
                f"  Icons:   spinner={theme.icons.spinner}  tool={theme.icons.tool}"
                f"  error={theme.icons.error}  success={theme.icons.success}"
                f"  agent={theme.icons.agent}",
                f"  Layout:  compact={theme.layout.compact}"
                f"  show_tokens={theme.layout.show_tokens}"
                f"  show_time={theme.layout.show_time}",
            ]
            return CommandResult(message="\n".join(lines))
        except KeyError:
            return CommandResult(message=f"Theme: {current} (not found)")

    if tokens[0] == "list":
        available = list_themes()
        lines = [f"{'*' if name == current else ' '} {name}" for name in available]
        return CommandResult(message="\n".join(lines))

    if tokens[0] == "set" and len(tokens) == 2:
        name = tokens[1]
    elif len(tokens) == 1 and tokens[0] not in {"list", "preview"}:
        name = tokens[0]
    else:
        name = None
    if name is not None:
        try:
            load_theme(name)
        except KeyError:
            available = list_themes()
            return CommandResult(
                message=f"Unknown theme: {name!r}. Available: {', '.join(available)}"
            )
        settings.theme = name
        save_settings(settings)
        if context.app_state is not None:
            context.app_state.set(theme=name)
        return CommandResult(message=f"Theme set to {name}")

    if tokens[0] == "preview" and len(tokens) == 2:
        name = tokens[1]
        try:
            theme = load_theme(name)
        except KeyError:
            available = list_themes()
            return CommandResult(
                message=f"Unknown theme: {name!r}. Available: {', '.join(available)}"
            )
        lines = [
            f"Preview: {theme.name}",
            f"  primary    {theme.colors.primary}",
            f"  secondary  {theme.colors.secondary}",
            f"  accent     {theme.colors.accent}",
            f"  error      {theme.colors.error}",
            f"  muted      {theme.colors.muted}",
            f"  background {theme.colors.background}",
            f"  foreground {theme.colors.foreground}",
            f"  borders    {theme.borders.style}",
            f"  icons      spinner={theme.icons.spinner} tool={theme.icons.tool}"
            f" success={theme.icons.success} error={theme.icons.error}"
            f" agent={theme.icons.agent}",
        ]
        return CommandResult(message="\n".join(lines))

    return CommandResult(message="Usage: /theme [list|show|NAME|preview NAME]")

async def _output_style_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    tokens = args.split(maxsplit=1)
    styles = load_output_styles()
    available = {style.name: style for style in styles}
    current = (
        context.app_state.get().output_style
        if context.app_state is not None
        else settings.output_style
    )
    if not tokens or tokens[0] == "show":
        return CommandResult(message=f"Output style: {current}")
    if tokens[0] == "list":
        return CommandResult(
            message="\n".join(f"{style.name} [{style.source}]" for style in styles)
        )
    if tokens[0] == "set" and len(tokens) == 2:
        style_name = tokens[1]
    elif len(tokens) == 1 and tokens[0] not in {"list"}:
        style_name = tokens[0]
    else:
        style_name = None
    if style_name is not None:
        if style_name not in available:
            return CommandResult(message=f"Unknown output style: {style_name}")
        settings.output_style = style_name
        save_settings(settings)
        if context.app_state is not None:
            context.app_state.set(output_style=style_name)
        return CommandResult(message=f"Output style set to {style_name}")
    return CommandResult(message="Usage: /output-style [show|list|NAME]")

async def _keybindings_handler(_: str, context: CommandContext) -> CommandResult:
    from openharness.keybindings import get_keybindings_path, load_keybindings

    bindings = (
        context.app_state.get().keybindings
        if context.app_state is not None and context.app_state.get().keybindings
        else load_keybindings()
    )
    lines = [f"Keybindings file: {get_keybindings_path()}"]
    lines.extend(f"{key} -> {command}" for key, command in sorted(bindings.items()))
    return CommandResult(message="\n".join(lines))

async def _vim_handler(args: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    current = (
        context.app_state.get().vim_enabled
        if context.app_state is not None
        else settings.vim_mode
    )
    action = args.strip() or "show"
    if action == "show":
        return CommandResult(message=f"Vim mode: {'on' if current else 'off'}")
    enabled = {"on": True, "off": False, "toggle": not current}.get(action)
    if enabled is None:
        return CommandResult(message="Usage: /vim [show|on|off|toggle]")
    settings.vim_mode = enabled
    save_settings(settings)
    if context.app_state is not None:
        context.app_state.set(vim_enabled=enabled)
    return CommandResult(message=f"Vim mode {'enabled' if enabled else 'disabled'}.")

async def _voice_handler(args: str, context: CommandContext) -> CommandResult:
    from openharness.voice import extract_keyterms, inspect_voice_capabilities

    settings = load_settings()
    diagnostics = inspect_voice_capabilities(detect_provider(settings))
    current = (
        context.app_state.get().voice_enabled
        if context.app_state is not None
        else settings.voice_mode
    )
    tokens = args.split(maxsplit=1)
    if not tokens or tokens[0] == "show":
        return CommandResult(
            message=(
                f"Voice mode: {'on' if current else 'off'}\n"
                f"Available: {'yes' if diagnostics.available else 'no'}\n"
                f"Recorder: {diagnostics.recorder or '(none)'}\n"
                f"Reason: {diagnostics.reason}"
            )
        )
    if tokens[0] == "keyterms" and len(tokens) == 2:
        keyterms = extract_keyterms(tokens[1])
        return CommandResult(message="\n".join(keyterms) if keyterms else "(no keyterms)")
    enabled = {"on": True, "off": False, "toggle": not current}.get(tokens[0])
    if enabled is None:
        return CommandResult(message="Usage: /voice [show|on|off|toggle|keyterms TEXT]")
    settings.voice_mode = enabled
    save_settings(settings)
    if context.app_state is not None:
        context.app_state.set(
            voice_enabled=enabled,
            voice_available=diagnostics.available,
            voice_reason=diagnostics.reason,
        )
    return CommandResult(message=f"Voice mode {'enabled' if enabled else 'disabled'}.")

async def _doctor_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    manager = AuthManager(settings)
    active_profile_name, active_profile = settings.resolve_profile()
    memory_dir = get_project_memory_dir(context.cwd)
    state = context.app_state.get() if context.app_state is not None else None
    lines = [
        "Doctor summary:",
        f"- cwd: {context.cwd}",
        f"- active_profile: {active_profile_name}",
        f"- model: {settings.model}",
        f"- provider_workflow: {active_profile.label}",
        f"- auth_source: {active_profile.auth_source}",
        f"- permission_mode: {state.permission_mode if state is not None else settings.permission.mode}",
        f"- theme: {state.theme if state is not None else settings.theme}",
        f"- output_style: {state.output_style if state is not None else settings.output_style}",
        f"- vim_mode: {'on' if (state.vim_enabled if state is not None else settings.vim_mode) else 'off'}",
        f"- voice_mode: {'on' if (state.voice_enabled if state is not None else settings.voice_mode) else 'off'}",
        f"- effort: {state.effort if state is not None else settings.effort}",
        f"- passes: {state.passes if state is not None else settings.passes}",
        f"- memory_dir: {memory_dir}",
        f"- plugin_count: {max(len(context.plugin_summary.splitlines()) - 1, 0) if context.plugin_summary else 0}",
        f"- mcp_configured: {'yes' if context.mcp_summary and 'No MCP' not in context.mcp_summary else 'no'}",
        f"- auth_configured: {'yes' if manager.get_profile_statuses()[active_profile_name]['configured'] else 'no'}",
    ]
    return CommandResult(message="\n".join(lines))

async def _privacy_settings_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    session_dir = context.session_backend.get_session_dir(context.cwd)
    lines = [
        "Privacy settings:",
        f"- user_config_dir: {get_config_dir()}",
        f"- project_config_dir: {get_project_config_dir(context.cwd)}",
        f"- session_dir: {session_dir}",
        f"- feedback_log: {get_feedback_log_path()}",
        f"- api_base_url: {settings.base_url or '(default Anthropic-compatible endpoint)'}",
        "- network: enabled only for provider and explicit web/MCP calls",
        "- storage: local files under ~/.openharness and project .openharness",
    ]
    return CommandResult(message="\n".join(lines))

async def _rate_limit_options_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    provider = "moonshot-compatible" if (settings.base_url and "moonshot" in settings.base_url) else "anthropic-compatible"
    lines = [
        "Rate limit options:",
        f"- provider: {provider}",
        "- reduce /passes or switch /effort low for lighter requests",
        "- enable /fast for shorter responses and less tool churn",
        "- use /compact to shrink long transcripts before retrying",
        "- prefer background /tasks for long-running local work",
    ]
    return CommandResult(message="\n".join(lines))

async def _release_notes_handler(_: str, context: CommandContext) -> CommandResult:
    path = Path(context.cwd) / "RELEASE_NOTES.md"
    if path.exists():
        return CommandResult(message=path.read_text(encoding="utf-8"))
    return CommandResult(
        message=(
            "# Release Notes\n\n"
            "- React TUI is now the default `abc` interface.\n"
            "- Added richer session, files, bridge, agent, copy, rewind, effort, passes, and privacy commands.\n"
            "- Expanded real-model validation across tools, MCP, tasks, plugins, notebook, LSP, cron, and worktree flows.\n"
        )
    )

async def _upgrade_handler(_: str, context: CommandContext) -> CommandResult:
    del context
    try:
        version = importlib.metadata.version("openharness")
    except importlib.metadata.PackageNotFoundError:
        version = "0.1.7"
    return CommandResult(
        message=(
            f"Current version: {version}\n"
            "Upgrade instructions:\n"
            "- uv sync --extra dev\n"
            "- uv pip install -e .\n"
            "- npm --prefix frontend/terminal install"
        )
    )

async def _diff_handler(args: str, context: CommandContext) -> CommandResult:
    if args.strip() == "full":
        ok, output = _run_git_command(context.cwd, "diff", "HEAD")
        return CommandResult(message=output or "(no diff)")
    ok, output = _run_git_command(context.cwd, "diff", "--stat")
    if not ok:
        return CommandResult(message=output)
    return CommandResult(message=output or "(no diff)")
