# ruff: noqa: F401
"""Settings model and loading logic for OpenHarness.

Settings are resolved with the following precedence (highest first):
1. CLI arguments
2. Environment variables (ANTHROPIC_API_KEY, OPENHARNESS_MODEL, etc.)
3. Project config file (config/backend/settings.json, when running inside a repo;
   legacy .openharness/settings.json is still accepted)
4. User config file (~/.openharness/settings.json)
5. Defaults
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.config.layers import expand_env, load_json_file
from openharness.config.settings_models import (
    BrandingSettings,
    DockerSandboxSettings,
    ImageGenerationConfig,
    MemorySettings,
    PathRuleConfig,
    PermissionSettings,
    ProviderProfile,
    ResolvedAuth,
    SandboxFilesystemSettings,
    SandboxNetworkSettings,
    SandboxSettings,
    VisionModelConfig,
    WebSettings,
)
from openharness.config.settings_profiles import (
    _CLAUDE_ALIAS_TARGETS,
    CLAUDE_MODEL_ALIAS_OPTIONS,
    _slugify_profile_name,
    auth_source_env_var_candidates,
    auth_source_provider_name,
    auth_source_uses_api_key,
    builtin_provider_profile_names,
    credential_storage_provider_name,
    default_auth_source_for_provider,
    default_provider_profiles,
    display_label_for_profile,
    display_model_setting,
    is_claude_family_provider,
    normalize_anthropic_model_name,
    resolve_auth_env_value,
    resolve_model_setting,
)
from openharness.hooks.schemas import HookDefinition
from openharness.mcp.types import McpServerConfig
from openharness.permissions.modes import PermissionMode
from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

# ANSI escape sequence pattern
_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi_escape_sequences(text: str) -> str:
    """Remove ANSI escape sequences from text.

    This is used to clean environment variables that may contain terminal
    formatting codes (e.g., '[1m' for bold) which can corrupt API requests.
    """
    if not text:
        return text
    return _ANSI_ESCAPE_PATTERN.sub("", text)


def _infer_profile_name_from_flat_settings(settings: "Settings") -> str:
    provider = (settings.provider or "").strip()
    if provider == "openai_codex":
        return "codex"
    if provider == "anthropic_claude":
        return "claude-subscription"
    if provider == "copilot" or settings.api_format == "copilot":
        return "copilot"
    if provider == "openai" and not settings.base_url:
        return "openai-compatible"
    if provider == "anthropic" and not settings.base_url:
        return "claude-api"
    if settings.base_url:
        return _slugify_profile_name(Path(settings.base_url).name or settings.base_url)
    if provider:
        return _slugify_profile_name(provider)
    return "claude-api"


def _profile_from_flat_settings(settings: "Settings") -> tuple[str, ProviderProfile]:
    defaults = default_provider_profiles()
    name = _infer_profile_name_from_flat_settings(settings)
    existing = defaults.get(name)
    if existing is not None and (
        existing.provider == settings.provider or not settings.provider
    ) and (
        existing.api_format == settings.api_format
    ) and (
        existing.base_url == settings.base_url
    ):
        profile = existing.model_copy(
            update={
                "last_model": settings.model or existing.resolved_model,
            }
        )
        return name, profile

    provider = settings.provider or ("copilot" if settings.api_format == "copilot" else ("openai" if settings.api_format == "openai" else "anthropic"))
    profile = ProviderProfile(
        label=f"Imported {provider}",
        provider=provider,
        api_format=settings.api_format,
        auth_source=default_auth_source_for_provider(provider, settings.api_format),
        default_model=settings.model or defaults.get("claude-api", ProviderProfile(
            label="Claude API",
            provider="anthropic",
            api_format="anthropic",
            auth_source="anthropic_api_key",
            default_model="sonnet",
        )).default_model,
        last_model=settings.model or None,
        base_url=settings.base_url,
    )
    return name, profile


class Settings(BaseModel):
    """Main settings model for OpenHarness."""

    # API configuration
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 16384
    base_url: str | None = None
    timeout: float = 30.0
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    api_format: str = "anthropic"  # "anthropic", "openai", or "copilot"
    provider: str = ""
    active_profile: str = "claude-api"
    profiles: dict[str, ProviderProfile] = Field(default_factory=default_provider_profiles)
    max_turns: int = 200

    # Behavior
    system_prompt: str | None = None
    permission: PermissionSettings = Field(default_factory=PermissionSettings)
    hooks: dict[str, list[HookDefinition]] = Field(default_factory=dict)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    enabled_plugins: dict[str, bool] = Field(default_factory=dict)
    allow_project_plugins: bool = False
    allow_project_skills: bool = True
    project_skill_dirs: list[str] = Field(
        default_factory=lambda: [".openharness/skills", ".agents/skills", ".claude/skills"]
    )
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    # UI
    theme: str = "default"
    output_style: str = "default"
    branding: BrandingSettings = Field(default_factory=BrandingSettings)
    vim_mode: bool = False
    voice_mode: bool = False
    fast_mode: bool = False
    effort: str = "medium"
    passes: int = 1
    verbose: bool = False

    # Vision model (image-to-text fallback)
    vision: VisionModelConfig = Field(default_factory=VisionModelConfig)

    # Image generation model
    image_generation: ImageGenerationConfig = Field(default_factory=ImageGenerationConfig)

    def merged_profiles(self) -> dict[str, ProviderProfile]:
        """Return the saved profiles merged over the built-in catalog."""
        merged = default_provider_profiles()
        for name, raw_profile in self.profiles.items():
            profile = (
                raw_profile.model_copy(deep=True)
                if isinstance(raw_profile, ProviderProfile)
                else ProviderProfile.model_validate(raw_profile)
            )
            builtin = merged.get(name)
            if builtin is not None and profile.base_url is None and builtin.base_url is not None:
                profile = profile.model_copy(update={"base_url": builtin.base_url})
            merged[name] = profile
        return merged

    def resolve_profile(self, name: str | None = None) -> tuple[str, ProviderProfile]:
        """Return the active provider profile."""
        profiles = self.merged_profiles()
        profile_name = (name or self.active_profile or os.environ.get("OPENHARNESS_PROFILE") or "").strip() or "claude-api"
        if profile_name not in profiles:
            fallback_name, fallback = _profile_from_flat_settings(self)
            profiles[fallback_name] = fallback
            profile_name = fallback_name
        return profile_name, profiles[profile_name].model_copy(deep=True)

    def materialize_active_profile(self) -> Settings:
        """Project the active profile back onto legacy flat settings fields."""
        profile_name, profile = self.resolve_profile()
        configured_model = (profile.last_model or "").strip() or profile.default_model
        return self.model_copy(
            update={
                "active_profile": profile_name,
                "profiles": self.merged_profiles(),
                "provider": profile.provider,
                "api_format": profile.api_format,
                "base_url": profile.base_url,
                "context_window_tokens": profile.context_window_tokens,
                "auto_compact_threshold_tokens": profile.auto_compact_threshold_tokens,
                "model": resolve_model_setting(
                    configured_model,
                    profile.provider,
                    default_model=profile.default_model,
                    permission_mode=self.permission.mode.value,
                ),
            }
        )

    def sync_active_profile_from_flat_fields(self) -> Settings:
        """Fold legacy flat provider fields back into the active profile.

        This preserves compatibility for callers that still construct `Settings`
        by setting top-level `provider` / `api_format` / `base_url` / `model`
        directly before the profile layer is used everywhere.
        """
        profile_name, profile = self.resolve_profile()
        profile_from_env = bool(os.environ.get("OPENHARNESS_PROFILE"))
        flat_profile_fields_match_profile = profile_from_env or (
            (self.provider or "").strip() == profile.provider
            and (self.api_format or "").strip() == profile.api_format
            and self.base_url == profile.base_url
        )
        next_provider = profile.provider if flat_profile_fields_match_profile else (self.provider or "").strip() or profile.provider
        next_api_format = profile.api_format if flat_profile_fields_match_profile else (self.api_format or "").strip() or profile.api_format
        next_base_url = profile.base_url if flat_profile_fields_match_profile else (self.base_url if self.base_url is not None else profile.base_url)
        next_context_window_tokens = (
            self.context_window_tokens
            if self.context_window_tokens is not None
            else profile.context_window_tokens
        )
        next_auto_compact_threshold_tokens = (
            self.auto_compact_threshold_tokens
            if self.auto_compact_threshold_tokens is not None
            else profile.auto_compact_threshold_tokens
        )
        flat_model = (self.model or "").strip()
        resolved_profile_model = resolve_model_setting(
            (profile.last_model or "").strip() or profile.default_model,
            profile.provider,
            default_model=profile.default_model,
            permission_mode=self.permission.mode.value,
        )
        if flat_model and flat_model != resolved_profile_model:
            next_model = flat_model
        else:
            next_model = profile.last_model
        current_default_auth = default_auth_source_for_provider(profile.provider, profile.api_format)
        next_auth_source = profile.auth_source
        if not next_auth_source or next_auth_source == current_default_auth:
            next_auth_source = default_auth_source_for_provider(next_provider, next_api_format)

        updated_profile = profile.model_copy(
            update={
                "provider": next_provider,
                "api_format": next_api_format,
                "base_url": next_base_url,
                "auth_source": next_auth_source,
                "last_model": next_model,
                "context_window_tokens": next_context_window_tokens,
                "auto_compact_threshold_tokens": next_auto_compact_threshold_tokens,
            }
        )
        profiles = self.merged_profiles()
        profiles[profile_name] = updated_profile
        return self.model_copy(
            update={
                "active_profile": profile_name,
                "profiles": profiles,
            }
        )

    def resolve_api_key(self) -> str:
        """Resolve API key with precedence: instance value > env var > empty.

        For ``copilot`` api_format the key is managed separately via
        ``abc auth copilot-login`` and this method is not called.

        Returns the API key string. Raises ValueError if no key is found.
        """
        profile_name, profile = self.resolve_profile()
        del profile_name
        if profile.provider == "openai_codex":
            return self.resolve_auth().value
        if profile.provider == "anthropic_claude":
            raise ValueError(
                "Current provider uses Anthropic auth tokens instead of API keys. "
                "Use resolve_auth() for runtime credential resolution."
            )
        # Copilot format manages its own auth; skip normal key resolution.
        if profile.api_format == "copilot":
            return "copilot-managed"

        if self.api_key:
            return self.api_key

        env_resolved = resolve_auth_env_value(profile.auth_source)
        if env_resolved:
            _, env_value = env_resolved
            return env_value

        raise ValueError(
            "No API key found. Set an OPENHARNESS_* provider API key "
            "(preferred) or the matching native provider environment variable, "
            "or configure api_key in the effective settings file "
            "(.openharness/settings.json in the repo, or ~/.openharness/settings.json)"
        )

    def resolve_auth(self) -> ResolvedAuth:
        """Resolve auth for the current provider, including subscription bridges."""
        profile_name, profile = self.resolve_profile()
        provider = profile.provider.strip()
        auth_source = profile.auth_source.strip() or default_auth_source_for_provider(provider, profile.api_format)
        if auth_source in {"codex_subscription", "claude_subscription"}:
            env_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            if auth_source == "claude_subscription" and env_auth_token:
                return ResolvedAuth(
                    provider=provider,
                    auth_kind="oauth",
                    value=env_auth_token,
                    source="env:ANTHROPIC_AUTH_TOKEN",
                    state="configured",
                )
            from openharness.auth.external import (
                is_third_party_anthropic_endpoint,
                load_external_credential,
            )
            from openharness.auth.storage import load_external_binding

            if auth_source == "claude_subscription" and is_third_party_anthropic_endpoint(profile.base_url):
                raise ValueError(
                    "Claude subscription auth only supports direct Anthropic/Claude endpoints. "
                    "Use an API-key-backed Anthropic-compatible profile for third-party base URLs."
                )
            binding = load_external_binding(auth_source_provider_name(auth_source))
            if binding is None:
                raise ValueError(
                    f"No external auth binding found for {auth_source}. Run 'abc auth "
                    f"{'codex-login' if auth_source == 'codex_subscription' else 'claude-login'}' first."
                )
            credential = load_external_credential(
                binding,
                refresh_if_needed=(auth_source == "claude_subscription"),
            )
            return ResolvedAuth(
                provider=provider,
                auth_kind=credential.auth_kind,
                value=credential.value,
                source=f"external:{credential.source_path}",
                state="configured",
            )

        if auth_source == "copilot_oauth":
            return ResolvedAuth(
                provider="copilot",
                auth_kind="oauth_device",
                value="copilot-managed",
                source="copilot",
                state="configured",
            )

        storage_provider = auth_source_provider_name(auth_source)

        from openharness.auth.storage import load_credential

        if profile.credential_slot:
            scoped_storage_provider = f"profile:{profile.credential_slot}"
            scoped = load_credential(scoped_storage_provider, "api_key", use_keyring=False)
            if scoped is None:
                scoped = load_credential(scoped_storage_provider, "api_key")
            if scoped:
                return ResolvedAuth(
                    provider=provider or auth_source_provider_name(auth_source),
                    auth_kind="api_key",
                    value=scoped,
                    source=f"file:{scoped_storage_provider}",
                    state="configured",
                )

        storage_provider = credential_storage_provider_name(profile_name, profile)

        env_resolved = resolve_auth_env_value(auth_source)
        if env_resolved:
            env_var, env_value = env_resolved
            return ResolvedAuth(
                provider=provider or storage_provider,
                auth_kind="api_key",
                value=env_value,
                source=f"env:{env_var}",
                state="configured",
            )

        profile_key = (profile.api_key or "").strip()
        if profile_key:
            return ResolvedAuth(
                provider=provider or storage_provider,
                auth_kind="api_key",
                value=profile_key,
                source="settings:profile",
                state="configured",
            )

        explicit_key = "" if profile.credential_slot else self.api_key
        if explicit_key:
            return ResolvedAuth(
                provider=provider or storage_provider,
                auth_kind="api_key",
                value=explicit_key,
                source="settings_or_env",
                state="configured",
            )

        stored = load_credential(storage_provider, "api_key")
        if stored:
            return ResolvedAuth(
                provider=provider or auth_source_provider_name(auth_source),
                auth_kind="api_key",
                value=stored,
                source=f"file:{storage_provider}",
                state="configured",
            )

        raise ValueError(
            f"No credentials found for auth source '{auth_source}'. "
            "Configure the matching provider or environment variable first."
        )

    def merge_cli_overrides(self, **overrides: Any) -> Settings:
        """Return a new Settings with CLI overrides applied (non-None values only)."""
        updates = {k: v for k, v in overrides.items() if v is not None}
        permission_mode = updates.pop("permission_mode", None)

        def apply_permission_mode(settings: Settings) -> Settings:
            if permission_mode is None:
                return settings
            return settings.model_copy(
                update={
                    "permission": settings.permission.model_copy(
                        update={"mode": PermissionMode(str(permission_mode))}
                    )
                }
            )

        # Strip ANSI escape sequences from model name if present
        if "model" in updates and isinstance(updates["model"], str):
            updates["model"] = strip_ansi_escape_sequences(updates["model"])
        if "effort" in updates and isinstance(updates["effort"], str):
            updates["effort"] = "xhigh" if updates["effort"].strip().lower() == "max" else updates["effort"].strip().lower()
        merged = apply_permission_mode(self.model_copy(update=updates))
        if not updates:
            return merged
        profile_keys = {
            "model",
            "base_url",
            "api_format",
            "provider",
            "api_key",
            "active_profile",
            "profiles",
            "context_window_tokens",
            "auto_compact_threshold_tokens",
        }
        profile_updates = profile_keys.intersection(updates)
        if not profile_updates:
            return merged
        if "active_profile" in profile_updates:
            switch_updates = {
                key: value
                for key, value in updates.items()
                if key not in profile_keys or key in {"active_profile", "profiles"}
            }
            switched = apply_permission_mode(self.model_copy(update=switch_updates)).materialize_active_profile()
            remaining_profile_updates = {
                key: value
                for key, value in updates.items()
                if key in profile_keys and key not in {"active_profile", "profiles"}
            }
            if not remaining_profile_updates:
                return switched
            return (
                switched.model_copy(update=remaining_profile_updates)
                .sync_active_profile_from_flat_fields()
                .materialize_active_profile()
            )
        return merged.sync_active_profile_from_flat_fields().materialize_active_profile()


def _apply_env_overrides(settings: Settings) -> Settings:
    """Apply supported environment variable overrides over loaded settings.

    Provider-scoped env vars (``ANTHROPIC_BASE_URL``, ``ANTHROPIC_MODEL``,
    ``OPENAI_BASE_URL``) only apply when the active profile does *not*
    explicitly configure the corresponding field.  ``OPENHARNESS_*`` env vars
    always override (explicit user intent).
    """
    updates: dict[str, Any] = {}

    # Resolve the active profile to check for explicit settings.
    _, active_profile = settings.resolve_profile()
    profile_has_base_url = active_profile.base_url is not None
    profile_explicit_model = (active_profile.last_model or "").strip()
    profile_has_explicit_model = bool(profile_explicit_model) and profile_explicit_model.lower() not in {"", "default"}

    # --- model ---
    openharness_model = os.environ.get("OPENHARNESS_MODEL")
    if openharness_model:
        updates["model"] = strip_ansi_escape_sequences(openharness_model)
    elif not profile_has_explicit_model:
        anthropic_model = os.environ.get("ANTHROPIC_MODEL")
        if anthropic_model:
            updates["model"] = strip_ansi_escape_sequences(anthropic_model)

    # --- base_url ---
    openharness_base = os.environ.get("OPENHARNESS_BASE_URL")
    if openharness_base:
        updates["base_url"] = openharness_base
    elif not profile_has_base_url:
        generic_base = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        if generic_base:
            updates["base_url"] = generic_base

    max_tokens = os.environ.get("OPENHARNESS_MAX_TOKENS")
    if max_tokens:
        updates["max_tokens"] = int(max_tokens)

    timeout = os.environ.get("OPENHARNESS_TIMEOUT")
    if timeout:
        updates["timeout"] = float(timeout)

    max_turns = os.environ.get("OPENHARNESS_MAX_TURNS")
    if max_turns:
        updates["max_turns"] = int(max_turns)

    context_window_tokens = os.environ.get("OPENHARNESS_CONTEXT_WINDOW_TOKENS")
    if context_window_tokens:
        updates["context_window_tokens"] = int(context_window_tokens)

    auto_compact_threshold_tokens = os.environ.get("OPENHARNESS_AUTO_COMPACT_THRESHOLD_TOKENS")
    if auto_compact_threshold_tokens:
        updates["auto_compact_threshold_tokens"] = int(auto_compact_threshold_tokens)

    provider = os.environ.get("OPENHARNESS_PROVIDER")
    api_format = os.environ.get("OPENHARNESS_API_FORMAT")
    env_auth_source = active_profile.auth_source
    if provider or api_format:
        env_auth_source = default_auth_source_for_provider(
            provider or active_profile.provider,
            api_format or active_profile.api_format,
        )

    env_resolved = resolve_auth_env_value(env_auth_source)
    if env_resolved:
        _, api_key = env_resolved
        updates["api_key"] = api_key

    if api_format:
        updates["api_format"] = api_format

    if provider:
        updates["provider"] = provider

    sandbox_enabled = os.environ.get("OPENHARNESS_SANDBOX_ENABLED")
    sandbox_fail = os.environ.get("OPENHARNESS_SANDBOX_FAIL_IF_UNAVAILABLE")
    sandbox_backend = os.environ.get("OPENHARNESS_SANDBOX_BACKEND")
    sandbox_docker_image = os.environ.get("OPENHARNESS_SANDBOX_DOCKER_IMAGE")
    sandbox_updates: dict[str, Any] = {}
    if sandbox_enabled is not None:
        sandbox_updates["enabled"] = _parse_bool_env(sandbox_enabled)
    if sandbox_fail is not None:
        sandbox_updates["fail_if_unavailable"] = _parse_bool_env(sandbox_fail)
    if sandbox_backend is not None:
        sandbox_updates["backend"] = sandbox_backend
    if sandbox_docker_image is not None:
        sandbox_updates["docker"] = settings.sandbox.docker.model_copy(
            update={"image": sandbox_docker_image}
        )
    if sandbox_updates:
        updates["sandbox"] = settings.sandbox.model_copy(update=sandbox_updates)

    web_updates: dict[str, Any] = {}
    web_proxy = os.environ.get("OPENHARNESS_WEB_PROXY")
    if web_proxy:
        web_updates["proxy"] = web_proxy
    web_resolution_mode = os.environ.get("OPENHARNESS_WEB_RESOLUTION_MODE")
    if web_resolution_mode:
        web_updates["resolution_mode"] = web_resolution_mode
    web_synthetic_dns_cidrs = os.environ.get("OPENHARNESS_WEB_SYNTHETIC_DNS_CIDRS")
    if web_synthetic_dns_cidrs:
        web_updates["synthetic_dns_cidrs"] = [
            entry.strip()
            for entry in web_synthetic_dns_cidrs.split(",")
            if entry.strip()
        ]
    if web_updates:
        updates["web"] = settings.web.model_copy(update=web_updates)

    # --- branding (product name / slogan for the welcome screen) ---
    branding_updates: dict[str, Any] = {}
    product_name = os.environ.get("OPENHARNESS_PRODUCT_NAME")
    if product_name and product_name.strip():
        branding_updates["product_name"] = strip_ansi_escape_sequences(product_name).strip()
    slogan = os.environ.get("OPENHARNESS_SLOGAN")
    if slogan and slogan.strip():
        branding_updates["slogan"] = strip_ansi_escape_sequences(slogan).strip()
    if branding_updates:
        updates["branding"] = settings.branding.model_copy(update=branding_updates)

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _parse_bool_env(value: str) -> bool:
    """Parse a boolean environment override."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from config file, merging with defaults.

    Args:
        config_path: Path to settings.json. If None, uses the default location.

    Returns:
        Settings instance with file values merged over defaults.
    """
    if config_path is None:
        from openharness.config.paths import get_config_file_path

        config_path = get_config_file_path()

    if config_path.exists():
        # Centralized project configs may contain ``${VAR}`` placeholders so
        # deployment-specific values stay in the environment / .env layer.
        raw = expand_env(load_json_file(config_path))
        settings = Settings.model_validate(raw)
        env_profile = os.environ.get("OPENHARNESS_PROFILE")
        if env_profile:
            settings = settings.model_copy(update={"active_profile": env_profile.strip()})
        if "profiles" not in raw or "active_profile" not in raw:
            profile_name, profile = _profile_from_flat_settings(settings)
            merged_profiles = settings.merged_profiles()
            merged_profiles[profile_name] = profile
            settings = settings.model_copy(
                update={
                    "active_profile": profile_name,
                    "profiles": merged_profiles,
                }
            )
        return _apply_env_overrides(settings.materialize_active_profile())

    settings = Settings()
    env_profile = os.environ.get("OPENHARNESS_PROFILE")
    if env_profile:
        settings = settings.model_copy(update={"active_profile": env_profile.strip()})
    return _apply_env_overrides(settings.materialize_active_profile())


def save_settings(settings: Settings, config_path: Path | None = None) -> None:
    """Persist settings to the config file.

    Args:
        settings: Settings instance to save.
        config_path: Path to write. If None, uses the default location.
    """
    if config_path is None:
        from openharness.config.paths import get_config_file_path

        config_path = get_config_file_path()

    settings = settings.sync_active_profile_from_flat_fields().materialize_active_profile()
    lock_path = config_path.with_suffix(config_path.suffix + ".lock")
    with exclusive_file_lock(lock_path):
        atomic_write_text(
            config_path,
            settings.model_dump_json(indent=2) + "\n",
        )
