"""Registry, journal, queue, policy, and scan behavior for RepoAutopilotStore."""

from __future__ import annotations

import json
import subprocess
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from openharness.autopilot.service.constants import (
    _BUG_HINTS,
    _DEFAULT_AUTOPILOT_POLICY,
    _DEFAULT_RELEASE_POLICY,
    _DEFAULT_VERIFICATION_POLICY,
    _SOURCE_BASE_SCORES,
    _URGENT_HINTS,
)
from openharness.autopilot.service.helpers import _json_default, _safe_text, _shorten
from openharness.autopilot.types import (
    RepoAutopilotRegistry,
    RepoJournalEntry,
    RepoTaskCard,
    RepoTaskSource,
    RepoTaskStatus,
)
from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_autopilot_policy_path,
    get_project_autopilot_registry_path,
    get_project_autopilot_runs_dir,
    get_project_release_policy_path,
    get_project_repo_journal_path,
    get_project_verification_policy_path,
)
from openharness.utils.fs import atomic_write_text


class StoreMixin:
    """Registry, journal, queue, policy, and scan methods."""

    def __init__(self, cwd: str | Path) -> None:
        self._cwd = Path(cwd).resolve()
        self._registry_path = get_project_autopilot_registry_path(self._cwd)
        self._journal_path = get_project_repo_journal_path(self._cwd)
        self._context_path = get_project_active_repo_context_path(self._cwd)
        self._runs_dir = get_project_autopilot_runs_dir(self._cwd)
        self._ensure_layout()

    @property
    def registry_path(self) -> Path:
        return self._registry_path

    @property
    def journal_path(self) -> Path:
        return self._journal_path

    @property
    def context_path(self) -> Path:
        return self._context_path

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def list_cards(self, *, status: RepoTaskStatus | None = None) -> list[RepoTaskCard]:
        cards = self._load_registry().cards
        if status is not None:
            cards = [card for card in cards if card.status == status]
        return sorted(cards, key=lambda card: (-card.score, -card.updated_at, card.title.lower()))

    def get_card(self, card_id: str) -> RepoTaskCard | None:
        for card in self._load_registry().cards:
            if card.id == card_id:
                return card
        return None

    def enqueue_card(
        self,
        *,
        source_kind: RepoTaskSource,
        title: str,
        body: str = "",
        source_ref: str = "",
        labels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[RepoTaskCard, bool]:
        registry = self._load_registry()
        now = time.time()
        normalized_title = title.strip()
        normalized_body = body.strip()
        normalized_ref = source_ref.strip()
        fingerprint = self._build_fingerprint(
            source_kind=source_kind,
            source_ref=normalized_ref,
            title=normalized_title,
            body=normalized_body,
        )
        existing = next((card for card in registry.cards if card.fingerprint == fingerprint), None)
        merged_labels = self._normalize_labels(labels)
        merged_metadata = dict(metadata or {})
        if existing is not None:
            if normalized_title:
                existing.title = normalized_title
            if normalized_body:
                existing.body = normalized_body
            if normalized_ref:
                existing.source_ref = normalized_ref
            existing.labels = self._merge_labels(existing.labels, merged_labels)
            existing.metadata.update(merged_metadata)
            existing.updated_at = now
            existing.score, existing.score_reasons = self._score_card(existing)
            self._save_registry(registry)
            self.append_journal(
                kind="intake_refresh",
                summary=f"Refreshed intake card {existing.id}: {existing.title}",
                task_id=existing.id,
                metadata={"source_kind": existing.source_kind, "source_ref": existing.source_ref},
            )
            self.rebuild_active_context()
            return existing, False

        card = RepoTaskCard(
            id=f"ap-{uuid4().hex[:8]}",
            fingerprint=fingerprint,
            title=normalized_title or "Untitled intake item",
            body=normalized_body,
            source_kind=source_kind,
            source_ref=normalized_ref,
            labels=merged_labels,
            metadata=merged_metadata,
            created_at=now,
            updated_at=now,
        )
        card.score, card.score_reasons = self._score_card(card)
        registry.cards.append(card)
        self._save_registry(registry)
        self.append_journal(
            kind="intake_added",
            summary=f"Queued {card.source_kind}: {card.title}",
            task_id=card.id,
            metadata={"source_ref": card.source_ref, "score": card.score},
        )
        self.rebuild_active_context()
        return card, True

    def pick_next_card(self) -> RepoTaskCard | None:
        queued = [card for card in self._load_registry().cards if card.status == "queued"]
        if not queued:
            return None
        return sorted(queued, key=lambda card: (-card.score, -card.updated_at, card.title.lower()))[0]

    def update_status(
        self,
        card_id: str,
        *,
        status: RepoTaskStatus,
        note: str | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> RepoTaskCard:
        registry = self._load_registry()
        card = next((item for item in registry.cards if item.id == card_id), None)
        if card is None:
            raise ValueError(f"No autopilot card found with ID: {card_id}")
        card.status = status
        card.updated_at = time.time()
        if note:
            card.metadata["last_note"] = note.strip()
        if metadata_updates:
            card.metadata.update(metadata_updates)
        card.score, card.score_reasons = self._score_card(card)
        self._save_registry(registry)
        summary = f"{status}: {card.title}"
        if note:
            summary = f"{summary} ({_shorten(note, limit=80)})"
        self.append_journal(kind=f"status_{status}", summary=summary, task_id=card.id)
        self.rebuild_active_context()
        return card

    def load_journal(self, *, limit: int = 12) -> list[RepoJournalEntry]:
        if not self._journal_path.exists():
            return []
        entries: list[RepoJournalEntry] = []
        for line in self._journal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(RepoJournalEntry.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return entries[-limit:]

    def append_journal(
        self,
        *,
        kind: str,
        summary: str,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RepoJournalEntry:
        entry = RepoJournalEntry(
            timestamp=time.time(),
            kind=kind,
            summary=summary.strip(),
            task_id=task_id,
            metadata=metadata or {},
        )
        with self._journal_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
        return entry

    def load_active_context(self) -> str:
        if not self._context_path.exists():
            return ""
        return self._context_path.read_text(encoding="utf-8", errors="replace").strip()

    def rebuild_active_context(self) -> str:
        cards = self._load_registry().cards
        running = [card for card in cards if card.status in {"preparing", "running", "verifying", "waiting_ci", "repairing"}]
        accepted = [card for card in cards if card.status in {"accepted", "pr_open"}]
        queued = [card for card in cards if card.status == "queued"]
        completed = [card for card in cards if card.status in {"completed", "merged"}]
        failed = [card for card in cards if card.status in {"failed", "rejected"}]
        focus = None
        for group in (running, accepted, queued):
            if group:
                focus = sorted(
                    group,
                    key=lambda card: (-card.score, -card.updated_at, card.title.lower()),
                )[0]
                break

        lines = [
            "# Active Repo Context",
            "",
            f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            "",
            "## Current Task Focus",
        ]
        if focus is None:
            lines.append("- No active repo task focus yet.")
        else:
            lines.append(
                f"- [{focus.status}] {focus.title} ({focus.source_kind}, score={focus.score})"
            )
            if focus.body:
                lines.append(f"- Detail: {_shorten(focus.body, limit=220)}")

        lines.extend(["", "## In Progress"])
        for card in sorted(running + accepted, key=lambda item: (-item.score, -item.updated_at))[:6]:
            lines.append(f"- [{card.status}] {card.id} {card.title} ({card.source_kind})")
        if not running and not accepted:
            lines.append("- None.")

        lines.extend(["", "## Next Up"])
        for card in sorted(queued, key=lambda item: (-item.score, -item.updated_at))[:8]:
            lines.append(f"- [{card.score}] {card.id} {card.title} ({card.source_kind})")
        if not queued:
            lines.append("- No queued items.")

        lines.extend(["", "## Recently Completed"])
        for card in sorted(completed, key=lambda item: item.updated_at, reverse=True)[:5]:
            lines.append(f"- {card.id} {card.title}")
        if not completed:
            lines.append("- None yet.")

        lines.extend(["", "## Recent Failures"])
        for card in sorted(failed, key=lambda item: item.updated_at, reverse=True)[:5]:
            lines.append(f"- [{card.status}] {card.id} {card.title}")
        if not failed:
            lines.append("- None.")

        lines.extend(["", "## Recent Repo Journal"])
        journal = self.load_journal(limit=8)
        if journal:
            for entry in journal:
                lines.append(
                    f"- {time.strftime('%m-%d %H:%M', time.gmtime(entry.timestamp))} "
                    f"{entry.kind}: {entry.summary}"
                )
        else:
            lines.append("- Journal is empty.")

        lines.extend(
            [
                "",
                "## Policies",
                f"- Autopilot: {get_project_autopilot_policy_path(self._cwd)}",
                f"- Verification: {get_project_verification_policy_path(self._cwd)}",
                f"- Release: {get_project_release_policy_path(self._cwd)}",
            ]
        )
        content = "\n".join(lines).strip() + "\n"
        atomic_write_text(self._context_path, content)
        self.export_dashboard()
        return content

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for card in self._load_registry().cards:
            counts[card.status] = counts.get(card.status, 0) + 1
        return counts

    def load_policies(self) -> dict[str, Any]:
        return {
            "autopilot": self._read_yaml(get_project_autopilot_policy_path(self._cwd), _DEFAULT_AUTOPILOT_POLICY),
            "verification": self._read_yaml(
                get_project_verification_policy_path(self._cwd),
                _DEFAULT_VERIFICATION_POLICY,
            ),
            "release": self._read_yaml(get_project_release_policy_path(self._cwd), _DEFAULT_RELEASE_POLICY),
        }

    def scan_github_issues(self, *, limit: int = 10) -> list[RepoTaskCard]:
        raw = self._run_gh_json(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,labels,updatedAt,url",
            ]
        )
        cards: list[RepoTaskCard] = []
        for item in raw:
            number = item.get("number")
            if number is None:
                continue
            labels = [str(label.get("name", "")).strip() for label in item.get("labels", [])]
            card, _ = self.enqueue_card(
                source_kind="github_issue",
                source_ref=f"issue:{number}",
                title=f"GitHub issue #{number}: {_safe_text(item.get('title'))}",
                body=_safe_text(item.get("body")),
                labels=[label for label in labels if label],
                metadata={
                    "url": _safe_text(item.get("url")),
                    "updated_at_remote": _safe_text(item.get("updatedAt")),
                },
            )
            cards.append(card)
        return cards

    def scan_github_prs(self, *, limit: int = 10) -> list[RepoTaskCard]:
        raw = self._run_gh_json(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,body,isDraft,reviewDecision,mergeStateStatus,updatedAt,url,labels,headRefName,baseRefName",
            ]
        )
        cards: list[RepoTaskCard] = []
        for item in raw:
            number = item.get("number")
            if number is None:
                continue
            labels = [str(label.get("name", "")).strip() for label in item.get("labels", [])]
            card, _ = self.enqueue_card(
                source_kind="github_pr",
                source_ref=f"pr:{number}",
                title=f"GitHub PR #{number}: {_safe_text(item.get('title'))}",
                body=_safe_text(item.get("body")),
                labels=[label for label in labels if label],
                metadata={
                    "url": _safe_text(item.get("url")),
                    "updated_at_remote": _safe_text(item.get("updatedAt")),
                    "is_draft": bool(item.get("isDraft")),
                    "review_decision": _safe_text(item.get("reviewDecision")),
                    "merge_state_status": _safe_text(item.get("mergeStateStatus")),
                    "head_ref_name": _safe_text(item.get("headRefName")),
                    "base_ref_name": _safe_text(item.get("baseRefName")),
                },
            )
            cards.append(card)
        return cards

    def scan_claude_code_candidates(
        self,
        *,
        limit: int = 10,
        root: str | Path | None = None,
    ) -> list[RepoTaskCard]:
        candidate_root = Path(root or Path.home() / "claude-code").expanduser().resolve()
        if not candidate_root.exists():
            raise ValueError(f"claude-code root not found: {candidate_root}")
        discovered: list[tuple[str, Path]] = []
        for dirname, label in (("commands", "command"), ("agents", "agent")):
            base = candidate_root / dirname
            if not base.exists():
                continue
            for path in sorted(base.iterdir(), key=lambda item: item.name.lower()):
                if path.name.startswith("."):
                    continue
                discovered.append((label, path))
        cards: list[RepoTaskCard] = []
        for label, path in discovered[:limit]:
            name = path.stem if path.is_file() else path.name
            card, _ = self.enqueue_card(
                source_kind="claude_code_candidate",
                source_ref=f"{label}:{path}",
                title=f"Evaluate claude-code {label}: {name}",
                body=(
                    f"Borrow candidate from {path}. "
                    "Review whether this should be aligned, adapted, or ignored for OpenHarness."
                ),
                metadata={"path": str(path)},
            )
            cards.append(card)
        return cards

    def scan_all_sources(self, *, issue_limit: int = 10, pr_limit: int = 10) -> dict[str, int]:
        counts = {"github_issue": 0, "github_pr": 0, "claude_code_candidate": 0}
        try:
            counts["github_issue"] = len(self.scan_github_issues(limit=issue_limit))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"GitHub issue scan failed: {exc}")
        try:
            counts["github_pr"] = len(self.scan_github_prs(limit=pr_limit))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"GitHub PR scan failed: {exc}")
        try:
            counts["claude_code_candidate"] = len(self.scan_claude_code_candidates(limit=8))
        except Exception as exc:
            self.append_journal(kind="scan_warning", summary=f"claude-code scan failed: {exc}")
        self.append_journal(kind="scan_all", summary=f"Scanned sources: {counts}")
        self.rebuild_active_context()
        return counts

    def _max_attempts(self, policies: dict[str, Any]) -> int:
        execution = dict(policies.get("autopilot", {}).get("execution", {}))
        repair = dict(policies.get("autopilot", {}).get("repair", {}))
        execution_attempts = int(execution.get("max_attempts", 3) or 3)
        repair_rounds = int(repair.get("max_rounds", 2) or 2)
        return max(execution_attempts, repair_rounds + 1, 1)

    def _base_branch(self, policies: dict[str, Any]) -> str:
        execution = dict(policies.get("autopilot", {}).get("execution", {}))
        return _safe_text(execution.get("base_branch")) or "main"

    def _head_branch(self, card: RepoTaskCard, policies: dict[str, Any]) -> str:
        github_policy = dict(policies.get("autopilot", {}).get("github", {}))
        prefix = _safe_text(github_policy.get("pr_branch_prefix")) or "autopilot/"
        return f"{prefix}{card.id}"

    def _worktree_slug(self, card: RepoTaskCard) -> str:
        return f"autopilot/{card.id}"

    def _ensure_layout(self) -> None:
        for path, payload in (
            (get_project_autopilot_policy_path(self._cwd), _DEFAULT_AUTOPILOT_POLICY),
            (get_project_verification_policy_path(self._cwd), _DEFAULT_VERIFICATION_POLICY),
            (get_project_release_policy_path(self._cwd), _DEFAULT_RELEASE_POLICY),
        ):
            if not path.exists():
                atomic_write_text(path, yaml.safe_dump(payload, sort_keys=False))
        if not self._registry_path.exists():
            self._save_registry(RepoAutopilotRegistry(updated_at=time.time(), cards=[]))
        if not self._context_path.exists():
            self.rebuild_active_context()

    def _load_registry(self) -> RepoAutopilotRegistry:
        if not self._registry_path.exists():
            return RepoAutopilotRegistry(updated_at=time.time(), cards=[])
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return RepoAutopilotRegistry(updated_at=time.time(), cards=[])
        return RepoAutopilotRegistry.model_validate(payload)

    def _save_registry(self, registry: RepoAutopilotRegistry) -> None:
        registry.updated_at = time.time()
        atomic_write_text(
            self._registry_path,
            json.dumps(
                registry.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
            + "\n",
        )

    def _build_fingerprint(
        self,
        *,
        source_kind: RepoTaskSource,
        source_ref: str,
        title: str,
        body: str,
    ) -> str:
        basis = source_ref.strip() or f"{title.strip()}\n{body.strip()}"
        digest = sha1(basis.encode("utf-8")).hexdigest()[:16]
        return f"{source_kind}:{digest}"

    def _score_card(self, card: RepoTaskCard) -> tuple[int, list[str]]:
        score = _SOURCE_BASE_SCORES.get(card.source_kind, 50)
        reasons = [f"source={card.source_kind}"]
        text = f"{card.title}\n{card.body}".lower()
        labels = {label.lower() for label in card.labels}
        if card.source_kind == "github_issue":
            if labels.intersection({"bug", "regression", "failure"}):
                score += 25
                reasons.append("bug-labelled issue")
            if any(hint in text for hint in _BUG_HINTS):
                score += 15
                reasons.append("issue looks like a bug/regression")
        if card.source_kind == "github_pr":
            if bool(card.metadata.get("is_draft")):
                score -= 30
                reasons.append("draft pr")
            if str(card.metadata.get("merge_state_status", "")).upper() == "CLEAN":
                score += 20
                reasons.append("clean merge state")
            if str(card.metadata.get("review_decision", "")).upper() == "APPROVED":
                score += 20
                reasons.append("approved review state")
        if card.source_kind in {"ohmo_request", "manual_idea"}:
            score += 10
            reasons.append("direct user-driven input")
        if any(hint in text for hint in _URGENT_HINTS) or labels.intersection(
            {"urgent", "p0", "p1", "high", "critical", "blocker"}
        ):
            score += 20
            reasons.append("urgent signals")
        age_days = max(0.0, (time.time() - card.updated_at) / 86400.0)
        freshness_bonus = max(0, 10 - int(age_days))
        if freshness_bonus:
            score += freshness_bonus
            reasons.append("recently updated")
        return score, reasons

    def _normalize_labels(self, labels: list[str] | None) -> list[str]:
        if not labels:
            return []
        return sorted({label.strip() for label in labels if label and label.strip()})

    def _merge_labels(self, existing: list[str], incoming: list[str]) -> list[str]:
        return sorted({*existing, *incoming})

    def _run_gh_json(self, command: list[str]) -> list[dict[str, Any]]:
        try:
            completed = subprocess.run(
                command,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ValueError("gh CLI is not installed.") from exc
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout).strip() or "gh command failed"
            raise ValueError(error)
        raw = (completed.stdout or "").strip()
        if not raw:
            return []
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("Expected gh JSON array output.")
        return [item for item in payload if isinstance(item, dict)]

    def _read_yaml(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return dict(default)
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return dict(default)
        if not isinstance(payload, dict):
            return dict(default)
        return payload
