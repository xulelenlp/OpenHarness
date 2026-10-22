"""Agent definition model and validation constants."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AGENT_COLORS: frozenset[str] = frozenset(
    {
        "red",
        "green",
        "blue",
        "yellow",
        "purple",
        "orange",
        "cyan",
        "magenta",
        "white",
        "gray",
    }
)

#: Valid effort level strings (maps to EFFORT_LEVELS in TS).
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high")

#: Valid permission mode strings (maps to PERMISSION_MODES in TS).
PERMISSION_MODES: tuple[str, ...] = (
    "default",
    "acceptEdits",
    "bypassPermissions",
    "plan",
    "dontAsk",
)

#: Valid memory scope strings (maps to AgentMemoryScope in TS).
MEMORY_SCOPES: tuple[str, ...] = ("user", "project", "local")

#: Valid isolation mode strings.
ISOLATION_MODES: tuple[str, ...] = ("worktree", "remote")


# ---------------------------------------------------------------------------
# AgentDefinition model
# ---------------------------------------------------------------------------


class AgentDefinition(BaseModel):
    """Full agent definition with all configuration fields.

    Field mapping to TypeScript ``BaseAgentDefinition``:
    - ``name``          → ``agentType``
    - ``description``   → ``whenToUse``
    - ``system_prompt`` → ``getSystemPrompt()`` return value
    - ``tools``         → ``tools`` (None means all tools / ``['*']``)
    - ``disallowed_tools`` → ``disallowedTools``
    - ``skills``        → ``skills``
    - ``mcp_servers``   → ``mcpServers``
    - ``hooks``         → ``hooks``
    - ``color``         → ``color``
    - ``model``         → ``model``
    - ``effort``        → ``effort``
    - ``permission_mode`` → ``permissionMode``
    - ``max_turns``     → ``maxTurns``
    - ``filename``      → ``filename``
    - ``base_dir``      → ``baseDir``
    - ``critical_system_reminder`` → ``criticalSystemReminder_EXPERIMENTAL``
    - ``required_mcp_servers`` → ``requiredMcpServers``
    - ``background``    → ``background``
    - ``initial_prompt`` → ``initialPrompt``
    - ``memory``        → ``memory``
    - ``isolation``     → ``isolation``
    - ``omit_claude_md`` → ``omitClaudeMd``
    """

    # --- required ---
    name: str
    description: str

    # --- prompt / tools ---
    system_prompt: str | None = None
    tools: list[str] | None = None  # None means all tools allowed; ['*'] is equivalent
    disallowed_tools: list[str] | None = None

    # --- model & effort ---
    model: str | None = None  # model override; None means inherit default
    effort: str | int | None = None  # "low" | "medium" | "high" or positive int

    # --- permissions ---
    permission_mode: str | None = None  # one of PERMISSION_MODES

    # --- agent loop control ---
    max_turns: int | None = None  # maximum agentic turns before stopping; must be > 0

    # --- skills & mcp ---
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[Any] | None = None  # str refs or {name: config} dicts
    required_mcp_servers: list[str] | None = None  # server name patterns that must be present

    # --- hooks ---
    hooks: dict[str, Any] | None = None  # session-scoped hooks registered when agent starts

    # --- ui ---
    color: str | None = None  # one of AGENT_COLORS

    # --- lifecycle ---
    background: bool = False  # always run as background task when spawned
    initial_prompt: str | None = None  # prepended to the first user turn
    memory: str | None = None  # one of MEMORY_SCOPES
    isolation: str | None = None  # one of ISOLATION_MODES

    # --- metadata ---
    filename: str | None = None  # original filename without .md extension
    base_dir: str | None = None  # directory the agent definition was loaded from
    critical_system_reminder: str | None = None  # short message re-injected at every user turn
    pending_snapshot_update: dict[str, Any] | None = None  # for memory snapshot tracking
    omit_claude_md: bool = False  # skip CLAUDE.md injection for this agent

    # --- Python-specific ---
    permissions: list[str] = Field(default_factory=list)  # extra permission rules
    subagent_type: str = "general-purpose"  # routing key used by the harness
    source: Literal["builtin", "user", "plugin"] = "builtin"
