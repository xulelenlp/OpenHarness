"""Interactive setup helpers and the non-interactive ``setup`` command."""

from __future__ import annotations

import sys

import typer

from openharness.cli.app import app
from openharness.ui.prompts import (
    can_use_questionary,
    confirm_prompt,
    secret_prompt,
    select_from_menu,
    text_prompt,
)


def _prompt_model_for_profile(profile) -> str:
    from openharness.config.settings import (
        CLAUDE_MODEL_ALIAS_OPTIONS,
        display_model_setting,
        is_claude_family_provider,
    )

    current = display_model_setting(profile)
    if profile.allowed_models:
        if len(profile.allowed_models) == 1:
            return profile.allowed_models[0]
        options = [(value, value) for value in profile.allowed_models]
        return select_from_menu("Choose a model setting:", options, default_value=current if current in profile.allowed_models else profile.allowed_models[0])
    if is_claude_family_provider(profile.provider):
        options = [(value, f"{label} - {description}") for value, label, description in CLAUDE_MODEL_ALIAS_OPTIONS]
        options.append(("__custom__", "Custom model ID"))
        selection = select_from_menu(
            "Choose a model setting:",
            options,
            default_value=current if any(value == current for value, _, _ in CLAUDE_MODEL_ALIAS_OPTIONS) else "__custom__",
        )
        if selection != "__custom__":
            return selection
    return text_prompt("Model", default=current).strip() or current


def _format_profile_choice_label(info: dict[str, object]) -> str:
    """Render a user-facing workflow label without leaking internal provider ids."""
    label = str(info["label"])
    state = "" if bool(info["configured"]) else f" ({info['auth_state']})"
    return f"{label}{state}"


def _styled_missing_suffix(info: dict[str, object]) -> tuple[str, str] | None:
    """Return a soft red missing-auth suffix for questionary titles."""
    if bool(info["configured"]):
        return None
    return (f" ({info['auth_state']})", "fg:#d3869b")


def _select_setup_workflow(
    statuses: dict[str, dict[str, object]],
    *,
    default_value: str | None = None,
) -> str:
    """Render the top-level `abc setup` workflow picker with richer hints."""
    hints = {
        "claude-api": ("Claude / Kimi / GLM / MiniMax", "fg:#7aa2f7"),
        "openai-compatible": ("OpenAI / OpenRouter", "fg:#9ece6a"),
    }

    if can_use_questionary():
        import questionary

        choices = []
        for name, info in statuses.items():
            label = str(info["label"])
            hint = hints.get(name)
            missing = _styled_missing_suffix(info)
            if hint is None:
                if missing is None:
                    title = label
                else:
                    suffix, suffix_style = missing
                    title = [("", label), (suffix_style, suffix)]
            else:
                hint_text, hint_style = hint
                if missing is None:
                    title = [
                        ("", f"{label}  "),
                        (hint_style, hint_text),
                    ]
                else:
                    suffix, suffix_style = missing
                    title = [
                        ("", f"{label}  "),
                        (hint_style, hint_text),
                        ("", "  "),
                        (suffix_style, suffix.strip()),
                    ]
            choices.append(questionary.Choice(title=title, value=name, checked=(name == default_value)))

        result = questionary.select("Choose a provider workflow:", choices=choices, default=default_value).ask()
        if result is None:
            raise typer.Abort()
        return str(result)

    options: list[tuple[str, str]] = []
    for name, info in statuses.items():
        label = _format_profile_choice_label(info)
        hint = hints.get(name)
        if hint is not None:
            label = f"{label} ({hint[0]})"
        options.append((name, label))
    return select_from_menu("Choose a provider workflow:", options, default_value=default_value)


def _default_credential_slot_for_profile(name: str, auth_source: str) -> str | None:
    from openharness.config.settings import auth_source_uses_api_key, builtin_provider_profile_names

    if name in builtin_provider_profile_names():
        return None
    if not auth_source_uses_api_key(auth_source):
        return None
    return name


def _prompt_api_key_for_profile(label: str) -> str:
    key = secret_prompt(f"Enter API key for {label}").strip()
    if not key:
        raise typer.BadParameter("API key cannot be empty.")
    return key


def _configure_custom_profile_via_setup(manager) -> str:
    from openharness.config.settings import ProviderProfile, default_auth_source_for_provider

    family = select_from_menu(
        "Choose a compatible API family:",
        [
            ("anthropic", "Anthropic-compatible"),
            ("openai", "OpenAI-compatible"),
        ],
        default_value="anthropic",
    )
    default_name = f"custom-{family}"
    name = text_prompt("Profile name", default=default_name).strip()
    if not name:
        raise typer.BadParameter("Profile name cannot be empty.")
    label = text_prompt("Display label", default=name).strip() or name
    base_url = text_prompt("Base URL", default="").strip()
    if not base_url:
        raise typer.BadParameter("Base URL cannot be empty.")

    auth_source = default_auth_source_for_provider(family, family)
    model = text_prompt("Default model", default="").strip()
    if not model:
        raise typer.BadParameter("Default model cannot be empty.")

    profile = ProviderProfile(
        label=label,
        provider=family,
        api_format=family,
        auth_source=auth_source,
        default_model=model,
        last_model=model,
        base_url=base_url,
        credential_slot=_default_credential_slot_for_profile(name, auth_source),
        allowed_models=[model],
    )
    manager.upsert_profile(name, profile)
    manager.store_profile_credential(name, "api_key", _prompt_api_key_for_profile(label))
    return name


def _ensure_preset_profile(
    manager,
    *,
    name: str,
    label: str,
    provider: str,
    api_format: str,
    auth_source: str,
    base_url: str | None,
    model: str,
    lock_model: bool,
) -> str:
    from openharness.config.settings import ProviderProfile

    existing = manager.list_profiles().get(name)
    profile = ProviderProfile(
        label=label,
        provider=provider,
        api_format=api_format,
        auth_source=auth_source,
        default_model=model,
        last_model=model,
        base_url=base_url,
        credential_slot=_default_credential_slot_for_profile(name, auth_source),
        allowed_models=[model] if lock_model else (existing.allowed_models if existing else []),
    )
    manager.upsert_profile(name, profile)
    return name


def _specialize_setup_target(manager, target: str) -> str:
    """Expand a top-level family choice into a concrete workflow profile."""
    from openharness.config.settings import default_auth_source_for_provider

    if target == "claude-api":
        choice = select_from_menu(
            "Choose an Anthropic-compatible provider:",
            [
                ("claude-api", "Claude official"),
                ("kimi-anthropic", "Moonshot Kimi"),
                ("glm-anthropic", "Zhipu GLM"),
                ("minimax-anthropic", "MiniMax"),
            ],
            default_value="claude-api",
        )
        if choice == "claude-api":
            return choice
        defaults = {
            "kimi-anthropic": ("Kimi (Anthropic-compatible)", "https://api.moonshot.cn/anthropic", "kimi-k2.5"),
            "glm-anthropic": ("GLM (Anthropic-compatible)", "", "glm-4.5"),
            "minimax-anthropic": ("MiniMax (Anthropic-compatible)", "", "MiniMax-M2.7"),
        }
        label, suggested_base_url, suggested_model = defaults[choice]
        base_url = text_prompt("Base URL", default=suggested_base_url).strip()
        if not base_url:
            raise typer.BadParameter("Base URL cannot be empty.")
        model = text_prompt("Model", default=suggested_model).strip()
        if not model:
            raise typer.BadParameter("Model cannot be empty.")
        return _ensure_preset_profile(
            manager,
            name=choice,
            label=label,
            provider="anthropic",
            api_format="anthropic",
            auth_source=default_auth_source_for_provider("anthropic", "anthropic"),
            base_url=base_url,
            model=model,
            lock_model=True,
        )

    if target == "openai-compatible":
        choice = select_from_menu(
            "Choose an OpenAI-compatible provider:",
            [
                ("openai-compatible", "OpenAI official"),
                ("openrouter", "OpenRouter"),
            ],
            default_value="openai-compatible",
        )
        if choice == "openai-compatible":
            return choice
        base_url = text_prompt("Base URL", default="https://openrouter.ai/api/v1").strip()
        if not base_url:
            raise typer.BadParameter("Base URL cannot be empty.")
        model = text_prompt("Default model", default="").strip()
        if not model:
            raise typer.BadParameter("Default model cannot be empty.")
        return _ensure_preset_profile(
            manager,
            name="openrouter",
            label="OpenRouter",
            provider="openai",
            api_format="openai",
            auth_source=default_auth_source_for_provider("openai", "openai"),
            base_url=base_url,
            model=model,
            lock_model=False,
        )

    return target


def _ensure_profile_auth(manager, profile_name: str) -> None:
    from openharness.auth.flows import ApiKeyFlow

    # Imported lazily to keep the setup helpers and auth commands decoupled.
    from openharness.cli.commands.auth import _login_provider
    from openharness.config.settings import auth_source_provider_name, auth_source_uses_api_key

    profile = manager.list_profiles()[profile_name]
    if not auth_source_uses_api_key(profile.auth_source):
        _login_provider(auth_source_provider_name(profile.auth_source))
        return

    flow = ApiKeyFlow(
        provider=profile.provider,
        prompt_text=f"Enter API key for {profile.label}",
    )
    try:
        key = flow.run()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    manager.store_profile_credential(profile_name, "api_key", key)
    print(f"{profile.label} API key saved.", flush=True)


def _maybe_update_profile_auth(manager, profile_name: str) -> bool:
    """Ask whether to replace an already configured profile API key."""
    from openharness.config.settings import auth_source_uses_api_key

    profile = manager.list_profiles()[profile_name]
    if not auth_source_uses_api_key(profile.auth_source):
        return False
    if not confirm_prompt(f"Update API key for {profile.label}?", default=False):
        return False
    _ensure_profile_auth(manager, profile_name)
    return True


@app.command("setup")
def setup_cmd(
    profile: str | None = typer.Argument(None, help="Provider profile name to validate"),
) -> None:
    """Validate model configuration from settings.json (non-interactive).

    Model/provider settings are configured by editing the effective settings file
    (.openharness/settings.json inside the repo when present, otherwise
    ~/.openharness/settings.json) or via environment variables (OPENHARNESS_MODEL,
    OPENHARNESS_BASE_URL, OPENHARNESS_PROVIDER, OPENHARNESS_API_FORMAT, plus the
    provider's API key).
    This command only reads and validates; it never prompts interactively.
    """
    from openharness.api.provider import auth_status, detect_provider
    from openharness.config import get_config_file_path, load_settings

    settings = load_settings()
    available = settings.merged_profiles()
    if profile is not None:
        if profile not in available:
            print(f"Unknown provider profile: {profile!r}", file=sys.stderr)
            print(f"Available profiles: {', '.join(sorted(available))}", file=sys.stderr)
            raise typer.Exit(1)
        settings = settings.model_copy(update={"active_profile": profile}).materialize_active_profile()

    profile_name, profile_obj = settings.resolve_profile()
    provider = detect_provider(settings)
    auth = auth_status(settings)

    print("OpenHarness Setup (non-interactive)")
    print()
    print("Resolved model configuration")
    print(f"  config file:    {get_config_file_path()}")
    print(f"  profile:        {profile_name} ({profile_obj.label})")
    print(f"  provider:       {provider.name}")
    print(f"  api_format:     {settings.api_format}")
    print(f"  model:          {settings.model}")
    print(f"  base_url:       {settings.base_url or '(default)'}")
    print(f"  auth:           {auth}")
    print()

    if auth.startswith("missing"):
        print("Status: NOT READY")
        print()
        print("Configure non-interactively by editing settings.json, e.g.:")
        print(
            "\n".join(
                [
                    "  {",
                    '    "active_profile": "openai-compatible",',
                    '    "profiles": {',
                    '      "openai-compatible": {',
                    '        "label": "DeepSeek",',
                    '        "provider": "openai",',
                    '        "api_format": "openai",',
                    '        "auth_source": "openai_api_key",',
                    '        "default_model": "deepseek-chat",',
                    '        "base_url": "https://api.deepseek.com/v1",',
                    '        "api_key": "sk-YOUR-KEY"',
                    "      }",
                    "    }",
                    "  }",
                ]
            )
        )
        print()
        print("The API key can live in settings.json (profile `api_key`, as above).")
        print("Alternatively set an env var (e.g. OPENAI_API_KEY) or `abc auth login`.")
        raise typer.Exit(1)

    print("Status: READY")
    print("Run `abc` for an interactive session, or `abc -p \"...\"` for a single prompt.")
