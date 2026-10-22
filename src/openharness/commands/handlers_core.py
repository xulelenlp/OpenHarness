# ruff: noqa: F403, F405
"""Built-in slash command handlers: core."""

from __future__ import annotations

from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandResult,
)


async def _exit_handler(_: str, context: CommandContext) -> CommandResult:
    del context
    return CommandResult(should_exit=True)

async def _clear_handler(_: str, context: CommandContext) -> CommandResult:
    context.engine.clear()
    return CommandResult(message="Conversation cleared.", clear_screen=True)

async def _status_handler(_: str, context: CommandContext) -> CommandResult:
    usage = context.engine.total_usage
    state = context.app_state.get() if context.app_state is not None else None
    manager = AuthManager()
    return CommandResult(
        message=(
            f"Messages: {len(context.engine.messages)}\n"
            f"Usage: input={usage.input_tokens} output={usage.output_tokens}\n"
            f"Profile: {manager.get_active_profile()}\n"
            f"Effort: {state.effort if state is not None else load_settings().effort}\n"
            f"Passes: {state.passes if state is not None else load_settings().passes}"
        )
    )

async def _version_handler(_: str, context: CommandContext) -> CommandResult:
    del context
    try:
        version = importlib.metadata.version("openharness")
    except importlib.metadata.PackageNotFoundError:
        version = "0.1.7"
    return CommandResult(message=f"OpenHarness {version}")

async def _context_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    prompt = build_runtime_system_prompt(
        settings,
        cwd=context.cwd,
        include_project_memory=context.include_project_memory,
    )
    return CommandResult(message=prompt)

async def _summary_handler(args: str, context: CommandContext) -> CommandResult:
    max_messages = 8
    if args:
        try:
            max_messages = max(1, int(args))
        except ValueError:
            return CommandResult(message="Usage: /summary [MAX_MESSAGES]")
    summary = summarize_messages(context.engine.messages, max_messages=max_messages)
    return CommandResult(message=summary or "No conversation content to summarize.")

async def _compact_handler(args: str, context: CommandContext) -> CommandResult:
    preserve_recent = 6
    if args:
        try:
            preserve_recent = max(1, int(args))
        except ValueError:
            return CommandResult(message="Usage: /compact [PRESERVE_RECENT]")
    before = len(context.engine.messages)
    try:
        compacted_result = await compact_conversation(
            context.engine.messages,
            api_client=context.engine.api_client,
            model=context.engine.model,
            system_prompt=context.engine.system_prompt,
            preserve_recent=preserve_recent,
            trigger="manual",
        )
        compacted = build_post_compact_messages(compacted_result)
    except Exception:
        compacted = compact_messages(context.engine.messages, preserve_recent=preserve_recent)
    context.engine.load_messages(compacted)
    return CommandResult(
        message=f"Compacted conversation from {before} messages to {len(compacted)}."
    )

async def _usage_handler(_: str, context: CommandContext) -> CommandResult:
    usage = context.engine.total_usage
    estimated = estimate_conversation_tokens(context.engine.messages)
    return CommandResult(
        message=(
            f"Actual usage: input={usage.input_tokens} output={usage.output_tokens}\n"
            f"Estimated conversation tokens: {estimated}\n"
            f"Messages: {len(context.engine.messages)}"
        )
    )

async def _cost_handler(_: str, context: CommandContext) -> CommandResult:
    usage = context.engine.total_usage
    model = context.app_state.get().model if context.app_state is not None else load_settings().model
    estimated_cost = "unavailable"
    if model.startswith("claude-3-5-sonnet"):
        estimated = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
        estimated_cost = f"${estimated:.4f} (estimated)"
    elif model.startswith("claude-3-7-sonnet"):
        estimated = (usage.input_tokens * 3.0 + usage.output_tokens * 15.0) / 1_000_000
        estimated_cost = f"${estimated:.4f} (estimated)"
    elif model.startswith("claude-3-opus"):
        estimated = (usage.input_tokens * 15.0 + usage.output_tokens * 75.0) / 1_000_000
        estimated_cost = f"${estimated:.4f} (estimated)"
    return CommandResult(
        message=(
            f"Model: {model}\n"
            f"Input tokens: {usage.input_tokens}\n"
            f"Output tokens: {usage.output_tokens}\n"
            f"Total tokens: {usage.total_tokens}\n"
            f"Estimated cost: {estimated_cost}"
        )
    )

async def _stats_handler(_: str, context: CommandContext) -> CommandResult:
    settings = load_settings()
    memory_backend = _memory_backend_for_context(context)
    memory_count = len(memory_backend.list_files())
    task_count = len(get_task_manager().list_tasks())
    tool_count = len(context.tool_registry.list_tools()) if context.tool_registry is not None else 0
    style = settings.output_style
    if context.app_state is not None:
        state = context.app_state.get()
        style = state.output_style
    return CommandResult(
        message=(
            "Session stats:\n"
            f"- messages: {len(context.engine.messages)}\n"
            f"- estimated_tokens: {estimate_conversation_tokens(context.engine.messages)}\n"
            f"- tools: {tool_count}\n"
            f"- memory_files: {memory_count}\n"
            f"- background_tasks: {task_count}\n"
            f"- output_style: {style}"
        )
    )

async def _dream_handler(args: str, context: CommandContext) -> CommandResult:
    settings = getattr(context.engine, "_settings", None) or load_settings().materialize_active_profile()
    parts = args.split()
    action = parts[0] if parts else "run"
    backend = context.memory_backend if context.memory_backend is not None else None
    memory_dir = backend.get_memory_dir() if backend is not None else get_project_memory_dir(context.cwd)
    session_dir = context.session_backend.get_session_dir(context.cwd)
    app_label = backend.label if backend is not None else "openharness project memory"
    runner_module = "ohmo" if backend is not None and "ohmo" in backend.label.lower() else "openharness"
    if action == "status":
        last_at = read_last_consolidated_at(context.cwd, memory_dir=memory_dir)
        last = "never" if last_at <= 0 else datetime.fromtimestamp(last_at).isoformat(timespec="seconds")
        return CommandResult(
            message=(
                f"Auto-dream: {'on' if settings.memory.auto_dream_enabled else 'off'}\n"
                f"Memory store: {app_label}\n"
                f"Memory directory: {memory_dir}\n"
                f"Session directory: {session_dir}\n"
                f"Last consolidated: {last}"
            )
        )
    if action == "diff":
        target = parts[1] if len(parts) > 1 else "latest"
        task_memory_dir = str(memory_dir)
        backup_dir = ""
        label = target
        if target == "latest":
            latest = latest_memory_backup(memory_dir, app_label=app_label)
            backup_dir = str(latest) if latest is not None else ""
            label = "latest backup"
        else:
            task = get_task_manager().get_task(target)
            if task is not None and task.type == "dream":
                backup_dir = task.metadata.get("backup_dir", "")
                task_memory_dir = task.metadata.get("memory_dir", str(memory_dir))
                label = task.id
        if not backup_dir:
            return CommandResult(message=f"No dream backup found for {target}.")
        diff = diff_memory_dirs(backup_dir, task_memory_dir)
        return CommandResult(
            message=(
                f"Dream diff for {label}:\n"
                f"Backup: {backup_dir}\n"
                f"Memory: {task_memory_dir}\n"
                f"{format_memory_diff(diff)}"
            )
        )
    if action == "rollback":
        target = parts[1] if len(parts) > 1 else "latest"
        task_memory_dir = str(memory_dir)
        backup_dir = ""
        label = target
        if target == "latest":
            latest = latest_memory_backup(memory_dir, app_label=app_label)
            backup_dir = str(latest) if latest is not None else ""
            label = "latest backup"
        else:
            task = get_task_manager().get_task(target)
            if task is not None and task.type == "dream":
                backup_dir = task.metadata.get("backup_dir", "")
                task_memory_dir = task.metadata.get("memory_dir", str(memory_dir))
                label = task.id
        if not backup_dir:
            return CommandResult(message=f"No dream backup found for {target}.")
        restore_memory_backup(backup_dir, task_memory_dir)
        return CommandResult(message=f"Rolled back memory from backup for {label}: {backup_dir}")
    if action not in {"run", "now", "preview"}:
        return CommandResult(message="Usage: /dream [run|now|preview|status|diff [task_id]|rollback [task_id]]")
    task = await start_dream_now(
        cwd=context.cwd,
        settings=settings,
        model=context.engine.model,
        current_session_id=context.session_id,
        force=True,
        memory_dir=memory_dir,
        session_dir=session_dir,
        app_label=app_label,
        runner_module=runner_module,
        preview=action == "preview",
    )
    if task is None:
        return CommandResult(message="Dream did not start. Memory may be disabled or another dream is running.")
    return CommandResult(
        message=(
            f"Started {'preview ' if action == 'preview' else ''}memory consolidation task {task.id}.\n"
            f"Memory directory: {task.metadata.get('memory_dir', get_project_memory_dir(context.cwd))}\n"
            f"Backup directory: {task.metadata.get('backup_dir', '') or '(preview/no backup)'}\n"
            "Use /tasks or task_output to inspect progress. After completion: /dream diff latest or /dream rollback latest."
        )
    )
