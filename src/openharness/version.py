"""Single source of truth for the package version at runtime.

``pyproject.toml`` remains the release source of truth. When the package is
installed (including editable installs), ``importlib.metadata`` reads that
value; source checkouts without installation fall back to the constant below.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["get_version", "__version__"]

_FALLBACK_VERSION = "0.1.9"


def get_version() -> str:
    """Return the installed distribution version, or the source fallback."""
    try:
        return version("openharness-ai")
    except PackageNotFoundError:
        return _FALLBACK_VERSION


__version__ = get_version()
