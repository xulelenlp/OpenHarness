"""File writing tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.diff import unified_diff_with_counts
from openharness.utils.fs import resolve_path


class FileWriteToolInput(BaseModel):
    """Arguments for the file write tool."""

    path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)


class FileWriteTool(BaseTool):
    """Write complete file contents."""

    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    input_model = FileWriteToolInput

    async def execute(
        self,
        arguments: FileWriteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = resolve_path(context.cwd, arguments.path)

        from openharness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from openharness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        approval_prompt = context.metadata.get("edit_approval_prompt") if context.metadata else None
        if approval_prompt is not None:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            diff_text, added, removed = unified_diff_with_counts(str(path), original, arguments.content)
            reply = await approval_prompt(str(path), diff_text, added, removed)
            if reply == "reject":
                return ToolResult(output=f"Write rejected by user: {path}", is_error=True)
            if arguments.create_directories:
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments.content, encoding="utf-8")
            stats = f"  ({_ANSI_GREEN}+{added}{_ANSI_RESET} {_ANSI_RED}-{removed}{_ANSI_RESET})"
            return ToolResult(output=f"Wrote {path}{stats}")

        if arguments.create_directories:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        return ToolResult(output=f"Wrote {path}")


_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"
