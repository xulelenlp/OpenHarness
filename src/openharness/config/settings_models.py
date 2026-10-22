"""Pydantic settings models shared by the config package."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import BaseModel, Field

from openharness.permissions.modes import PermissionMode


class PathRuleConfig(BaseModel):
    """A glob-pattern path permission rule."""

    pattern: str
    allow: bool = True


class PermissionSettings(BaseModel):
    """Permission mode configuration."""

    mode: PermissionMode = PermissionMode.DEFAULT
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    path_rules: list[PathRuleConfig] = Field(default_factory=list)
    denied_commands: list[str] = Field(default_factory=list)


class MemorySettings(BaseModel):
    """Memory system configuration."""

    enabled: bool = True
    max_files: int = 5
    max_entrypoint_lines: int = 200
    max_entrypoint_bytes: int = 25_000
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    auto_extract_enabled: bool = False
    auto_extract_max_records: int = 3
    session_memory_enabled: bool = True
    auto_dream_enabled: bool = False
    auto_dream_min_hours: float = 24.0
    auto_dream_min_sessions: int = 5


class SandboxNetworkSettings(BaseModel):
    """OS-level network restrictions passed to sandbox-runtime."""

    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)


class SandboxFilesystemSettings(BaseModel):
    """OS-level filesystem restrictions passed to sandbox-runtime."""

    allow_read: list[str] = Field(default_factory=list)
    deny_read: list[str] = Field(default_factory=list)
    allow_write: list[str] = Field(default_factory=lambda: ["."])
    deny_write: list[str] = Field(default_factory=list)


class DockerSandboxSettings(BaseModel):
    """Docker-specific sandbox configuration."""

    image: str = "openharness-sandbox:latest"
    auto_build_image: bool = True
    cpu_limit: float = 0.0
    memory_limit: str = ""
    extra_mounts: list[str] = Field(default_factory=list)
    extra_env: dict[str, str] = Field(default_factory=dict)


class SandboxSettings(BaseModel):
    """Sandbox-runtime integration settings."""

    enabled: bool = False
    backend: str = "srt"
    fail_if_unavailable: bool = False
    enabled_platforms: list[str] = Field(default_factory=list)
    network: SandboxNetworkSettings = Field(default_factory=SandboxNetworkSettings)
    filesystem: SandboxFilesystemSettings = Field(default_factory=SandboxFilesystemSettings)
    docker: DockerSandboxSettings = Field(default_factory=DockerSandboxSettings)


class WebSettings(BaseModel):
    """Outbound web tool configuration."""

    proxy: str | None = None
    resolution_mode: str = "auto"
    synthetic_dns_cidrs: list[str] = Field(default_factory=list)
    #: HTML endpoint used by ``web_search``. Defaults to Bing; DuckDuckGo was
    #: dropped because it is unreachable from mainland-China networks and its
    #: DNS answers are commonly poisoned.
    search_url: str | None = None


class ProviderProfile(BaseModel):
    """Named provider workflow configuration."""

    label: str
    provider: str
    api_format: str
    auth_source: str
    default_model: str
    base_url: str | None = None
    last_model: str | None = None
    credential_slot: str | None = None
    api_key: str = ""
    allowed_models: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None

    @property
    def resolved_model(self) -> str:
        """Return the active model for this profile."""
        from openharness.config.settings_profiles import resolve_model_setting

        return resolve_model_setting(
            (self.last_model or "").strip() or self.default_model,
            self.provider,
            default_model=self.default_model,
        )


@dataclass(frozen=True)
class ResolvedAuth:
    """Normalized auth material used to construct API clients."""

    provider: str
    auth_kind: str
    value: str
    source: str
    state: str = "configured"


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


class ImageGenerationConfig(BaseModel):
    """Configuration for the image_generation tool."""

    provider: str = "auto"
    model: str = "gpt-image-2"
    api_key: str = ""
    base_url: str = ""
    codex_model: str = "gpt-5.4"
    codex_base_url: str = ""

    @classmethod
    def from_env(cls) -> "ImageGenerationConfig":
        """Load image generation config from environment variables."""
        return cls(
            provider=os.environ.get("OPENHARNESS_IMAGE_GENERATION_PROVIDER", "auto").strip()
            or "auto",
            model=os.environ.get("OPENHARNESS_IMAGE_GENERATION_MODEL", "gpt-image-2").strip()
            or "gpt-image-2",
            api_key=os.environ.get("OPENHARNESS_IMAGE_GENERATION_API_KEY", "").strip(),
            base_url=os.environ.get("OPENHARNESS_IMAGE_GENERATION_BASE_URL", "").strip(),
            codex_model=os.environ.get("OPENHARNESS_IMAGE_GENERATION_CODEX_MODEL", "gpt-5.4").strip()
            or "gpt-5.4",
            codex_base_url=os.environ.get("OPENHARNESS_IMAGE_GENERATION_CODEX_BASE_URL", "").strip(),
        )

    @property
    def is_configured(self) -> bool:
        """Return True when either a key provider or Codex provider is selected."""
        return bool(self.api_key or self.provider in {"auto", "codex"})


class VisionModelConfig(BaseModel):
    """Configuration for the vision model used by the image_to_text tool.

    When the active model does not support multimodal input, the agent loop
    automatically falls back to this vision model to describe images.
    """

    model: str = ""
    api_key: str = ""
    base_url: str = ""

    @classmethod
    def from_env(cls) -> "VisionModelConfig":
        """Load vision model config from environment variables."""
        return cls(
            model=os.environ.get("OPENHARNESS_VISION_MODEL", "").strip(),
            api_key=os.environ.get("OPENHARNESS_VISION_API_KEY", "").strip(),
            base_url=os.environ.get("OPENHARNESS_VISION_BASE_URL", "").strip(),
        )

    @property
    def is_configured(self) -> bool:
        """Return True when both model and api_key are set."""
        return bool(self.model and self.api_key)


class BrandingSettings(BaseModel):
    """Product branding shown in the interactive welcome screen.

    ``product_name`` replaces the default ASCII logo with a plain-text
    product title; ``slogan`` is rendered as a dim subtitle underneath it.
    Both may be set via ``settings.json`` under the ``branding`` key or via
    the ``OPENHARNESS_PRODUCT_NAME`` / ``OPENHARNESS_SLOGAN`` environment
    variables (env vars take precedence at load time).
    """

    product_name: str = "abc helper"
    slogan: str = "An AI-powered coding assistant"

    @classmethod
    def from_env(cls) -> "BrandingSettings":
        """Load branding overrides from environment variables."""
        return cls(
            product_name=os.environ.get("OPENHARNESS_PRODUCT_NAME", "").strip() or "abc helper",
            slogan=os.environ.get("OPENHARNESS_SLOGAN", "").strip() or "An AI-powered coding assistant",
        )
