"""Guard the intended layering between the core harness and its apps."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Core modules may import ohmo only lazily at integration points.
_ALLOWED_CORE_TO_OHMO = {
    ("src/openharness/services/cron_scheduler.py", "ohmo.gateway.config"),
    ("src/openharness/services/cron_scheduler.py", "ohmo.gateway.notify"),
    ("src/openharness/channels/impl/feishu.py", "ohmo.group_registry"),
}


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.imports: list[tuple[str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name, self.depth))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append((node.module, self.depth))


def _imports(path: Path) -> list[tuple[str, int]]:
    collector = _ImportCollector()
    collector.visit(ast.parse(path.read_text(encoding="utf-8")))
    return collector.imports


def _python_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def test_core_does_not_import_qaapp():
    offenders = []
    for path in _python_files(REPO_ROOT / "src" / "openharness"):
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}: {module}"
            for module, _depth in _imports(path)
            if module == "qaapp" or module.startswith("qaapp.")
        )
    assert not offenders, "core must stay independent of qaapp: " + ", ".join(offenders)


def test_ohmo_does_not_import_qaapp():
    offenders = []
    for path in _python_files(REPO_ROOT / "ohmo"):
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)}: {module}"
            for module, _depth in _imports(path)
            if module == "qaapp" or module.startswith("qaapp.")
        )
    assert not offenders, "ohmo must stay independent of qaapp: " + ", ".join(offenders)


def test_core_to_ohmo_imports_are_lazy_and_allowlisted():
    offenders = []
    for path in _python_files(REPO_ROOT / "src" / "openharness"):
        rel = str(path.relative_to(REPO_ROOT))
        for module, depth in _imports(path):
            if not (module == "ohmo" or module.startswith("ohmo.")):
                continue
            if depth == 0:
                offenders.append(f"{rel}: top-level import of {module}")
            if (rel, module) not in _ALLOWED_CORE_TO_OHMO:
                offenders.append(f"{rel}: unexpected import of {module}")
    assert not offenders, "core/ohmo crossing is too tight: " + "; ".join(offenders)
