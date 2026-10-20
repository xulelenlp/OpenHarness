"""``run_script`` 工具测试：脚本必须落在会话目录、用共享环境执行。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openharness.tools.base import ToolExecutionContext
from qaapp.config import ConversationWorkspaceConfig
from qaapp.conversation_files import ConversationPaths
from qaapp.script_tool import CONVERSATION_DIR_KEY, RunScriptInput, RunScriptTool


@pytest.fixture()
def workspace(tmp_path: Path):
    config = ConversationWorkspaceConfig(root=str(tmp_path / "conversations"))
    paths = ConversationPaths.for_conversation(None, "abc123abc123", config).ensure()
    return config, paths


def _context(paths: ConversationPaths) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path.cwd(), metadata={CONVERSATION_DIR_KEY: str(paths.root)})


def _run(tool: RunScriptTool, payload: dict, context: ToolExecutionContext):
    return asyncio.run(tool.execute(RunScriptInput.model_validate(payload), context))


def test_requires_conversation_dir():
    tool = RunScriptTool()
    result = asyncio.run(
        tool.execute(
            RunScriptInput(code="print(1)"),
            ToolExecutionContext(cwd=Path.cwd(), metadata={}),
        )
    )
    assert result.is_error
    assert CONVERSATION_DIR_KEY in result.output


def test_writes_script_into_conversation_dir_and_runs(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(
        tool,
        {"code": "print('hello from script')", "name": "greet.py"},
        _context(paths),
    )
    assert not result.is_error, result.output
    script = paths.scripts / "greet.py"
    assert script.is_file()
    assert "hello from script" in result.output
    assert result.metadata["script_path"] == "scripts/greet.py"
    assert result.metadata["exit_code"] == 0


def test_auto_names_script_and_can_use_uploads_relative_path(workspace):
    config, paths = workspace
    (paths.uploads / "data.txt").write_text("售电量,10\n", encoding="utf-8")
    tool = RunScriptTool(config)
    code = (
        "from pathlib import Path\n"
        "text = Path('uploads/data.txt').read_text(encoding='utf-8')\n"
        "Path('outputs/result.txt').write_text(text.upper(), encoding='utf-8')\n"
        "print('done', len(text))\n"
    )
    result = _run(tool, {"code": code}, _context(paths))
    assert not result.is_error, result.output
    assert "done" in result.output
    scripts = list(paths.scripts.glob("auto_*.py"))
    assert len(scripts) == 1
    assert (paths.outputs / "result.txt").read_text(encoding="utf-8").startswith("售电量")
    # 产物会被提示回模型，便于它告诉用户结果文件在哪
    assert "outputs/result.txt" in result.output


def test_nonzero_exit_marks_error(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(tool, {"code": "import sys\nsys.exit(3)\n"}, _context(paths))
    assert result.is_error
    assert result.metadata["exit_code"] == 3


def test_timeout_kills_script(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(
        tool,
        {"code": "import time\ntime.sleep(30)\n", "timeout_seconds": 1},
        _context(paths),
    )
    assert result.is_error
    assert "超时" in result.output
    assert result.metadata["timed_out"] is True


def test_output_truncated(workspace):
    config, paths = workspace
    config = config.model_copy(update={"script_output_max_chars": 200})
    tool = RunScriptTool(config)
    result = _run(tool, {"code": "print('x' * 5000)\n"}, _context(paths))
    assert not result.is_error
    assert "输出已截断" in result.output


def test_code_size_limit(workspace):
    config, paths = workspace
    config = config.model_copy(update={"script_code_max_chars": 50})
    tool = RunScriptTool(config)
    result = _run(tool, {"code": "print(1)\n" * 100}, _context(paths))
    assert result.is_error
    assert "过大" in result.output


def test_run_existing_script_and_rejects_escape(workspace):
    config, paths = workspace
    (paths.scripts / "existing.py").write_text("print('existing ok')\n", encoding="utf-8")
    tool = RunScriptTool(config)

    result = _run(tool, {"script": "scripts/existing.py"}, _context(paths))
    assert not result.is_error, result.output
    assert "existing ok" in result.output
    assert "复用已有脚本" in result.output

    escape = _run(tool, {"script": "../../etc/passwd"}, _context(paths))
    assert escape.is_error
    assert "越界" in escape.output


def test_code_and_script_are_exclusive(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(
        tool,
        {"code": "print(1)", "script": "scripts/x.py"},
        _context(paths),
    )
    assert result.is_error
    assert "二选一" in result.output


def test_language_extension_is_forced(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(
        tool,
        {"code": "console.log('node ok')", "language": "node", "name": "demo.py"},
        _context(paths),
    )
    # 机器上没装 node 时给出明确错误，装了则执行成功；两种情况都不能生成 .py
    assert (paths.scripts / "demo.py").exists() is False
    if result.is_error:
        assert "node" in result.output
    else:
        assert (paths.scripts / "demo.js").is_file()
        assert "node ok" in result.output


def test_script_name_traversal_sanitized(workspace):
    config, paths = workspace
    tool = RunScriptTool(config)
    result = _run(tool, {"code": "print('ok')", "name": "../../evil.py"}, _context(paths))
    assert not result.is_error, result.output
    created = list(paths.scripts.glob("*.py"))
    assert len(created) == 1
    assert created[0].parent == paths.scripts
    assert ".." not in created[0].name
