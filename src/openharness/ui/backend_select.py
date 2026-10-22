"""Select-command handling for the React backend host."""

from __future__ import annotations

from openharness.auth.manager import AuthManager
from openharness.config.settings import CLAUDE_MODEL_ALIAS_OPTIONS, resolve_model_setting
from openharness.output_styles import load_output_styles
from openharness.themes.loader import list_themes
from openharness.ui.protocol import BackendEvent


class SelectCommandMixin:
    async def _handle_select_command(self, command_name: str) -> None:
        assert self._bundle is not None
        command = command_name.strip().lstrip("/").lower()
        if command == "resume":
            await self._handle_list_sessions()
            return

        settings = self._bundle.current_settings()
        state = self._bundle.app_state.get()
        _, active_profile = settings.resolve_profile()
        current_model = settings.model

        if command == "provider":
            statuses = AuthManager(settings).get_profile_statuses()
            options = [
                {
                    "value": name,
                    "label": info["label"],
                    "description": f"{info['provider']} / {info['auth_source']}" + (" [missing auth]" if not info["configured"] else ""),
                    "active": info["active"],
                }
                for name, info in statuses.items()
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Provider Profile", "command": "provider"},
                    select_options=options,
                )
            )
            return

        if command == "permissions":
            options = [
                {
                    "value": "default",
                    "label": "Default",
                    "description": "Ask before write/execute operations",
                    "active": settings.permission.mode.value == "default",
                },
                {
                    "value": "full_auto",
                    "label": "Auto",
                    "description": "Allow all tools automatically",
                    "active": settings.permission.mode.value == "full_auto",
                },
                {
                    "value": "plan",
                    "label": "Plan Mode",
                    "description": "Block all write operations",
                    "active": settings.permission.mode.value == "plan",
                },
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Permission Mode", "command": "permissions"},
                    select_options=options,
                )
            )
            return

        if command == "theme":
            options = [
                {
                    "value": name,
                    "label": name,
                    "active": name == settings.theme,
                }
                for name in list_themes()
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Theme", "command": "theme"},
                    select_options=options,
                )
            )
            return

        if command == "output-style":
            options = [
                {
                    "value": style.name,
                    "label": style.name,
                    "description": style.source,
                    "active": style.name == settings.output_style,
                }
                for style in load_output_styles()
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Output Style", "command": "output-style"},
                    select_options=options,
                )
            )
            return

        if command == "effort":
            options = [
                {"value": "low", "label": "Low", "description": "Fastest responses", "active": settings.effort == "low"},
                {"value": "medium", "label": "Medium", "description": "Balanced reasoning", "active": settings.effort == "medium"},
                {"value": "high", "label": "High", "description": "Deepest reasoning", "active": settings.effort == "high"},
                {"value": "xhigh", "label": "XHigh", "description": "Extra high reasoning", "active": settings.effort == "xhigh"},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Reasoning Effort", "command": "effort"},
                    select_options=options,
                )
            )
            return

        if command == "passes":
            current = int(state.passes or settings.passes)
            options = [
                {"value": str(value), "label": f"{value} pass{'es' if value != 1 else ''}", "active": value == current}
                for value in range(1, 9)
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Reasoning Passes", "command": "passes"},
                    select_options=options,
                )
            )
            return

        if command == "turns":
            current = self._bundle.engine.max_turns
            values = {32, 64, 128, 200, 256, 512}
            if isinstance(current, int):
                values.add(current)
            options = [{"value": "unlimited", "label": "Unlimited", "description": "Do not hard-stop this session", "active": current is None}]
            options.extend(
                {"value": str(value), "label": f"{value} turns", "active": value == current}
                for value in sorted(values)
            )
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Max Turns", "command": "turns"},
                    select_options=options,
                )
            )
            return

        if command == "fast":
            current = bool(state.fast_mode)
            options = [
                {"value": "on", "label": "On", "description": "Prefer shorter, faster responses", "active": current},
                {"value": "off", "label": "Off", "description": "Use normal response mode", "active": not current},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Fast Mode", "command": "fast"},
                    select_options=options,
                )
            )
            return

        if command == "vim":
            current = bool(state.vim_enabled)
            options = [
                {"value": "on", "label": "On", "description": "Enable Vim keybindings", "active": current},
                {"value": "off", "label": "Off", "description": "Use standard keybindings", "active": not current},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Vim Mode", "command": "vim"},
                    select_options=options,
                )
            )
            return

        if command == "voice":
            current = bool(state.voice_enabled)
            options = [
                {"value": "on", "label": "On", "description": state.voice_reason or "Enable voice mode", "active": current},
                {"value": "off", "label": "Off", "description": "Disable voice mode", "active": not current},
            ]
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Voice Mode", "command": "voice"},
                    select_options=options,
                )
            )
            return

        if command == "model":
            options = self._model_select_options(current_model, active_profile.provider, active_profile.allowed_models)
            await self._emit(
                BackendEvent(
                    type="select_request",
                    modal={"kind": "select", "title": "Model", "command": "model"},
                    select_options=options,
                )
            )
            return

        await self._emit(BackendEvent(type="error", message=f"No selector available for /{command}"))

    def _model_select_options(self, current_model: str, provider: str, allowed_models: list[str] | None = None) -> list[dict[str, object]]:
        if allowed_models:
            return [
                {
                    "value": value,
                    "label": value,
                    "description": "Allowed for this profile",
                    "active": value == current_model,
                }
                for value in allowed_models
            ]
        provider_name = provider.lower()
        if provider_name in {"anthropic", "anthropic_claude"}:
            resolved_current = resolve_model_setting(current_model, provider_name)
            return [
                {
                    "value": value,
                    "label": label,
                    "description": description,
                    "active": value == current_model
                    or resolve_model_setting(value, provider_name) == resolved_current,
                }
                for value, label, description in CLAUDE_MODEL_ALIAS_OPTIONS
            ]
        families: list[tuple[str, str]] = []
        if provider_name in {"openai-codex", "openai", "openai-compatible", "openrouter", "github_copilot"}:
            families.extend(
                [
                    ("gpt-5.4", "OpenAI flagship"),
                    ("gpt-5", "General GPT-5"),
                    ("gpt-4.1", "Stable GPT-4.1"),
                    ("o4-mini", "Fast reasoning"),
                ]
            )
        elif provider_name in {"moonshot", "moonshot-compatible"}:
            families.extend(
                [
                    ("kimi-k2.5", "Moonshot K2.5"),
                    ("kimi-k2-turbo-preview", "Faster Moonshot"),
                ]
            )
        elif provider_name == "dashscope":
            families.extend(
                [
                    ("qwen3.5-flash", "Fast Qwen"),
                    ("qwen3-max", "Strong Qwen"),
                    ("deepseek-r1", "Reasoning model"),
                ]
            )
        elif provider_name == "gemini":
            families.extend(
                [
                    ("gemini-2.5-pro", "Gemini Pro"),
                    ("gemini-2.5-flash", "Gemini Flash"),
                ]
            )
        elif provider_name == "minimax":
            families.extend(
                [
                    ("MiniMax-M2.7", "MiniMax flagship"),
                    ("MiniMax-M2.7-highspeed", "MiniMax fast"),
                ]
            )

        seen: set[str] = set()
        options: list[dict[str, object]] = []
        for value, description in [(current_model, "Current model"), *families]:
            if not value or value in seen:
                continue
            seen.add(value)
            options.append(
                {
                    "value": value,
                    "label": value,
                    "description": description,
                    "active": value == current_model,
                }
            )
        return options
