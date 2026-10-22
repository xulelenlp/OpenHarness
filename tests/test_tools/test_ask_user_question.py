"""Tests for the option-aware ``ask_user_question`` tool."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openharness.tools.ask_user_question_tool import (
    AskUserQuestionTool,
    AskUserQuestionToolInput,
)
from openharness.tools.base import ToolExecutionContext


def _context(tmp_path: Path, answer) -> ToolExecutionContext:
    return ToolExecutionContext(
        cwd=tmp_path,
        metadata={"ask_user_prompt": answer},
    )


@pytest.mark.asyncio
async def test_options_are_forwarded_and_answer_returned(tmp_path: Path):
    seen: dict[str, object] = {}

    async def _answer(question: str, options: list[dict]):
        seen["question"] = question
        seen["options"] = options
        return options[1]["label"]

    tool = AskUserQuestionTool()
    arguments = tool.input_model(
        question="报告按什么口径生成？",
        options=[
            {"label": "按供电单位", "description": "适合管理汇报", "recommended": True},
            {"label": "按行业分类", "description": "适合结构分析"},
            {"label": "按电压等级"},
        ],
    )
    result = await tool.execute(arguments, _context(tmp_path, _answer))

    assert result.output == "按行业分类"
    assert result.is_error is False
    assert seen["question"] == "报告按什么口径生成？"
    options = seen["options"]
    assert isinstance(options, list)
    assert options[0] == {
        "label": "按供电单位",
        "description": "适合管理汇报",
        "recommended": True,
    }
    assert options[2] == {"label": "按电压等级", "description": "", "recommended": False}


@pytest.mark.asyncio
async def test_string_options_are_coerced(tmp_path: Path):
    async def _answer(_question: str, options: list[dict]):
        return options[0]["label"]

    tool = AskUserQuestionTool()
    arguments = tool.input_model(question="选一个", options=["方案 A", "方案 B"])
    result = await tool.execute(arguments, _context(tmp_path, _answer))

    assert [option.label for option in arguments.options] == ["方案 A", "方案 B"]
    assert result.output == "方案 A"


@pytest.mark.asyncio
async def test_text_only_question_passes_empty_options(tmp_path: Path):
    async def _answer(_question: str, options: list[dict]):
        assert options == []
        return "green"

    tool = AskUserQuestionTool()
    result = await tool.execute(
        tool.input_model(question="What is your favorite color?"),
        _context(tmp_path, _answer),
    )
    assert result.output == "green"


def test_single_option_is_rejected():
    with pytest.raises(ValidationError, match="at least two"):
        AskUserQuestionToolInput(question="选一个", options=[{"label": "唯一选项"}])


def test_duplicate_and_blank_options_are_cleaned():
    arguments = AskUserQuestionToolInput(
        question="选一个",
        options=[
            {"label": " 方案 A "},
            {"label": "方案 A"},
            {"label": "   "},
            {"label": "方案 B", "description": "  第二行\n 说明  "},
        ],
    )
    assert [option.label for option in arguments.options] == ["方案 A", "方案 B"]
    assert arguments.options[1].description == "第二行 说明"


def test_missing_label_is_recovered_from_description():
    """弱模型只给 description 时，用描述生成一个可读 label，避免整次调用失败。"""
    arguments = AskUserQuestionToolInput(
        question="选一个",
        options=[
            {"description": "只做总量与结构占比，快速出结果"},
            {"label": "结构 + 因素分解", "description": "更深入的分析"},
        ],
    )
    assert arguments.options[0].label == "只做总量与结构占比，快速出结果"
    assert arguments.options[0].description == "只做总量与结构占比，快速出结果"


def test_schema_is_flattened_for_compatible_providers():
    schema = AskUserQuestionTool().to_api_schema()
    input_schema = schema["input_schema"]
    assert "options" in input_schema["properties"]
    option_schema = input_schema["properties"]["options"]
    assert option_schema["maxItems"] == 6
    assert option_schema["items"]["required"] == ["label"]
    assert "label" in option_schema["items"]["properties"]
    # 嵌套 Pydantic 模型常见的 $defs/$ref 不应出现在 provider schema 里。
    assert "$defs" not in input_schema


@pytest.mark.asyncio
async def test_missing_prompt_is_reported(tmp_path: Path):
    tool = AskUserQuestionTool()
    result = await tool.execute(
        tool.input_model(question="hello"),
        ToolExecutionContext(cwd=tmp_path),
    )
    assert result.is_error is True
    assert "unavailable" in result.output
