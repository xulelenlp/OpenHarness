"""Agent definition loading system for OpenHarness."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from openharness.config.paths import get_config_dir
from openharness.coordinator.agent_models import (
    AGENT_COLORS,
    EFFORT_LEVELS,
    ISOLATION_MODES,
    MEMORY_SCOPES,
    PERMISSION_MODES,
    AgentDefinition,
)
from openharness.coordinator.agent_prompts import _BUILTIN_AGENTS

logger = logging.getLogger(__name__)

def get_builtin_agent_definitions() -> list[AgentDefinition]:
    """Return the built-in agent definitions."""
    return list(_BUILTIN_AGENTS)


# ---------------------------------------------------------------------------
# Markdown / YAML-frontmatter loader
# ---------------------------------------------------------------------------


def _parse_agent_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file.

    Returns a (frontmatter_dict, body) tuple. Uses ``yaml.safe_load`` for
    proper YAML parsing (supports nested structures for hooks, mcpServers, etc.).
    """
    frontmatter: dict[str, Any] = {}
    body = content

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return frontmatter, body

    end_index: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = i
            break

    if end_index is None:
        return frontmatter, body

    fm_text = "\n".join(lines[1:end_index])
    try:
        parsed = yaml.safe_load(fm_text)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except yaml.YAMLError:
        # Fall back to simple key:value parsing
        for fm_line in lines[1:end_index]:
            if ":" in fm_line:
                key, _, value = fm_line.partition(":")
                frontmatter[key.strip()] = value.strip().strip("'\"")

    # Body is everything after the closing ---
    body = "\n".join(lines[end_index + 1 :]).strip()
    return frontmatter, body


def _parse_str_list(raw: Any) -> list[str] | None:
    """Parse a comma-separated string or list into a list of strings."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        items = [t.strip() for t in raw.split(",") if t.strip()]
        return items if items else None
    return None


def _parse_positive_int(raw: Any) -> int | None:
    """Parse a positive integer from frontmatter, returning None if invalid."""
    if raw is None:
        return None
    try:
        val = int(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def load_agents_dir(directory: Path) -> list[AgentDefinition]:
    """Load agent definitions from .md files in *directory*.

    Each file should contain YAML frontmatter with at least ``name`` and
    ``description`` fields. The markdown body becomes the ``system_prompt``.

    Supported frontmatter fields (all optional unless noted):

    Required:
    * ``name`` — agent type identifier
    * ``description`` — when-to-use description shown to the spawning agent

    Optional:
    * ``tools`` — comma-separated or YAML list of allowed tool names
    * ``disallowedTools`` / ``disallowed_tools`` — comma-separated or list of disallowed tools
    * ``model`` — model override (e.g. "haiku", "inherit")
    * ``effort`` — "low", "medium", "high", or a positive integer
    * ``permissionMode`` / ``permission_mode`` — one of PERMISSION_MODES
    * ``maxTurns`` / ``max_turns`` — positive integer turn limit
    * ``skills`` — comma-separated or list of skill names
    * ``mcpServers`` / ``mcp_servers`` — list of MCP server references or inline configs
    * ``hooks`` — YAML dict of session-scoped hooks
    * ``color`` — one of AGENT_COLORS
    * ``background`` — true/false; run as background task
    * ``initialPrompt`` / ``initial_prompt`` — string prepended to first user turn
    * ``memory`` — one of MEMORY_SCOPES
    * ``isolation`` — one of ISOLATION_MODES
    * ``omitClaudeMd`` / ``omit_claude_md`` — true/false; skip CLAUDE.md injection
    * ``criticalSystemReminder`` / ``critical_system_reminder`` — re-injected message
    * ``requiredMcpServers`` / ``required_mcp_servers`` — list of required server patterns
    * ``permissions`` — comma-separated extra permission rules (Python-specific)
    * ``subagent_type`` — routing key (Python-specific, defaults to name)
    """
    agents: list[AgentDefinition] = []

    if not directory.is_dir():
        return agents

    for path in sorted(directory.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter, body = _parse_agent_frontmatter(content)

            name = str(frontmatter.get("name", "")).strip() or path.stem
            description = str(frontmatter.get("description", "")).strip()
            if not description:
                description = f"Agent: {name}"

            # Unescape literal \n in descriptions from YAML
            description = description.replace("\\n", "\n")

            # --- tools ---
            tools = _parse_str_list(frontmatter.get("tools"))

            # --- disallowed tools ---
            disallowed_raw = frontmatter.get(
                "disallowedTools", frontmatter.get("disallowed_tools")
            )
            disallowed_tools = _parse_str_list(disallowed_raw)

            # --- model ---
            model_raw = frontmatter.get("model")
            model: str | None = None
            if isinstance(model_raw, str) and model_raw.strip():
                trimmed = model_raw.strip()
                model = "inherit" if trimmed.lower() == "inherit" else trimmed

            # --- effort ---
            effort_raw = frontmatter.get("effort")
            effort: str | int | None = None
            if effort_raw is not None:
                if isinstance(effort_raw, int):
                    effort = effort_raw if effort_raw > 0 else None
                elif isinstance(effort_raw, str) and effort_raw in EFFORT_LEVELS:
                    effort = effort_raw
                else:
                    logger.debug("Agent %s: invalid effort %r", name, effort_raw)

            # --- permissionMode ---
            perm_raw = frontmatter.get("permissionMode", frontmatter.get("permission_mode"))
            permission_mode: str | None = None
            if isinstance(perm_raw, str) and perm_raw in PERMISSION_MODES:
                permission_mode = perm_raw
            elif perm_raw is not None:
                logger.debug("Agent %s: invalid permissionMode %r", name, perm_raw)

            # --- maxTurns ---
            max_turns_raw = frontmatter.get("maxTurns", frontmatter.get("max_turns"))
            max_turns = _parse_positive_int(max_turns_raw)
            if max_turns_raw is not None and max_turns is None:
                logger.debug("Agent %s: invalid maxTurns %r", name, max_turns_raw)

            # --- skills ---
            skills_raw = frontmatter.get("skills")
            skills = _parse_str_list(skills_raw) or []

            # --- mcpServers ---
            mcp_raw = frontmatter.get("mcpServers", frontmatter.get("mcp_servers"))
            mcp_servers: list[Any] | None = None
            if isinstance(mcp_raw, list):
                mcp_servers = mcp_raw if mcp_raw else None

            # --- hooks ---
            hooks_raw = frontmatter.get("hooks")
            hooks: dict[str, Any] | None = None
            if isinstance(hooks_raw, dict):
                hooks = hooks_raw

            # --- color ---
            color_raw = frontmatter.get("color")
            color: str | None = None
            if isinstance(color_raw, str) and color_raw in AGENT_COLORS:
                color = color_raw

            # --- background ---
            bg_raw = frontmatter.get("background")
            background = bg_raw is True or bg_raw == "true"

            # --- initialPrompt ---
            ip_raw = frontmatter.get("initialPrompt", frontmatter.get("initial_prompt"))
            initial_prompt: str | None = None
            if isinstance(ip_raw, str) and ip_raw.strip():
                initial_prompt = ip_raw

            # --- memory ---
            memory_raw = frontmatter.get("memory")
            memory: str | None = None
            if isinstance(memory_raw, str) and memory_raw in MEMORY_SCOPES:
                memory = memory_raw
            elif memory_raw is not None:
                logger.debug("Agent %s: invalid memory %r", name, memory_raw)

            # --- isolation ---
            iso_raw = frontmatter.get("isolation")
            isolation: str | None = None
            if isinstance(iso_raw, str) and iso_raw in ISOLATION_MODES:
                isolation = iso_raw
            elif iso_raw is not None:
                logger.debug("Agent %s: invalid isolation %r", name, iso_raw)

            # --- omitClaudeMd ---
            ocm_raw = frontmatter.get("omitClaudeMd", frontmatter.get("omit_claude_md"))
            omit_claude_md = ocm_raw is True or ocm_raw == "true"

            # --- criticalSystemReminder ---
            csr_raw = frontmatter.get(
                "criticalSystemReminder", frontmatter.get("critical_system_reminder")
            )
            critical_system_reminder: str | None = None
            if isinstance(csr_raw, str) and csr_raw.strip():
                critical_system_reminder = csr_raw

            # --- requiredMcpServers ---
            rms_raw = frontmatter.get(
                "requiredMcpServers", frontmatter.get("required_mcp_servers")
            )
            required_mcp_servers = _parse_str_list(rms_raw)

            # --- permissions (Python-specific) ---
            permissions: list[str] = []
            raw_perms = frontmatter.get("permissions", "")
            if raw_perms:
                permissions = [p.strip() for p in str(raw_perms).split(",") if p.strip()]

            agents.append(
                AgentDefinition(
                    name=name,
                    description=description,
                    system_prompt=body or None,
                    tools=tools,
                    disallowed_tools=disallowed_tools,
                    model=model,
                    effort=effort,
                    permission_mode=permission_mode,
                    max_turns=max_turns,
                    skills=skills,
                    mcp_servers=mcp_servers,
                    hooks=hooks,
                    color=color,
                    background=background,
                    initial_prompt=initial_prompt,
                    memory=memory,
                    isolation=isolation,
                    omit_claude_md=omit_claude_md,
                    critical_system_reminder=critical_system_reminder,
                    required_mcp_servers=required_mcp_servers,
                    permissions=permissions,
                    filename=path.stem,
                    base_dir=str(directory),
                    subagent_type=str(frontmatter.get("subagent_type", name)),
                    source="user",
                )
            )
        except Exception:
            logger.debug("Failed to parse agent from %s", path, exc_info=True)
            continue

    return agents


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _get_user_agents_dir() -> Path:
    """Return the user agent definitions directory."""
    return get_config_dir() / "agents"


def get_all_agent_definitions() -> list[AgentDefinition]:
    """Return all agent definitions: built-in + user + plugin.

    Merge order (last writer wins for same ``name``):
    1. Built-in agents
    2. User agents (~/.openharness/agents/)
    3. Plugin agents (loaded from active plugins)

    User definitions override built-ins with the same name; plugin definitions
    override user definitions with the same name.
    """
    agent_map: dict[str, AgentDefinition] = {}

    # 1. Built-ins (lowest priority)
    for agent in get_builtin_agent_definitions():
        agent_map[agent.name] = agent

    # 2. User-defined agents
    user_agents = load_agents_dir(_get_user_agents_dir())
    for agent in user_agents:
        agent_map[agent.name] = agent

    # 3. Plugin agents — loaded lazily to avoid import cycles
    try:
        from openharness.config.settings import load_settings  # noqa: PLC0415
        from openharness.plugins.loader import load_plugins  # noqa: PLC0415

        settings = load_settings()
        import os  # noqa: PLC0415

        cwd = os.getcwd()
        for plugin in load_plugins(settings, cwd):
            if not plugin.enabled:
                continue
            for agent_def in getattr(plugin, "agents", []):
                if isinstance(agent_def, AgentDefinition):
                    agent_map[agent_def.name] = agent_def
    except Exception:
        pass

    return list(agent_map.values())


def get_agent_definition(name: str) -> AgentDefinition | None:
    """Return the agent definition for *name*, or ``None`` if not found."""
    for agent in get_all_agent_definitions():
        if agent.name == name:
            return agent
    return None


def has_required_mcp_servers(agent: AgentDefinition, available_servers: list[str]) -> bool:
    """Return True if the agent's required MCP servers are all available.

    Each pattern in ``required_mcp_servers`` must match (case-insensitive
    substring) at least one server in ``available_servers``.
    """
    if not agent.required_mcp_servers:
        return True
    return all(
        any(pattern.lower() in server.lower() for server in available_servers)
        for pattern in agent.required_mcp_servers
    )


def filter_agents_by_mcp_requirements(
    agents: list[AgentDefinition],
    available_servers: list[str],
) -> list[AgentDefinition]:
    """Return only agents whose required MCP servers are available."""
    return [a for a in agents if has_required_mcp_servers(a, available_servers)]
