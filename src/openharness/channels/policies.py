"""Channel policy normalization shared by the core and the ohmo gateway."""

from __future__ import annotations

__all__ = ["normalize_feishu_group_policy"]

_FEISHU_GROUP_POLICIES = {"open", "mention", "managed", "managed_or_mention"}

_FEISHU_GROUP_POLICY_ALIASES = {
    "all": "open",
    "always": "open",
    "always_reply": "open",
    "managed_mention": "managed_or_mention",
    "managed_or_at": "managed_or_mention",
    "at": "mention",
    "mentions": "mention",
}


def normalize_feishu_group_policy(value: str | None) -> str:
    """Normalize a configured Feishu group policy to a canonical value."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    normalized = _FEISHU_GROUP_POLICY_ALIASES.get(normalized, normalized)
    if normalized in _FEISHU_GROUP_POLICIES:
        return normalized
    return "managed_or_mention"
