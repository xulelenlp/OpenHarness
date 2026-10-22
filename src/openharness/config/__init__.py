"""Configuration system for OpenHarness.

Provides settings management, path resolution, and API key handling.
"""

from openharness.config.paths import (
    find_project_config_file,
    find_project_env_file,
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_env_file_path,
    get_logs_dir,
)
from openharness.config.settings import (
    ProviderProfile,
    Settings,
    apply_project_env,
    auth_source_provider_name,
    default_auth_source_for_provider,
    default_provider_profiles,
    load_settings,
    save_settings,
)

__all__ = [
    "ProviderProfile",
    "Settings",
    "apply_project_env",
    "auth_source_provider_name",
    "default_auth_source_for_provider",
    "default_provider_profiles",
    "find_project_config_file",
    "find_project_env_file",
    "get_config_dir",
    "get_config_file_path",
    "get_data_dir",
    "get_env_file_path",
    "get_logs_dir",
    "load_settings",
    "save_settings",
]
