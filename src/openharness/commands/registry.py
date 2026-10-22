# ruff: noqa: F403, F405
"""Slash command registry."""

from __future__ import annotations

from typing import Iterable

from openharness.commands import (
    handlers_core,
    handlers_memory,
    handlers_plugins,
    handlers_settings,
    handlers_tasks,
    handlers_ui,
)
from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandRegistry,
    CommandResult,
    SlashCommand,
)
from openharness.commands.registry_helpers import *
from openharness.commands.registry_helpers import (
    _register_user_invocable_skill_commands,
    _render_plugin_command_prompt,
)
from openharness.plugins.types import PluginCommandDefinition


def create_default_command_registry(
    plugin_commands: Iterable[PluginCommandDefinition] | None = None,
) -> CommandRegistry:
    """Create the built-in command registry."""
    registry = CommandRegistry()

    async def _help_handler(_: str, context: CommandContext) -> CommandResult:
        del context
        return CommandResult(message=registry.help_text())

    registry.register(SlashCommand("help", "Show available commands", _help_handler))
    registry.register(
        SlashCommand("exit", "Exit OpenHarness", handlers_core._exit_handler, aliases=("quit",))
    )
    registry.register(SlashCommand("clear", "Clear conversation history", handlers_core._clear_handler))
    registry.register(SlashCommand("version", "Show the installed OpenHarness version", handlers_core._version_handler))
    registry.register(SlashCommand("status", "Show session status", handlers_core._status_handler))
    registry.register(SlashCommand("context", "Show the active runtime system prompt", handlers_core._context_handler))
    registry.register(
        SlashCommand(
            "summary",
            "Summarize conversation history",
            handlers_core._summary_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("compact", "Compact older conversation history", handlers_core._compact_handler))
    registry.register(SlashCommand("cost", "Show token usage and estimated cost", handlers_core._cost_handler))
    registry.register(SlashCommand("usage", "Show usage and token estimates", handlers_core._usage_handler))
    registry.register(SlashCommand("stats", "Show session statistics", handlers_core._stats_handler))
    registry.register(SlashCommand("dream", "Consolidate memory", handlers_core._dream_handler))
    registry.register(SlashCommand("memory", "Inspect and manage project memory", handlers_memory._memory_handler))
    registry.register(SlashCommand("hooks", "Show configured hooks", handlers_memory._hooks_handler))
    registry.register(
        SlashCommand(
            "resume",
            "Restore the latest saved session",
            handlers_memory._resume_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("session", "Inspect the current session storage", handlers_memory._session_handler))
    registry.register(SlashCommand("export", "Export the current transcript", handlers_memory._export_handler))
    registry.register(SlashCommand("share", "Create a shareable transcript snapshot", handlers_memory._share_handler))
    registry.register(SlashCommand("copy", "Copy the latest response or provided text", handlers_memory._copy_handler))
    registry.register(SlashCommand("tag", "Create a named snapshot of the current session", handlers_memory._tag_handler))
    registry.register(SlashCommand("rewind", "Remove the latest conversation turn(s)", handlers_memory._rewind_handler))
    registry.register(SlashCommand("files", "List files in the current workspace", handlers_memory._files_handler))
    registry.register(SlashCommand("init", "Initialize project OpenHarness files", handlers_plugins._init_handler))
    registry.register(
        SlashCommand(
            "bridge",
            "Inspect bridge helpers and spawn bridge sessions",
            handlers_plugins._bridge_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "login",
            "Show auth status or store an API key",
            handlers_plugins._login_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "logout",
            "Clear the stored API key",
            handlers_plugins._logout_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("feedback", "Save CLI feedback to the local feedback log", handlers_plugins._feedback_handler))
    registry.register(SlashCommand("onboarding", "Show the quickstart guide", handlers_settings._onboarding_handler))
    registry.register(SlashCommand("skills", "List or show available skills", handlers_plugins._skills_handler))
    _register_user_invocable_skill_commands(registry)
    registry.register(
        SlashCommand(
            "config",
            "Show or update configuration",
            handlers_plugins._config_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "mcp",
            "Show MCP status",
            handlers_settings._mcp_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "plugin",
            "Manage plugins",
            handlers_settings._plugin_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "reload-plugins",
            "Reload plugin discovery for this workspace",
            handlers_plugins._reload_plugins_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "permissions",
            "Show or update permission mode; Tab in the TUI opens the mode picker",
            handlers_settings._permissions_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "plan",
            "Toggle plan permission mode; /permissions default|full_auto exits plan explicitly",
            handlers_settings._plan_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("fast", "Show or update fast mode", handlers_settings._fast_handler))
    registry.register(SlashCommand("effort", "Show or update reasoning effort", handlers_settings._effort_handler))
    registry.register(SlashCommand("passes", "Show or update reasoning pass count", handlers_settings._passes_handler))
    registry.register(SlashCommand("turns", "Show or update maximum agentic turn count", handlers_settings._turns_handler))
    registry.register(SlashCommand("continue", "Continue the previous tool loop if it was interrupted", handlers_settings._continue_handler))
    registry.register(SlashCommand("stop", "Interrupt the running turn from TUI/ohmo channels", handlers_settings._stop_handler))
    registry.register(
        SlashCommand(
            "provider",
            "Show or switch provider profiles",
            handlers_ui._provider_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "model",
            "Show, switch, or manage profile models",
            handlers_settings._model_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("theme", "List, set, show or preview TUI themes", handlers_ui._theme_handler))
    registry.register(SlashCommand("output-style", "Show or update output style", handlers_ui._output_style_handler))
    registry.register(SlashCommand("keybindings", "Show resolved keybindings", handlers_ui._keybindings_handler))
    registry.register(SlashCommand("vim", "Show or update Vim mode", handlers_ui._vim_handler))
    registry.register(SlashCommand("voice", "Show or update voice mode", handlers_ui._voice_handler))
    registry.register(SlashCommand("doctor", "Show environment diagnostics", handlers_ui._doctor_handler))
    registry.register(
        SlashCommand(
            "diff",
            "Show git diff output",
            handlers_ui._diff_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("branch", "Show git branch information", handlers_tasks._branch_handler))
    registry.register(
        SlashCommand(
            "commit",
            "Show status or create a git commit",
            handlers_tasks._commit_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "issue",
            "Show or update project issue context",
            handlers_settings._issue_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "pr_comments",
            "Show or update project PR comments context",
            handlers_settings._pr_comments_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(SlashCommand("privacy-settings", "Show local privacy and storage settings", handlers_ui._privacy_settings_handler))
    registry.register(SlashCommand("rate-limit-options", "Show ways to reduce provider rate pressure", handlers_ui._rate_limit_options_handler))
    registry.register(SlashCommand("release-notes", "Show recent OpenHarness release notes", handlers_ui._release_notes_handler))
    registry.register(SlashCommand("upgrade", "Show upgrade instructions", handlers_ui._upgrade_handler))
    registry.register(SlashCommand("agents", "List or inspect agent and teammate tasks", handlers_memory._agents_handler))
    registry.register(SlashCommand("subagents", "Show subagent usage and inspect worker tasks", handlers_memory._agents_handler))
    registry.register(
        SlashCommand(
            "tasks",
            "Manage background tasks",
            handlers_tasks._tasks_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "autopilot",
            "Manage repo autopilot intake and context",
            handlers_tasks._autopilot_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )
    registry.register(
        SlashCommand(
            "ship",
            "Queue and execute an ohmo-driven repo task",
            handlers_tasks._ship_handler,
            remote_invocable=False,
            remote_admin_opt_in=True,
        )
    )

    for plugin_command in plugin_commands or ():
        if not plugin_command.user_invocable:
            continue

        async def _plugin_command_handler(
            args: str,
            context: CommandContext,
            *,
            command: PluginCommandDefinition = plugin_command,
        ) -> CommandResult:
            prompt = _render_plugin_command_prompt(
                command,
                args,
                getattr(context, "session_id", None),
            )
            if command.disable_model_invocation:
                return CommandResult(message=prompt)
            return CommandResult(
                submit_prompt=prompt,
                submit_model=command.model,
            )

        registry.register(
            SlashCommand(
                plugin_command.name,
                plugin_command.description,
                _plugin_command_handler,
            )
        )
    return registry

    return registry
