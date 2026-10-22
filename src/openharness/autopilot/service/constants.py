"""Default autopilot, verification, and release policy constants."""

from __future__ import annotations

from openharness.autopilot.types import RepoTaskSource

_SOURCE_BASE_SCORES: dict[RepoTaskSource, int] = {
    "ohmo_request": 100,
    "manual_idea": 80,
    "github_issue": 75,
    "github_pr": 85,
    "claude_code_candidate": 45,
}
_BUG_HINTS = ("bug", "fix", "failure", "broken", "regression", "crash", "error", "issue")
_URGENT_HINTS = ("urgent", "p0", "p1", "high", "critical", "blocker")

_DEFAULT_AUTOPILOT_POLICY = {
    "intake": {
        "mode": "unified_queue",
        "max_visible_candidates": 12,
        "dedupe_strategy": "source_ref_then_fingerprint",
    },
    "decision": {
        "default_human_gate": True,
        "prefer_small_safe_steps": True,
    },
    "execution": {
        "default_model": "",
        "max_turns": 12,
        "permission_mode": "full_auto",
        "host_mode": "self_hosted",
        "use_worktree": True,
        "base_branch": "main",
        "max_attempts": 3,
    },
    "github": {
        "issue_comment_style": "bilingual",
        "pr_branch_prefix": "autopilot/",
        "ci_poll_interval_seconds": 20,
        "ci_timeout_seconds": 1800,
        "no_checks_grace_seconds": 60,
        "checks_settle_seconds": 20,
        "auto_merge": {
            "mode": "label_gated",
            "required_label": "autopilot:merge",
        },
    },
    "repair": {
        "max_rounds": 2,
        "retry_on": ["local_verification_failed", "remote_ci_failed"],
        "stop_on": ["agent_runtime_error", "git_error", "permission_error", "merge_conflict"],
    },
}
_DEFAULT_VERIFICATION_POLICY = {
    "gates": [
        "fast_gate",
        "repo_gate",
        "harness_gate",
    ],
    "commands": [
        "uv run pytest -q",
        "uv run ruff check src tests scripts",
        {
            "command": (
                "cd frontend/terminal && "
                "([ -x ./node_modules/.bin/tsc ] || npm ci --no-audit --no-fund) && "
                "./node_modules/.bin/tsc --noEmit"
            ),
            "shell": True,
        },
    ],
    "require_tests_before_merge": True,
}
_DEFAULT_RELEASE_POLICY = {
    "merge_requires_human": True,
    "release_requires_human": True,
    "auto_revert_on_failed_verification": False,
}
