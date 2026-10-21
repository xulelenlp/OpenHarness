"""``run_script`` —— 会话级脚本执行工具。

需求：**任务需要写脚本时，按会话 ID 把脚本落到该会话的临时目录里再执行**。

因此本工具不依赖模型自觉：脚本由服务端写入 ``<会话目录>/scripts/``，用服务端
共享 conda 环境的解释器执行（``sys.executable``，已内置 pandas/openpyxl/python-docx），
工作目录固定在会话目录根，脚本可以用 ``uploads/...``、``outputs/...`` 这类相对路径。

权限：工具名固定为 ``run_script``，输入里刻意**不含** ``path``/``file_path``/``root``
字段，因此不会被「会话目录内写操作免确认」规则放行，默认仍按危险操作门禁走审批；
需要放开时在 ``dangerous-actions.json`` 里加 ``{"tool": "run_script", "action": "allow"}``。
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from openharness.sandbox import SandboxUnavailableError
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.process import terminate_process
from openharness.utils.shell import create_shell_subprocess
from qaapp.config import ConversationWorkspaceConfig
from qaapp.conversation_files import ConversationPaths, sanitize_filename

#: 会话目录通过 engine.tool_metadata 传给工具执行上下文。
CONVERSATION_DIR_KEY = "qaapp_conversation_dir"

LANGUAGE_SUFFIXES: dict[str, str] = {"python": ".py", "node": ".js"}
SUFFIX_LANGUAGES = {".py": "python", ".js": "node", ".mjs": "node", ".cjs": "node"}

_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS = 2.0


class RunScriptInput(BaseModel):
    """``run_script`` 的入参。"""

    code: str = Field(
        default="",
        description=(
            "要写入并执行的脚本内容（与 script 二选一）。脚本会被保存到本会话的 "
            "scripts/ 目录，然后立即执行。"
        ),
    )
    script: str = Field(
        default="",
        description=(
            "已存在脚本的路径（相对本会话目录，例如 scripts/analyze.py；与 code 二选一）。"
        ),
    )
    name: str = Field(
        default="",
        description="脚本文件名（不含路径），留空自动命名，例如 analyze_sales.py。",
    )
    language: Literal["python", "node"] | None = Field(
        default=None,
        description="脚本语言；留空时按文件后缀推断，默认 python。",
    )
    args: list[str] = Field(default_factory=list, description="传给脚本的命令行参数。")
    timeout_seconds: int = Field(default=120, ge=1, le=600, description="执行超时（秒）。")


class RunScriptTool(BaseTool):
    """把脚本写到会话目录并用共享环境执行。"""

    name = "run_script"
    description = (
        "在本会话的临时目录里创建并执行脚本（python / node）。"
        "脚本保存到 <会话目录>/scripts/，工作目录为 <会话目录>，"
        "因此可以用 uploads/<文件名> 读取用户上传的文件、把结果写到 outputs/。"
        "python 解释器就是服务端共享环境（自带 pandas/openpyxl/python-docx）。"
        "适合需要真实计算、批量处理 Excel/CSV、生成图表或文件的子任务。"
    )
    input_model = RunScriptInput

    def __init__(self, config: ConversationWorkspaceConfig | None = None) -> None:
        self._config = config or ConversationWorkspaceConfig()

    # 需要确认（不是只读）——按危险操作门禁处理。
    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(self, arguments: RunScriptInput, context: ToolExecutionContext) -> ToolResult:
        raw_dir = context.metadata.get(CONVERSATION_DIR_KEY)
        if not raw_dir:
            return ToolResult(
                output=(
                    "run_script 不可用：当前会话没有分配临时目录"
                    f"（缺少元数据 {CONVERSATION_DIR_KEY}）。"
                ),
                is_error=True,
            )
        paths = ConversationPaths(
            conversation_id=Path(str(raw_dir)).name,
            root=Path(str(raw_dir)),
        ).ensure()

        try:
            script_path, created = self._prepare_script(paths, arguments)
        except ValueError as exc:
            return ToolResult(output=f"脚本参数不合法：{exc}", is_error=True)

        argv = self._build_argv(script_path, arguments)
        if isinstance(argv, str):  # 解释器缺失等
            return ToolResult(output=argv, is_error=True)

        timeout = min(arguments.timeout_seconds, self._config.script_timeout_seconds)
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "QAAPP_CONVERSATION_DIR": str(paths.root),
        }
        started = time.monotonic()
        try:
            process = await create_shell_subprocess(
                " ".join(shlex.quote(part) for part in argv),
                cwd=paths.root,
                prefer_pty=False,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except SandboxUnavailableError as exc:
            return ToolResult(output=f"沙箱不可用，无法执行脚本：{exc}", is_error=True)

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            await terminate_process(process, force=True, timeout=3.0)
        except asyncio.CancelledError:
            await terminate_process(process, force=False, timeout=3.0)
            raise

        output = await _read_output(process)
        duration_ms = int((time.monotonic() - started) * 1000)
        exit_code = process.returncode if process.returncode is not None else -1

        body = _truncate(output, self._config.script_output_max_chars)
        header_lines = [
            f"[run_script] {paths.relative(script_path)}"
            + ("（本次新建）" if created else "（复用已有脚本）"),
            f"解释器：{argv[0]}",
            f"工作目录：{paths.root}",
            f"退出码：{exit_code}，耗时：{duration_ms}ms"
            + (f"，超时 {timeout}s 已被终止" if timed_out else ""),
        ]
        produced = _recent_outputs(paths, started)
        if produced:
            header_lines.append("本次运行后 outputs/ 下的文件：" + "、".join(produced))
        parts = ["\n".join(header_lines), "", "--- 输出 ---", body or "(无输出)"]
        return ToolResult(
            output="\n".join(parts),
            is_error=timed_out or exit_code != 0,
            metadata={
                "script_path": paths.relative(script_path),
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "timed_out": timed_out,
                "interpreter": argv[0],
            },
        )

    # -- 内部 ------------------------------------------------------------

    def _prepare_script(
        self, paths: ConversationPaths, arguments: RunScriptInput
    ) -> tuple[Path, bool]:
        """返回 (脚本绝对路径, 是否本次新建)；参数非法时抛 ``ValueError``。"""
        code = (arguments.code or "").strip()
        existing = (arguments.script or "").strip()

        if existing:
            if code:
                raise ValueError("code 与 script 只能二选一")
            target = paths.resolve_within(existing)
            if not target.is_file():
                raise ValueError(f"脚本不存在：{existing}")
            return target, False

        if not code:
            raise ValueError("必须提供 code（脚本内容）或 script（已有脚本路径）")
        if len(code) > self._config.script_code_max_chars:
            raise ValueError(
                f"脚本内容过大（{len(code)} 字符，上限 {self._config.script_code_max_chars}）"
            )

        language = arguments.language or "python"
        suffix = LANGUAGE_SUFFIXES[language]
        raw_name = (arguments.name or "").strip()
        if raw_name:
            cleaned = sanitize_filename(raw_name, fallback=f"script{suffix}")
            stem = Path(cleaned).stem or "script"
        else:
            stem = time.strftime("auto_%Y%m%d_%H%M%S")
        filename = sanitize_filename(f"{stem}{suffix}")
        target = paths.scripts / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code if code.endswith("\n") else code + "\n", encoding="utf-8")
        return target, True

    def _build_argv(self, script_path: Path, arguments: RunScriptInput) -> list[str] | str:
        suffix = script_path.suffix.lower()
        language = arguments.language or SUFFIX_LANGUAGES.get(suffix, "python")
        if language == "node":
            node = shutil.which("node")
            if not node:
                return "run_script 执行失败：服务端没有找到 node 可执行文件"
            return [node, str(script_path), *arguments.args]
        return [sys.executable, str(script_path), *arguments.args]


async def _read_output(process: asyncio.subprocess.Process) -> str:
    if process.stdout is None:
        return ""
    try:
        data = await asyncio.wait_for(
            process.stdout.read(), timeout=_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return ""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n...[输出已截断，仅保留前 {limit} 字符]"


def _recent_outputs(paths: ConversationPaths, started: float, limit: int = 10) -> list[str]:
    """列出本次运行后 outputs/ 下新增或更新的文件。"""
    directory = paths.outputs
    if not directory.is_dir():
        return []
    found: list[tuple[float, str]] = []
    for item in directory.rglob("*"):
        if not item.is_file():
            continue
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        if mtime >= started - 1:
            found.append((mtime, paths.relative(item)))
    found.sort(reverse=True)
    return [name for _, name in found[:limit]]


__all__ = ["CONVERSATION_DIR_KEY", "RunScriptInput", "RunScriptTool"]
