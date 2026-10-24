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
    assert {"database-query", "business-analysis", "multi-agent-orchestration"} <= names
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
    assert "五阶段" in result.output
    assert "外部信息检索" in result.output
    assert "节假日" in result.output
    assert "气象与气候" in result.output
    assert "事实台账" in result.output
    assert "业务分析" in result.output
