"""Typer application creation and command registration."""

from __future__ import annotations

import typer

from openharness.version import __version__


def _version_callback(value: bool) -> None:
    if value:
        print(f"openharness {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="abc",
    help=(
        "ABC coding assistant — an AI-powered coding assistant.\n\n"
        "Starts an interactive session by default, use -p/--print for non-interactive output."
    ),
    add_completion=False,
    rich_markup_mode="rich",
    invoke_without_command=True,
)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

mcp_app = typer.Typer(name="mcp", help="Manage MCP servers")
plugin_app = typer.Typer(name="plugin", help="Manage plugins")
auth_app = typer.Typer(name="auth", help="Manage authentication")
provider_app = typer.Typer(name="provider", help="Manage provider profiles")
config_app = typer.Typer(name="config", help="Show or update settings")
cron_app = typer.Typer(name="cron", help="Manage cron scheduler and jobs")
autopilot_app = typer.Typer(name="autopilot", help="Manage repo autopilot")

app.add_typer(mcp_app)
app.add_typer(plugin_app)
app.add_typer(auth_app)
app.add_typer(provider_app)
app.add_typer(config_app)
app.add_typer(cron_app)
app.add_typer(autopilot_app)


# Import command modules last so their decorators attach to the Typer app and
# sub-apps defined above.
from openharness.cli.commands import (  # noqa: E402,F401
    auth,
    autopilot,
    config,
    cron,
    mcp,
    plugin,
    provider,
    setup,
)
from openharness.cli.main import main  # noqa: E402,F401
