"""Logging setup for the qaapp web service."""

from __future__ import annotations

import logging


def configure_logging() -> None:
    """Configure INFO-level console logging before constructing the service.

    The service logs the effective config files at startup; without this the
    default root logger (WARNING) swallows them.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    logging.getLogger("openharness.api.openai_client").setLevel(logging.INFO)
    logging.getLogger("qaapp.server").setLevel(logging.INFO)
