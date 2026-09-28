"""Test support for the external-agents plugin tool."""

from __future__ import annotations

import sys
from pathlib import Path

# Make the plugin tool module importable without depending on plugin auto-loading.
PLUGIN_TOOLS_DIR = (
    Path(__file__).resolve().parents[2] / "plugins" / "external-agents" / "tools"
)
if str(PLUGIN_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_TOOLS_DIR))
