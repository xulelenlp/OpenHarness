"""Fail on broken relative links in the hand-written documentation."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def _markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "qaapp" / "README.md"]
    files.extend(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if "autopilot" not in path.parts  # generated dashboard output
    )
    files.extend((ROOT / "plugins").rglob("README.md"))
    return sorted({path for path in files if path.is_file()})


def _relative_targets(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    in_fence = False
    targets: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for raw in _LINK.findall(line):
            target = raw.strip().strip("<>").split(" ", 1)[0]
            if not target or target.startswith(_SKIP_PREFIXES):
                continue
            target = target.split("#", 1)[0]
            if target:
                targets.append(target)
    return targets


def test_relative_documentation_links_resolve():
    broken: list[str] = []
    for path in _markdown_files():
        for target in _relative_targets(path):
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not broken, "broken documentation links:\n" + "\n".join(broken)
