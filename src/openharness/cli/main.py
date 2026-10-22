"""Main ``abc`` command entry callback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from openharness.cli.app import _version_callback, app

# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
    # --- Session ---
    continue_session: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Continue the most recent conversation in the current directory",
        rich_help_panel="Session",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        "-r",
        help="Resume a conversation by session ID, or open picker",
        rich_help_panel="Session",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Set a display name for this session",
        rich_help_panel="Session",
    ),
    # --- Model & Effort ---
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model alias (e.g. 'sonnet', 'opus') or full model ID",
        rich_help_panel="Model & Effort",
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help="Effort level for the session (low, medium, high, xhigh/max)",
        rich_help_panel="Model & Effort",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Override verbose mode setting from config",
        rich_help_panel="Model & Effort",
    ),
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help="Maximum number of agentic turns (enforced by default in --print; optional cap for interactive mode)",
        rich_help_panel="Model & Effort",
    ),
    # --- Output ---
    print_mode: str | None = typer.Option(
        None,
        "--print",
        "-p",
        help="Print response and exit. Pass your prompt as the value: -p 'your prompt'",
        rich_help_panel="Output",
    ),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        help="Output format with --print: text (default), json, or stream-json",
        rich_help_panel="Output",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview resolved runtime config, skills, commands, and tools without executing the model or tools",
        rich_help_panel="Output",
    ),
    # --- Permissions ---
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Permission mode: default, plan, or full_auto",
        rich_help_panel="Permissions",
    ),
    dangerously_skip_permissions: bool = typer.Option(
        False,
        "--dangerously-skip-permissions",
        help="Bypass all permission checks (only for sandboxed environments)",
        rich_help_panel="Permissions",
    ),
    allowed_tools: Optional[list[str]] = typer.Option(
        None,
        "--allowed-tools",
        help="Comma or space-separated list of tool names to allow",
        rich_help_panel="Permissions",
    ),
    disallowed_tools: Optional[list[str]] = typer.Option(
        None,
        "--disallowed-tools",
        help="Comma or space-separated list of tool names to deny",
        rich_help_panel="Permissions",
    ),
    # --- System & Context ---
    system_prompt: str | None = typer.Option(
        None,
        "--system-prompt",
        "-s",
        help="Override the default system prompt",
        rich_help_panel="System & Context",
    ),
    append_system_prompt: str | None = typer.Option(
        None,
        "--append-system-prompt",
        help="Append text to the default system prompt",
        rich_help_panel="System & Context",
    ),
    settings_file: str | None = typer.Option(
        None,
        "--settings",
        help="Path to a JSON settings file or inline JSON string",
        rich_help_panel="System & Context",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Anthropic-compatible API base URL",
        rich_help_panel="System & Context",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "-k",
        help="API key (overrides config and environment)",
        rich_help_panel="System & Context",
    ),
    bare: bool = typer.Option(
        False,
        "--bare",
        help="Minimal mode: skip hooks, plugins, MCP, and auto-discovery",
        rich_help_panel="System & Context",
    ),
    api_format: str | None = typer.Option(
        None,
        "--api-format",
        help="API format: 'anthropic' (default), 'openai' (DashScope, GitHub Models, etc.), or 'copilot' (GitHub Copilot)",
        rich_help_panel="System & Context",
    ),
    theme: str | None = typer.Option(
        None,
        "--theme",
        help="TUI theme: default, dark, minimal, cyberpunk, solarized, or custom name",
        rich_help_panel="System & Context",
    ),
    # --- Advanced ---
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug logging",
        rich_help_panel="Advanced",
    ),
    mcp_config: Optional[list[str]] = typer.Option(
        None,
        "--mcp-config",
        help="Load MCP servers from JSON files or strings",
        rich_help_panel="Advanced",
    ),
    cwd: str = typer.Option(
        str(Path.cwd()),
        "--cwd",
        help="Working directory for the session",
        hidden=True,
    ),
    backend_only: bool = typer.Option(
        False,
        "--backend-only",
        help="Run the structured backend host for the React terminal UI",
        hidden=True,
    ),
    task_worker: bool = typer.Option(
        False,
        "--task-worker",
        help="Run the stdin-driven headless worker loop used for background agent tasks",
        hidden=True,
    ),
) -> None:
    """Start an interactive session or run a single prompt."""
    # config/backend/.env 是模型/凭据的唯一配置来源（与 Web 服务一致）；
    # 进程环境变量优先，不会被文件覆盖。
    from openharness.config.settings import apply_project_env

    apply_project_env()

    if ctx.invoked_subcommand is not None:
        return

    import asyncio
    import logging

    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            stream=sys.stderr,
        )
        logging.getLogger("openharness").setLevel(logging.DEBUG)
    elif os.environ.get("OPENHARNESS_LOG_LEVEL"):
        lvl = getattr(logging, os.environ["OPENHARNESS_LOG_LEVEL"].upper(), logging.WARNING)
        logging.basicConfig(level=lvl, format="%(asctime)s [%(name)s] %(levelname)s %(message)s", stream=sys.stderr)

    if dangerously_skip_permissions:
        permission_mode = "full_auto"

    # Apply --theme override to settings
    if theme:
        from openharness.config.settings import load_settings, save_settings

        settings = load_settings()
        settings.theme = theme
        save_settings(settings)

    from openharness.ui.app import run_print_mode, run_repl, run_task_worker

    if dry_run and (continue_session or resume is not None):
        print("Error: --dry-run does not support --continue/--resume yet.", file=sys.stderr)
        raise typer.Exit(1)

    if dry_run:
        prompt = print_mode.strip() if print_mode is not None else None
        if print_mode is not None and not prompt:
            print("Error: -p/--print requires a prompt value, e.g. -p 'your prompt'", file=sys.stderr)
            raise typer.Exit(1)
        # Resolve through the package module so tests and callers that patch
        # ``openharness.cli._build_dry_run_preview`` are honoured at call time.
        from openharness import cli as _cli

        preview = _cli._build_dry_run_preview(
            prompt=prompt,
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            base_url=base_url,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            api_key=api_key,
            api_format=api_format,
            permission_mode=permission_mode,
            effort=effort,
        )
        effective_output_format = output_format or "text"
        if effective_output_format == "text":
            print(_cli._format_dry_run_preview(preview))
        elif effective_output_format == "json":
            print(json.dumps(preview, ensure_ascii=False, indent=2))
        elif effective_output_format == "stream-json":
            print(json.dumps(preview, ensure_ascii=False))
        else:
            print(
                "Error: --dry-run only supports --output-format text, json, or stream-json",
                file=sys.stderr,
            )
            raise typer.Exit(1)
        return

    # Handle --continue and --resume flags
    if continue_session or resume is not None:
        from openharness.services.session_storage import (
            list_session_snapshots,
            load_session_by_id,
            load_session_snapshot,
        )

        session_data = None
        if continue_session:
            session_data = load_session_snapshot(cwd)
            if session_data is None:
                print("No previous session found in this directory.", file=sys.stderr)
                raise typer.Exit(1)
            print(f"Continuing session: {session_data.get('summary', '(untitled)')[:60]}")
        elif resume == "" or resume is None:
            # --resume with no value: show session picker
            sessions = list_session_snapshots(cwd, limit=10)
            if not sessions:
                print("No saved sessions found.", file=sys.stderr)
                raise typer.Exit(1)
            print("Saved sessions:")
            for i, s in enumerate(sessions, 1):
                print(f"  {i}. [{s['session_id']}] {s.get('summary', '?')[:50]} ({s['message_count']} msgs)")
            choice = typer.prompt("Enter session number or ID")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(sessions):
                    session_data = load_session_by_id(cwd, sessions[idx]["session_id"])
                else:
                    print("Invalid selection.", file=sys.stderr)
                    raise typer.Exit(1)
            except ValueError:
                session_data = load_session_by_id(cwd, choice)
            if session_data is None:
                print(f"Session not found: {choice}", file=sys.stderr)
                raise typer.Exit(1)
        else:
            session_data = load_session_by_id(cwd, resume)
            if session_data is None:
                print(f"Session not found: {resume}", file=sys.stderr)
                raise typer.Exit(1)

        # Pass restored session to the REPL
        asyncio.run(
            run_repl(
                prompt=None,
                cwd=cwd,
                model=session_data.get("model") or model,
                backend_only=backend_only,
                base_url=base_url,
                system_prompt=system_prompt,
                api_key=api_key,
                restore_messages=session_data.get("messages"),
                restore_tool_metadata=session_data.get("tool_metadata"),
                permission_mode=permission_mode,
                api_format=api_format,
                effort=effort,
            )
        )
        return

    if print_mode is not None:
        prompt = print_mode.strip()
        if not prompt:
            print("Error: -p/--print requires a prompt value, e.g. -p 'your prompt'", file=sys.stderr)
            raise typer.Exit(1)
        asyncio.run(
            run_print_mode(
                prompt=prompt,
                output_format=output_format or "text",
                cwd=cwd,
                model=model,
                base_url=base_url,
                system_prompt=system_prompt,
                append_system_prompt=append_system_prompt,
                api_key=api_key,
                api_format=api_format,
                permission_mode=permission_mode,
                max_turns=max_turns,
                effort=effort,
            )
        )
        return

    if task_worker:
        asyncio.run(
            run_task_worker(
                cwd=cwd,
                model=model,
                max_turns=max_turns,
                base_url=base_url,
                system_prompt=system_prompt,
                api_key=api_key,
                api_format=api_format,
                permission_mode=permission_mode,
                effort=effort,
            )
        )
        return

    asyncio.run(
        run_repl(
            prompt=None,
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            backend_only=backend_only,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            permission_mode=permission_mode,
            effort=effort,
        )
    )
