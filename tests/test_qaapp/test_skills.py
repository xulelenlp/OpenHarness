"""项目技能目录测试：技能统一放在仓库根 skills/，随 git 维护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.skills.loader import load_skills_from_dirs
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_tool import SkillTool, SkillToolInput


def test_project_skills_live_in_tracked_root_folder():
    root = Path(__file__).resolve().parents[2]
    skills = load_skills_from_dirs([root / "skills"], create_missing=False)
    names = {skill.name for skill in skills}
    assert {
        "database-query",
        "business-analysis",
        "document-analysis",
        "multi-agent-orchestration",
    } <= names
    for skill in skills:
        path = Path(skill.path or "")
        assert "skills" in path.parts
        assert ".openharness" not in path.parts
    old = root / ".openharness" / "skills"
    assert not any(old.glob("*/SKILL.md")) if old.exists() else True


@pytest.mark.asyncio
async def test_skill_tool_loads_from_tracked_folder():
    root = Path(__file__).resolve().parents[2]
    context = ToolExecutionContext(
        cwd=root,
        metadata={"extra_skill_dirs": (str(root / "skills"),)},
    )
    result = await SkillTool().execute(SkillToolInput(name="business-analysis"), context)
    assert result.is_error is False
    assert "六步" in result.output
    assert "公网信息" in result.output
    assert "国外政策事件" in result.output
    assert "影响因素筛选" in result.output
    assert "不确定的不写" in result.output
    assert "国网公文" in result.output
    assert "业务分析" in result.output
    assert "开场对齐" in result.output
    assert "任务简报" in result.output
    assert "不要重复问" in result.output
    assert "结构框架" in result.output
    assert "不要每份报告都套用" in result.output


@pytest.mark.asyncio
async def test_document_analysis_skill_covers_uploaded_files_and_guardrails():
    root = Path(__file__).resolve().parents[2]
    context = ToolExecutionContext(
        cwd=root,
        metadata={"extra_skill_dirs": (str(root / "skills"),)},
    )
    result = await SkillTool().execute(SkillToolInput(name="document-analysis"), context)
    assert result.is_error is False
    assert "Excel" in result.output
    assert "Word" in result.output
    assert "TXT" in result.output
    assert "开工前对齐" in result.output
    assert "核验" in result.output
    assert "提示注入" in result.output
    assert "不要只读附件预览" in result.output


@pytest.mark.asyncio
async def test_database_query_skill_covers_metric_caliber_questions():
    root = Path(__file__).resolve().parents[2]
    context = ToolExecutionContext(
        cwd=root,
        metadata={"extra_skill_dirs": (str(root / "skills"),)},
    )
    result = await SkillTool().execute(SkillToolInput(name="database-query"), context)
    assert result.is_error is False
    assert "指标口径问答" in result.output
    assert "describe" in result.output
    assert "ask_user_question" in result.output
    assert "candidates" in result.output
