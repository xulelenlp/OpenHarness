"""Slash command registry base types."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from openharness.services.session_backend import DEFAULT_SESSION_BACKEND, SessionBackend

if TYPE_CHECKING:
    from openharness.engine.query_engine import QueryEngine
    from openharness.state import AppStateStore
    from openharness.tools.base import ToolRegistry

@dataclass
class CommandResult:
    """Result returned by a slash command."""

    message: str | None = None
    should_exit: bool = False
    clear_screen: bool = False
    replay_messages: list | None = None  # ConversationMessage list to replay in TUI
    continue_pending: bool = False
    continue_turns: int | None = None
    refresh_runtime: bool = False
    submit_prompt: str | None = None
    submit_model: str | None = None


@dataclass(frozen=True)
class MemoryCommandBackend:
    """Storage backend used by the generic ``/memory`` slash command."""

    label: str
    default_type: str
    default_category: str
    get_memory_dir: Callable[[], Path]
    get_entrypoint: Callable[[], Path]
    list_files: Callable[[], list[Path]]
    add_entry: Callable[[str, str], Path]
    remove_entry: Callable[[str], bool]


@dataclass
class CommandContext:
    """Context available to command handlers."""

    engine: QueryEngine
    hooks_summary: str = ""
    mcp_summary: str = ""
    plugin_summary: str = ""
    cwd: str = "."
    tool_registry: ToolRegistry | None = None
    app_state: AppStateStore | None = None
    session_backend: SessionBackend = DEFAULT_SESSION_BACKEND
    session_id: str | None = None
    extra_skill_dirs: Iterable[str | Path] | None = None
    extra_plugin_roots: Iterable[str | Path] | None = None
    memory_backend: MemoryCommandBackend | None = None
    include_project_memory: bool = True


CommandHandler = Callable[[str, CommandContext], Awaitable[CommandResult]]


@dataclass
class SlashCommand:
    """Definition of a slash command."""

    name: str
    description: str
    handler: CommandHandler
    remote_invocable: bool = True
    remote_admin_opt_in: bool = False
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    """Map slash commands to handlers."""

    def __init__(self) -> None:
        # Primary commands keyed by canonical name, plus aliases pointing at
        # the same SlashCommand instance. We keep a separate set of canonical
        # names so help/listing output doesn't duplicate aliased entries.
        self._commands: dict[str, SlashCommand] = {}
        self._canonical_names: list[str] = []

    def register(self, command: SlashCommand) -> None:
        """Register a command, plus any aliases pointing at the same handler."""
        if command.name not in self._commands:
            self._canonical_names.append(command.name)
        self._commands[command.name] = command
        for alias in command.aliases:
            self._commands[alias] = command

    def lookup(self, raw_input: str) -> tuple[SlashCommand, str] | None:
        """Parse a slash command and return its handler plus raw args."""
        if not raw_input.startswith("/"):
            return None
        name, _, args = raw_input[1:].partition(" ")
        command = self._commands.get(name)
        if command is None:
            return None
        return command, args.strip()

    def help_text(self) -> str:
        """Return a formatted summary of all registered commands."""
        lines = ["Available commands:"]
        commands = [self._commands[name] for name in self._canonical_names]
        for command in sorted(commands, key=lambda item: item.name):
            lines.append(f"/{command.name:<12} {command.description}")
        return "\n".join(lines)

    def list_commands(self) -> list[SlashCommand]:
        """Return canonical commands in registration order (aliases omitted)."""
        return [self._commands[name] for name in self._canonical_names]
