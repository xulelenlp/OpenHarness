"""Provider profile catalog and auth-source helpers."""

from __future__ import annotations

import os

from openharness.config.settings_models import ProviderProfile
from openharness.permissions.modes import PermissionMode

CLAUDE_MODEL_ALIAS_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("default", "Default", "Recommended model for this profile"),
    ("best", "Best", "Most capable available model"),
    ("sonnet", "Sonnet", "Latest Sonnet for everyday coding"),
    ("opus", "Opus", "Latest Opus for complex reasoning"),
    ("haiku", "Haiku", "Fastest Claude model"),
    ("sonnet[1m]", "Sonnet (1M context)", "Latest Sonnet with 1M context"),
    ("opus[1m]", "Opus (1M context)", "Latest Opus with 1M context"),
    ("opusplan", "Opus Plan Mode", "Use Opus in plan mode and Sonnet otherwise"),
)

_CLAUDE_ALIAS_TARGETS: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
    "sonnet[1m]": "claude-sonnet-4-6[1m]",
    "opus[1m]": "claude-opus-4-6[1m]",
}


def normalize_anthropic_model_name(model: str) -> str:
    """Normalize an Anthropic model name the same way Hermes does.

    - Strips the ``anthropic/`` prefix when present.
    - Converts dotted Claude version separators to Anthropic's hyphenated form.
    """
    normalized = model.strip()
    lower = normalized.lower()
    if lower.startswith("anthropic/"):
        normalized = normalized[len("anthropic/"):]
        lower = normalized.lower()
    if lower.startswith("claude-"):
        return normalized.replace(".", "-")
    return normalized


def default_provider_profiles() -> dict[str, ProviderProfile]:
    """Return the built-in provider workflow catalog."""
    return {
        "claude-api": ProviderProfile(
            label="Anthropic-Compatible API",
            provider="anthropic",
            api_format="anthropic",
            auth_source="anthropic_api_key",
            default_model="claude-sonnet-4-6",
        ),
        "claude-subscription": ProviderProfile(
            label="Claude Subscription",
            provider="anthropic_claude",
            api_format="anthropic",
            auth_source="claude_subscription",
            default_model="claude-sonnet-4-6",
        ),
        "openai-compatible": ProviderProfile(
            label="OpenAI-Compatible API",
            provider="openai",
            api_format="openai",
            auth_source="openai_api_key",
            default_model="gpt-5.4",
        ),
        "codex": ProviderProfile(
            label="Codex Subscription",
            provider="openai_codex",
            api_format="openai",
            auth_source="codex_subscription",
            default_model="gpt-5.4",
        ),
        "copilot": ProviderProfile(
            label="GitHub Copilot",
            provider="copilot",
            api_format="copilot",
            auth_source="copilot_oauth",
            default_model="gpt-5.4",
        ),
        "moonshot": ProviderProfile(
            label="Moonshot (Kimi)",
            provider="moonshot",
            api_format="openai",
            auth_source="moonshot_api_key",
            default_model="kimi-k2.5",
            base_url="https://api.moonshot.cn/v1",
        ),
        "gemini": ProviderProfile(
            label="Google Gemini",
            provider="gemini",
            api_format="openai",
            auth_source="gemini_api_key",
            default_model="gemini-2.5-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        ),
        "minimax": ProviderProfile(
            label="MiniMax",
            provider="minimax",
            api_format="openai",
            auth_source="minimax_api_key",
            default_model="MiniMax-M2.7",
            base_url="https://api.minimax.io/v1",
        ),
        "nvidia": ProviderProfile(
            label="NVIDIA NIM",
            provider="nvidia",
            api_format="openai",
            auth_source="nvidia_api_key",
            default_model="openai/gpt-oss-120b",
            base_url="https://integrate.api.nvidia.com/v1",
        ),
        "qwen": ProviderProfile(
            label="Qwen (DashScope)",
            provider="dashscope",
            api_format="openai",
            auth_source="dashscope_api_key",
            default_model="qwen-plus",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        "modelscope": ProviderProfile(
            label="ModelScope",
            provider="modelscope",
            api_format="openai",
            auth_source="modelscope_api_key",
            default_model="deepseek-ai/DeepSeek-V4-Flash",
            base_url="https://api-inference.modelscope.cn/v1",
        ),
    }


def builtin_provider_profile_names() -> set[str]:
    """Return the names of built-in provider profiles."""
    return set(default_provider_profiles())


def display_label_for_profile(profile_name: str, profile: ProviderProfile) -> str:
    """Return the user-facing label for a profile.

    Built-in profiles always use the current built-in catalog label so old
    persisted settings don't keep stale wording in menus.
    """
    builtin = default_provider_profiles().get(profile_name)
    if builtin is not None:
        return builtin.label
    return profile.label


def is_claude_family_provider(provider: str) -> bool:
    """Return True when the provider is a Claude/Anthropic workflow."""
    return provider in {"anthropic", "anthropic_claude"}


def display_model_setting(profile: ProviderProfile) -> str:
    """Return the user-facing model setting for a profile."""
    configured = (profile.last_model or "").strip()
    if not configured and is_claude_family_provider(profile.provider):
        return "default"
    return configured or profile.default_model


def resolve_model_setting(
    model_setting: str,
    provider: str,
    *,
    default_model: str | None = None,
    permission_mode: str | None = None,
) -> str:
    """Resolve a user-facing model setting into the concrete runtime model ID."""
    configured = model_setting.strip()
    normalized = configured.lower()

    if not configured or normalized == "default":
        fallback = (default_model or "").strip()
        if fallback and fallback.lower() != "default":
            return resolve_model_setting(
                fallback,
                provider,
                default_model=None,
                permission_mode=permission_mode,
            )
        if is_claude_family_provider(provider):
            return _CLAUDE_ALIAS_TARGETS["sonnet"]
        return "gpt-5.4"

    if is_claude_family_provider(provider):
        if normalized == "best":
            return _CLAUDE_ALIAS_TARGETS["opus"]
        if normalized == "opusplan":
            if permission_mode == PermissionMode.PLAN.value:
                return _CLAUDE_ALIAS_TARGETS["opus"]
            return _CLAUDE_ALIAS_TARGETS["sonnet"]
        if normalized in _CLAUDE_ALIAS_TARGETS:
            return _CLAUDE_ALIAS_TARGETS[normalized]
        return normalize_anthropic_model_name(configured)

    if provider in {"openai", "openai_codex", "copilot"} and normalized in {"default", "best"}:
        return "gpt-5.4"

    return configured


def auth_source_provider_name(auth_source: str) -> str:
    """Map an auth source to the storage/runtime provider name."""
    mapping = {
        "anthropic_api_key": "anthropic",
        "openai_api_key": "openai",
        "codex_subscription": "openai_codex",
        "claude_subscription": "anthropic_claude",
        "copilot_oauth": "copilot",
        "dashscope_api_key": "dashscope",
        "bedrock_api_key": "bedrock",
        "vertex_api_key": "vertex",
        "moonshot_api_key": "moonshot",
        "gemini_api_key": "gemini",
        "minimax_api_key": "minimax",
        "nvidia_api_key": "nvidia",
        "modelscope_api_key": "modelscope",
    }
    return mapping.get(auth_source, auth_source)


def auth_source_uses_api_key(auth_source: str) -> bool:
    """Return True when the auth source is backed by a user-supplied API key."""
    return auth_source.endswith("_api_key")


def auth_source_env_var_candidates(auth_source: str) -> tuple[str, ...]:
    """Return env vars to probe for an auth source in precedence order."""
    mapping = {
        "anthropic_api_key": ("OPENHARNESS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        "openai_api_key": ("OPENHARNESS_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "dashscope_api_key": ("OPENHARNESS_DASHSCOPE_API_KEY", "DASHSCOPE_API_KEY"),
        "moonshot_api_key": ("OPENHARNESS_MOONSHOT_API_KEY", "MOONSHOT_API_KEY"),
        "gemini_api_key": ("OPENHARNESS_GEMINI_API_KEY", "GEMINI_API_KEY"),
        "minimax_api_key": ("OPENHARNESS_MINIMAX_API_KEY", "MINIMAX_API_KEY"),
        "nvidia_api_key": ("OPENHARNESS_NVIDIA_API_KEY", "NVIDIA_API_KEY"),
        "modelscope_api_key": ("OPENHARNESS_MODELSCOPE_API_KEY", "MODELSCOPE_API_KEY"),
    }
    return mapping.get(auth_source, ())


def resolve_auth_env_value(auth_source: str) -> tuple[str, str] | None:
    """Return the first configured env var/value pair for an auth source."""
    for env_var in auth_source_env_var_candidates(auth_source):
        env_value = os.environ.get(env_var, "")
        if env_value:
            return env_var, env_value
    return None


def credential_storage_provider_name(profile_name: str, profile: ProviderProfile) -> str:
    """Return the storage namespace used for this profile's credential.

    Built-in API-key flows continue to use provider-level storage by default.
    Custom compatible profiles can set ``credential_slot`` to bind their own key.
    """
    del profile_name
    if auth_source_uses_api_key(profile.auth_source) and profile.credential_slot:
        return f"profile:{profile.credential_slot}"
    return auth_source_provider_name(profile.auth_source)


def default_auth_source_for_provider(provider: str, api_format: str | None = None) -> str:
    """Infer the default auth source for a provider/backend."""
    if provider == "anthropic_claude":
        return "claude_subscription"
    if provider == "openai_codex":
        return "codex_subscription"
    if provider == "copilot":
        return "copilot_oauth"
    if provider == "dashscope":
        return "dashscope_api_key"
    if provider == "bedrock":
        return "bedrock_api_key"
    if provider == "vertex":
        return "vertex_api_key"
    if provider == "moonshot":
        return "moonshot_api_key"
    if provider == "gemini":
        return "gemini_api_key"
    if provider == "minimax":
        return "minimax_api_key"
    if provider == "nvidia":
        return "nvidia_api_key"
    if provider == "modelscope":
        return "modelscope_api_key"
    if provider == "openai" or api_format == "openai":
        return "openai_api_key"
    return "anthropic_api_key"


def _slugify_profile_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "custom"

