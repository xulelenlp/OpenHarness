"""Installer regressions for Windows command aliases."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


def test_pyproject_exposes_abc_console_scripts():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["abc"] == "openharness.cli:app"
    assert scripts["abcag"] == "ohmo.cli:app"


def test_legacy_console_scripts_removed():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    for legacy in ("openharness", "oh", "openh", "ohmo"):
        assert legacy not in scripts


def test_powershell_installer_recommends_abc_for_windows():
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")
    assert "abc.exe" in script
    assert "Launch (PowerShell):     abc" in script
    assert "Launch personal agent:   abcag" in script
    # The legacy 'oh' alias collided with PowerShell's Out-Host alias; 'abc' does not.
    assert "Out-Host" not in script


def test_powershell_installer_falls_back_when_abc_exe_missing():
    """When ``abc.exe`` is absent from the venv, the installer must still guide
    the user to run via ``python -m openharness`` instead of a missing binary."""
    script = Path("scripts/install.ps1").read_text(encoding="utf-8")
    assert "python -m openharness" in script
