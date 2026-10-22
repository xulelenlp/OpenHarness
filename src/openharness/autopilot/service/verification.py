"""Verification command execution and reporting for RepoAutopilotStore."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from openharness.autopilot.service.helpers import (
    _parse_verification_entry,
    _safe_text,
    _VerificationCommand,
)
from openharness.autopilot.types import RepoTaskCard, RepoVerificationStep


def _service_looks_available(command: str, cwd: Path) -> bool:
    """Resolve ``_looks_available`` through the service package.

    Tests monkeypatch ``openharness.autopilot.service._looks_available``; going
    through the package module keeps that patch effective after the split.
    """

    import openharness.autopilot.service as service_module

    return service_module._looks_available(command, cwd)


class VerificationMixin:
    """Verification command execution and report rendering methods."""

    def _verification_commands(self, policies: dict[str, Any]) -> list[_VerificationCommand]:
        configured = policies.get("verification", {}).get("commands", [])
        parsed = [_parse_verification_entry(entry) for entry in configured]
        selected: list[_VerificationCommand] = []
        for cmd in parsed:
            if cmd.error is not None:
                selected.append(cmd)
                continue
            if _service_looks_available(cmd.raw, self._cwd):
                selected.append(cmd)
        return selected

    def _run_verification_steps(self, policies: dict[str, Any], *, cwd: Path | None = None) -> list[RepoVerificationStep]:
        steps: list[RepoVerificationStep] = []
        for cmd in self._verification_commands(policies):
            if cmd.error is not None:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=f"verification policy error: {cmd.error}",
                    )
                )
                continue
            target: str | list[str] = cmd.raw if cmd.shell else list(cmd.argv)
            try:
                completed = subprocess.run(
                    target,
                    cwd=cwd or self._cwd,
                    shell=cmd.shell,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=1800,
                )
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=completed.returncode,
                        status="success" if completed.returncode == 0 else "failed",
                        stdout=(completed.stdout or "")[-4000:],
                        stderr=(completed.stderr or "")[-4000:],
                    )
                )
            except FileNotFoundError as exc:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=f"executable not found: {exc}",
                    )
                )
            except subprocess.TimeoutExpired as exc:
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stdout=_safe_text(getattr(exc, "stdout", ""))[-4000:],
                        stderr=f"Timed out after {exc.timeout}s",
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                steps.append(
                    RepoVerificationStep(
                        command=cmd.raw,
                        returncode=-1,
                        status="error",
                        stderr=str(exc),
                    )
                )
        return steps

    def _render_verification_report(
        self,
        card: RepoTaskCard,
        steps: list[RepoVerificationStep],
    ) -> str:
        lines = [
            f"# Verification Report: {card.id}",
            "",
            f"Title: {card.title}",
            "",
        ]
        if not steps:
            lines.append("No verification commands were applicable.")
            return "\n".join(lines).strip() + "\n"
        for step in steps:
            lines.extend(
                [
                    f"## {step.status.upper()} :: {step.command}",
                    "",
                    f"Return code: {step.returncode}",
                    "",
                ]
            )
            if step.stdout:
                lines.extend(["### stdout", "```text", step.stdout, "```", ""])
            if step.stderr:
                lines.extend(["### stderr", "```text", step.stderr, "```", ""])
        return "\n".join(lines).strip() + "\n"

    def _render_run_report(
        self,
        card: RepoTaskCard,
        *,
        agent_summary: str,
        verification_steps: list[RepoVerificationStep],
        verification_status: str,
    ) -> str:
        lines = [
            f"# Autopilot Run Report: {card.id}",
            "",
            f"Title: {card.title}",
            f"Source: {card.source_kind}",
            f"Source ref: {card.source_ref or '-'}",
            "",
            "## Agent Self-Reported Summary",
            "",
            agent_summary.strip() or "(empty agent summary)",
            "",
            "## Service-Level Ground Truth",
            "",
            (
                "The section above is the model's own summary. "
                "Treat it as untrusted until the service-level verification results below finish."
            ),
            "",
        ]

        if verification_status == "not_started":
            lines.extend(
                [
                    "- Verification status: not started.",
                    "- The agent run itself failed before service-level verification could begin.",
                ]
            )
        elif verification_status == "pending":
            lines.extend(
                [
                    "- Verification status: pending.",
                    "- Service-level verification has not finished yet.",
                ]
            )
        else:
            overall = "passed" if verification_status == "passed" else "failed"
            lines.append(f"- Verification status: {overall}.")
            if verification_steps:
                for step in verification_steps:
                    lines.append(
                        f"- [{step.status}] `{step.command}` (rc={step.returncode})"
                    )
            else:
                lines.append("- No verification commands were applicable.")

        return "\n".join(lines).strip() + "\n"
