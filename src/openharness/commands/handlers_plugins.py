# ruff: noqa: F403, F405
"""Built-in slash command handlers: plugins."""

from __future__ import annotations

from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandResult,
)
from openharness.commands.registry_helpers import (
    _coerce_setting_value,
    _is_valid_skill_command_name,
    _settings_json_for_display,
    _skill_command_name,
)


async def _init_handler(args: str, context: CommandContext) -> CommandResult:
    del args
    project_dir = get_project_config_dir(context.cwd)
    created: list[str] = []

    claudemd = Path(context.cwd) / "CLAUDE.md"
    if not claudemd.exists():
        claudemd.write_text(
            "# Project Instructions\n\n"
            "- Use OpenHarness tools deliberately.\n"
            "- Keep changes minimal and verify with tests when possible.\n",
            encoding="utf-8",
        )
        created.append(str(claudemd.relative_to(Path(context.cwd))))

    for relative, content in (
        (
            project_dir / "README.md",
            "# Project OpenHarness Config\n\nThis directory stores project-specific OpenHarness state.\n",
        ),
        (
            project_dir / "memory" / "MEMORY.md",
            "# Project Memory\n\nAdd reusable project knowledge here.\n",
        ),
        (
            project_dir / "plugins" / ".gitkeep",
            "",
        ),
        (
            project_dir / "skills" / ".gitkeep",
            "",
        ),
    ):
        relative.parent.mkdir(parents=True, exist_ok=True)
        if not relative.exists():
            relative.write_text(content, encoding="utf-8")
            created.append(str(relative.relative_to(Path(context.cwd))))

    if not created:
        return CommandResult(message="Project already initialized for OpenHarness.")
    return CommandResult(message="Initialized project files:\n" + "\n".join(f"- {item}" for item in created))

async def _bridge_handler(args: str, context: CommandContext) -> CommandResult:
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        sessions = get_bridge_manager().list_sessions()
        lines = [
            "Bridge summary:",
            "- backend host: available",
            f"- cwd: {context.cwd}",
            f"- sessions: {len(sessions)}",
            "- utilities: encode, decode, sdk, spawn, list, output, stop",
        ]
        return CommandResult(message="\n".join(lines))
    if tokens[0] == "encode" and len(tokens) == 3:
        encoded = encode_work_secret(
            WorkSecret(version=1, session_ingress_token=tokens[2], api_base_url=tokens[1])
        )
        return CommandResult(message=encoded)
    if tokens[0] == "decode" and len(tokens) == 2:
        secret = decode_work_secret(tokens[1])
        return CommandResult(message=json.dumps(secret.__dict__, indent=2))
    if tokens[0] == "sdk" and len(tokens) == 3:
        return CommandResult(message=build_sdk_url(tokens[1], tokens[2]))
    if tokens[0] == "spawn" and len(tokens) >= 2:
        command = args[len("spawn ") :]
        handle = await get_bridge_manager().spawn(
            session_id=f"bridge-{datetime.now(timezone.utc).strftime('%H%M%S')}",
            command=command,
            cwd=context.cwd,
        )
        return CommandResult(
            message=f"Spawned bridge session {handle.session_id} pid={handle.process.pid}"
        )
    if tokens[0] == "list":
        sessions = get_bridge_manager().list_sessions()
        if not sessions:
            return CommandResult(message="No bridge sessions.")
        return CommandResult(
            message="\n".join(
                f"{item.session_id} [{item.status}] pid={item.pid} {item.command}"
                for item in sessions
            )
        )
    if tokens[0] == "output" and len(tokens) == 2:
        return CommandResult(message=get_bridge_manager().read_output(tokens[1]) or "(no output)")
    if tokens[0] == "stop" and len(tokens) == 2:
        try:
            await get_bridge_manager().stop(tokens[1])
        except ValueError as exc:
            return CommandResult(message=str(exc))
        return CommandResult(message=f"Stopped bridge session {tokens[1]}")
    return CommandResult(
        message="Usage: /bridge [show|encode API_BASE_URL TOKEN|decode SECRET|sdk API_BASE_URL SESSION_ID|spawn CMD|list|output SESSION_ID|stop SESSION_ID]"
    )

async def _reload_plugins_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    plugins = load_plugins(settings, context.cwd, extra_roots=context.extra_plugin_roots)
    if not plugins:
        return CommandResult(message="No plugins discovered.")
    lines = ["Reloaded plugins:"]
    for plugin in plugins:
        state = "enabled" if plugin.enabled else "disabled"
        lines.append(f"- {plugin.manifest.name} [{state}]")
    return CommandResult(message="\n".join(lines))

async def _skills_handler(args: str, context: CommandContext) -> CommandResult:
    skill_registry = load_skill_registry(
        context.cwd,
        extra_skill_dirs=context.extra_skill_dirs,
        extra_plugin_roots=context.extra_plugin_roots,
    )
    if args:
        skill = skill_registry.get(args)
        if skill is None:
            return CommandResult(message=f"Skill not found: {args}")
        return CommandResult(message=skill.content)
    skills = skill_registry.list_skills()
    if not skills:
        return CommandResult(message="No skills available.")
    lines = ["Available skills:"]
    for skill in skills:
        source = f" [{skill.source}]"
        path = f" {skill.path}" if skill.path else ""
        command_name = _skill_command_name(skill)
        slash = f" /{command_name}" if skill.user_invocable and _is_valid_skill_command_name(command_name) else ""
        display = f" ({skill.display_name})" if skill.display_name else ""
        lines.append(f"- {command_name}{display}{source}{path}{slash}: {skill.description}")
    return CommandResult(message="\n".join(lines))

async def _config_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    from openharness.commands import registry as registry_module

    settings = registry_module.load_settings()
    tokens = args.split(maxsplit=2)
    if not tokens or tokens[0] == "show":
        return CommandResult(message=_settings_json_for_display(settings))
    if tokens[0] == "set" and len(tokens) == 3:
        key, value = tokens[1], tokens[2]
        if key not in Settings.model_fields:
            return CommandResult(message=f"Unknown config key: {key}")
        try:
            coerced = _coerce_setting_value(settings, key, value)
        except ValueError as exc:
            return CommandResult(message=str(exc))
        setattr(settings, key, coerced)
        save_settings(settings)
        return CommandResult(message=f"Updated {key}")
    return CommandResult(message="Usage: /config [show|set KEY VALUE]")

async def _login_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    settings = load_settings()
    manager = AuthManager(settings)
    profile_name, profile = settings.resolve_profile()
    provider = detect_provider(settings)
    api_key = args.strip()
    if not api_key:
        masked = (
            f"{settings.api_key[:6]}...{settings.api_key[-4:]}"
            if settings.api_key
            else "(not configured)"
        )
        return CommandResult(
            message=(
                f"Auth status:\n"
                f"- profile: {profile_name}\n"
                f"- provider: {provider.name}\n"
                f"- auth_source: {profile.auth_source}\n"
                f"- auth_status: {auth_status(settings)}\n"
                f"- base_url: {settings.base_url or '(default)'}\n"
                f"- model: {settings.model}\n"
                f"- api_key: {masked}\n"
                "Usage: /login API_KEY"
            )
        )
    manager.store_profile_credential(profile_name, "api_key", api_key)
    return CommandResult(message=f"Stored API key in {get_config_file_path()}")

async def _logout_handler(_: str, context: CommandContext) -> CommandResult:
    del context
    settings = load_settings()
    profile_name = settings.resolve_profile()[0]
    AuthManager(settings).clear_profile_credential(profile_name)
    return CommandResult(message="Cleared stored API key.")

async def _feedback_handler(args: str, context: CommandContext) -> CommandResult:
    del context
    path = get_feedback_log_path()
    if not args.strip():
        return CommandResult(message=f"Feedback log: {path}\nUsage: /feedback TEXT")
    timestamp = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {args.strip()}\n")
    return CommandResult(message=f"Saved feedback to {path}")
