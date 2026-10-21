"""Keep runtime scenario keys and the business-analysis skill in sync."""

from __future__ import annotations

import re
from pathlib import Path

from qaapp.agents.scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parents[2]


def _skill_scenario_keys() -> set[str]:
    text = (ROOT / "skills" / "business-analysis" / "SKILL.md").read_text(encoding="utf-8")
    keys: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"scenario", "---"} or set(cells[0]) <= {"-", ":"}:
            continue
        if re.fullmatch(r"[a-z][a-z0-9_-]*", cells[0]):
            keys.add(cells[0])
    return keys


def test_skill_table_covers_runtime_scenarios():
    assert _skill_scenario_keys() == set(SCENARIOS), (
        "skills/business-analysis/SKILL.md 的场景表与 qaapp.agents.scenarios.SCENARIOS 不一致"
    )
