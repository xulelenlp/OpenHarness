"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()


@pytest.fixture(autouse=True)
def _restore_process_env():
    """Keep tests isolated from env mutations done by entry points.

    ``openharness.config.settings.apply_project_env()`` (called by the CLI) and
    ``qaapp``'s env layering write into ``os.environ``. Restoring the snapshot
    prevents one test's ``config/backend/.env`` from leaking into later tests.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)
