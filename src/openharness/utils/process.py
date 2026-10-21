"""Async subprocess helpers shared by tools and the Q&A service."""

from __future__ import annotations

import asyncio

__all__ = ["terminate_process"]


async def terminate_process(
    process: asyncio.subprocess.Process,
    *,
    force: bool = False,
    timeout: float = 2.0,
) -> None:
    """Terminate (or kill) *process* and wait for it to exit.

    ``force=True`` sends SIGKILL immediately. Otherwise SIGTERM is sent first
    and SIGKILL follows when the process does not exit within *timeout*.
    Already-exited processes are a no-op.
    """
    if process.returncode is not None:
        return
    if force:
        process.kill()
        await process.wait()
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
