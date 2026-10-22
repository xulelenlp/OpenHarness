"""Session/approval/turn orchestration for ``QaWebService``."""

from __future__ import annotations

from qaapp.server.runtime.approvals import ApprovalsMixin
from qaapp.server.runtime.attachments import AttachmentsMixin
from qaapp.server.runtime.bundle import BundleMixin
from qaapp.server.runtime.events import EventsMixin
from qaapp.server.runtime.execution import ExecutionMixin
from qaapp.server.runtime.sessions import SessionsMixin
from qaapp.server.runtime.startup import StartupMixin
from qaapp.server.runtime.turns import TurnsMixin


class RuntimeMixin(
    StartupMixin,
    SessionsMixin,
    BundleMixin,
    EventsMixin,
    ApprovalsMixin,
    TurnsMixin,
    AttachmentsMixin,
    ExecutionMixin,
):
    """Session/approval/turn orchestration shared by the web service."""


__all__ = ["RuntimeMixin"]
