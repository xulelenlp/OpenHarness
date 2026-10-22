# ruff: noqa: F403, F405
"""Built-in slash command handlers: memory."""

from __future__ import annotations

from openharness.commands.handlers_common import *
from openharness.commands.memory_helpers import *
from openharness.commands.registry_base import (
    CommandContext,
    CommandResult,
)
from openharness.commands.registry_helpers import (
    _copy_to_clipboard,
    _last_message_text,
    _rewind_turns,
)


async def _memory_handler(args: str, context: CommandContext) -> CommandResult:
    backend = _memory_backend_for_context(context)
    tokens = args.split(maxsplit=1)
    if not tokens:
        return CommandResult(
            message=(
                f"Memory store: {backend.label}\n"
                f"Memory directory: {backend.get_memory_dir()}\n"
                f"Entrypoint: {backend.get_entrypoint()}\n"
                "Commands: list, show NAME, add TITLE :: CONTENT, remove NAME, "
                "edit [NAME], validate, extract, session, team, agent, "
                "migrate --dry-run, migrate --apply"
            )
        )
    action = tokens[0]
    rest = tokens[1] if len(tokens) == 2 else ""
    if action == "list":
        memory_files = backend.list_files()
        if not memory_files:
            return CommandResult(message="No memory files.")
        return CommandResult(message="\n".join(path.name for path in memory_files))
    if action == "migrate":
        if rest not in {"--dry-run", "--apply"}:
            return CommandResult(
                message=(
                    "Usage: /memory "
                    "[list|show NAME|add TITLE :: CONTENT|remove NAME|"
                    "migrate --dry-run|migrate --apply]"
                )
            )
        summary = migrate_memory(
            context.cwd,
            memory_dir=backend.get_memory_dir(),
            default_type=backend.default_type,
            default_category=backend.default_category,
            apply=rest == "--apply",
        )
        mode = "dry run" if summary.dry_run else "applied"
        lines = [
            f"Memory migration {mode}.",
            f"Scanned: {summary.scanned}",
            f"Changed: {summary.changed}",
            f"Unchanged: {summary.unchanged}",
            f"Failed: {summary.failed}",
        ]
        if summary.backup_dir:
            lines.append(f"Backup: {summary.backup_dir}")
        if summary.changed_files:
            lines.append("Changed files: " + ", ".join(summary.changed_files))
        if summary.failed_files:
            lines.append("Failed files: " + ", ".join(summary.failed_files))
        return CommandResult(message="\n".join(lines))
    if action == "show" and rest:
        memory_dir = backend.get_memory_dir()
        path, invalid = _resolve_memory_entry_path(memory_dir, rest)
        if invalid:
            return CommandResult(message="Memory entry path must stay within the configured memory directory.")
        if path is None:
            return CommandResult(message=f"Memory entry not found: {rest}")
        if not path.exists():
            return CommandResult(message=f"Memory entry not found: {rest}")
        content = path.read_text(encoding="utf-8")
        metadata, _, _, _ = split_memory_file(content)
        if is_disabled_metadata(metadata) or is_memory_expired(metadata):
            return CommandResult(message=f"Memory entry not found: {rest}")
        return CommandResult(message=content)
    if action == "add" and rest:
        memory_type, scope, cleaned_rest = _parse_memory_add_flags(rest)
        title, separator, content = cleaned_rest.partition("::")
        if not separator or not title.strip() or not content.strip():
            return CommandResult(message="Usage: /memory add [--type TYPE] [--scope SCOPE] TITLE :: CONTENT")
        if context.memory_backend is None:
            path = add_memory_entry(
                context.cwd,
                title.strip(),
                content.strip(),
                memory_type=memory_type,
                scope=scope,
            )
        else:
            path = backend.add_entry(title.strip(), content.strip())
        return CommandResult(message=f"Added memory entry {path.name}")
    if action == "remove" and rest:
        if backend.remove_entry(rest.strip()):
            return CommandResult(message=f"Removed memory entry {rest.strip()}")
        return CommandResult(message=f"Memory entry not found: {rest.strip()}")
    if action == "edit":
        return _handle_memory_edit_command(rest, context, backend)
    if action == "validate":
        return _handle_memory_validate_command(context)
    if action == "extract":
        if context.memory_backend is not None:
            return CommandResult(message="Memory extraction is only supported for OpenHarness project memory.")
        result = await extract_memories_from_turn(
            cwd=context.cwd,
            api_client=context.engine.api_client,
            model=context.engine.model,
            messages=context.engine.messages,
            max_records=load_settings().memory.auto_extract_max_records,
        )
        if result.skipped:
            return CommandResult(message=f"Memory extraction skipped: {result.reason}")
        return CommandResult(
            message="Memory extraction wrote:\n" + "\n".join(f"- {path}" for path in result.written_paths)
        )
    if action == "session":
        return _handle_memory_session_command(rest, context)
    if action == "team":
        return _handle_memory_team_command(rest, context)
    if action == "agent":
        return _handle_memory_agent_command(rest, context)
    return CommandResult(
        message=(
            "Usage: /memory "
            "[list|show NAME|add TITLE :: CONTENT|remove NAME|edit [NAME]|"
            "validate|extract|session|team|agent|"
            "migrate --dry-run|migrate --apply]"
        )
    )

async def _hooks_handler(_: str, context: CommandContext) -> CommandResult:
    return CommandResult(message=context.hooks_summary or "No hooks configured.")

async def _resume_handler(args: str, context: CommandContext) -> CommandResult:
    tokens = args.strip().split()

    # /resume <session_id> — load a specific session
    if tokens:
        sid = tokens[0]
        snapshot = context.session_backend.load_by_id(context.cwd, sid)
        if snapshot is None:
            return CommandResult(message=f"Session not found: {sid}")
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(item) for item in snapshot.get("messages", [])]
        )
        context.engine.load_messages(messages)
        summary = snapshot.get("summary", "")[:60]
        return CommandResult(
            message=f"Restored {len(messages)} messages from session {sid}"
            + (f" ({summary})" if summary else ""),
            replay_messages=messages,
        )

    # /resume — list sessions (for the TUI to show a picker)
    sessions = context.session_backend.list_snapshots(context.cwd, limit=10)
    if not sessions:
        # Fall back to latest.json
        snapshot = context.session_backend.load_latest(context.cwd)
        if snapshot is None:
            return CommandResult(message="No saved sessions found for this project.")
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(item) for item in snapshot.get("messages", [])]
        )
        context.engine.load_messages(messages)
        return CommandResult(
            message=f"Restored {len(messages)} messages from the latest session.",
            replay_messages=messages,
        )

    # Format session list for display / picker
    import time
    lines = ["Saved sessions:"]
    for s in sessions:
        ts = time.strftime("%m/%d %H:%M", time.localtime(s["created_at"]))
        summary = s["summary"][:50] or "(no summary)"
        lines.append(f"  {s['session_id']}  {ts}  {s['message_count']}msg  {summary}")
    lines.append("")
    lines.append("Use /resume <session_id> to restore a specific session.")
    return CommandResult(message="\n".join(lines))

async def _export_handler(_: str, context: CommandContext) -> CommandResult:
    path = context.session_backend.export_markdown(cwd=context.cwd, messages=context.engine.messages)
    return CommandResult(message=f"Exported transcript to {path}")

async def _share_handler(_: str, context: CommandContext) -> CommandResult:
    path = context.session_backend.export_markdown(cwd=context.cwd, messages=context.engine.messages)
    return CommandResult(message=f"Created shareable transcript snapshot at {path}")

async def _copy_handler(args: str, context: CommandContext) -> CommandResult:
    text = args.strip() or _last_message_text(context.engine.messages)
    if not text:
        return CommandResult(message="Nothing to copy.")
    copied, target = _copy_to_clipboard(text)
    if copied:
        return CommandResult(message=f"Copied {len(text)} characters to the clipboard.")
    return CommandResult(message=f"Clipboard unavailable. Saved copied text to {target}")

async def _session_handler(args: str, context: CommandContext) -> CommandResult:
    session_dir = context.session_backend.get_session_dir(context.cwd)
    tokens = args.split()
    if not tokens or tokens[0] == "show":
        latest = session_dir / "latest.json"
        transcript = session_dir / "transcript.md"
        lines = [
            f"Session ID: {context.session_id or '(none)'}",
            f"Session directory: {session_dir}",
            f"Latest snapshot: {'present' if latest.exists() else 'missing'}",
            f"Transcript export: {'present' if transcript.exists() else 'missing'}",
            f"Message count: {len(context.engine.messages)}",
        ]
        return CommandResult(message="\n".join(lines))
    if tokens[0] == "ls":
        files = sorted(path.name for path in session_dir.iterdir())
        return CommandResult(message="\n".join(files) if files else "(empty)")
    if tokens[0] == "path":
        return CommandResult(message=str(session_dir))
    if tokens[0] == "tag" and len(tokens) == 2:
        safe_name = "".join(character for character in tokens[1] if character.isalnum() or character in {"-", "_"})
        if not safe_name:
            return CommandResult(message="Usage: /session tag NAME")
        snapshot_path = context.session_backend.save_snapshot(
            cwd=context.cwd,
            model=context.app_state.get().model if context.app_state is not None else load_settings().model,
            system_prompt=build_runtime_system_prompt(
                load_settings(),
                cwd=context.cwd,
                include_project_memory=context.include_project_memory,
            ),
            messages=context.engine.messages,
            usage=context.engine.total_usage,
        )
        export_path = context.session_backend.export_markdown(cwd=context.cwd, messages=context.engine.messages)
        tagged_json = session_dir / f"{safe_name}.json"
        tagged_md = session_dir / f"{safe_name}.md"
        shutil.copy2(snapshot_path, tagged_json)
        shutil.copy2(export_path, tagged_md)
        return CommandResult(message=f"Tagged session as {safe_name}:\n- {tagged_json}\n- {tagged_md}")
    if tokens[0] == "clear":
        if session_dir.exists():
            shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        return CommandResult(message=f"Cleared session storage at {session_dir}")
    return CommandResult(message="Usage: /session [show|ls|path|tag NAME|clear]")

async def _rewind_handler(args: str, context: CommandContext) -> CommandResult:
    turns = 1
    if args.strip():
        try:
            turns = max(1, int(args.strip()))
        except ValueError:
            return CommandResult(message="Usage: /rewind [TURNS]")
    before = len(context.engine.messages)
    updated = _rewind_turns(context.engine.messages, turns)
    context.engine.load_messages(updated)
    removed = before - len(updated)
    return CommandResult(message=f"Rewound {turns} turn(s); removed {removed} message(s).")

async def _tag_handler(args: str, context: CommandContext) -> CommandResult:
    name = args.strip()
    if not name:
        return CommandResult(message="Usage: /tag NAME")
    return await _session_handler(f"tag {name}", context)

async def _files_handler(args: str, context: CommandContext) -> CommandResult:
    raw = args.strip()
    root = Path(context.cwd)
    max_items = 30
    tokens = raw.split(maxsplit=1)
    if tokens and tokens[0] == "dirs":
        dirs = [
            path
            for path in sorted(root.rglob("*"))
            if path.is_dir() and ".git" not in path.parts and ".venv" not in path.parts
        ]
        lines = [str(path.relative_to(root)) for path in dirs[:max_items]]
        if len(dirs) > max_items:
            lines.append(f"... {len(dirs) - max_items} more")
        return CommandResult(message="\n".join(lines) if lines else "(no directories)")
    if tokens and tokens[0].isdigit():
        max_items = max(1, min(int(tokens[0]), 200))
        raw = tokens[1] if len(tokens) == 2 else ""
    needle = raw.lower()
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts and ".venv" not in path.parts
    ]
    if needle:
        files = [path for path in files if needle in str(path.relative_to(root)).lower()]
    lines = [str(path.relative_to(root)) for path in files[:max_items]]
    if len(files) > max_items:
        lines.append(f"... {len(files) - max_items} more")
    return CommandResult(
        message="\n".join(lines) if lines else "(no matching files)"
    )

async def _agents_handler(args: str, context: CommandContext) -> CommandResult:
    tokens = args.split(maxsplit=1)
    guide = (
        "Subagent guide:\n"
        "- Ask the model to delegate with the `agent` tool when the task needs background work or parallel investigation.\n"
        '- The usual worker shape is subagent_type="worker".\n'
        "- /agents lists known worker tasks.\n"
        "- /agents show TASK_ID shows one worker's output and metadata.\n"
        "- send_message(task_id=..., message=...) can continue a spawned worker.\n"
        "- task_output(task_id=...) reads the worker's latest output."
    )
    if tokens and tokens[0] in {"help", "usage"}:
        return CommandResult(
            message=guide
        )
    if tokens and tokens[0] == "show" and len(tokens) == 2:
        task = get_task_manager().get_task(tokens[1])
        if task is None or task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
            return CommandResult(message=f"No agent found with ID: {tokens[1]}")
        output = get_task_manager().read_task_output(task.id)
        return CommandResult(
            message=(
                f"{task.id} {task.type} {task.status} {task.description}\n"
                f"metadata={task.metadata}\n"
                f"output:\n{output or '(no output)'}"
            )
        )
    tasks = [
        task
        for task in get_task_manager().list_tasks()
        if task.type in {"local_agent", "remote_agent", "in_process_teammate"}
    ]
    if not tasks:
        return CommandResult(
            message=f"No active or recorded agents. Run /agents help for usage.\n\n{guide}"
        )
    lines = [
        f"{task.id} {task.type} {task.status} {task.description}"
        for task in tasks
    ]
    return CommandResult(message="\n".join(lines))
