"""高危操作门禁测试。"""

from openharness.config.settings import PermissionSettings
from openharness.permissions.modes import PermissionMode
from qaapp.config import DangerousActions
from qaapp.dangerous import (
    ACTION_ALLOW,
    ACTION_CONFIRM,
    ACTION_DENY,
    DangerousActionGate,
    DangerousPermissionChecker,
)


def _gate(rules: list[dict]) -> DangerousActionGate:
    return DangerousActionGate(DangerousActions.model_validate({"rules": rules}))


def test_tool_level_confirm():
    gate = _gate([{"tool": "file_write", "action": "confirm"}])
    assert gate.evaluate("file_write", is_read_only=False) == ACTION_CONFIRM
    assert gate.evaluate("file_read", is_read_only=False) is None


def test_command_deny_wins():
    gate = _gate([{"tool": "bash", "action": "confirm", "deny_commands": ["rm -rf *"]}])
    assert gate.evaluate("bash", is_read_only=False, command="rm -rf /x") == ACTION_DENY
    assert gate.evaluate("bash", is_read_only=False, command="ls") == ACTION_CONFIRM


def test_path_scope_and_deny():
    gate = _gate([{"tool": "*", "action": "deny", "paths": ["**/*.pem"]}])
    # 只读访问敏感路径同样拒绝
    assert gate.evaluate("file_read", is_read_only=True, file_path="/x/a.pem") == ACTION_DENY
    # 不命中路径则规则不适用
    assert gate.evaluate("file_read", is_read_only=True, file_path="/x/a.txt") is None


def test_allow_action():
    gate = _gate([{"tool": "file_write", "action": "allow"}])
    assert gate.evaluate("file_write", is_read_only=False) == ACTION_ALLOW


def test_deny_beats_confirm_across_rules():
    gate = _gate(
        [
            {"tool": "file_write", "action": "confirm"},
            {"tool": "*", "action": "deny", "paths": ["**/*.pem"]},
        ]
    )
    assert gate.evaluate("file_write", is_read_only=False, file_path="/x/a.pem") == ACTION_DENY


def test_checker_dangerous_confirm_overrides_full_auto():
    gate = _gate([{"tool": "file_write", "action": "confirm"}])
    checker = DangerousPermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO), gate)
    decision = checker.evaluate("file_write", is_read_only=False)
    assert not decision.allowed
    assert decision.requires_confirmation


def test_checker_sensitive_path_always_denied():
    gate = _gate([{"tool": "file_read", "action": "allow"}])
    checker = DangerousPermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO), gate)
    decision = checker.evaluate("file_read", is_read_only=True, file_path="/home/u/.ssh/id_rsa")
    assert not decision.allowed


def test_checker_unmatched_falls_back_to_mode():
    gate = _gate([])
    checker = DangerousPermissionChecker(PermissionSettings(mode=PermissionMode.PLAN), gate)
    decision = checker.evaluate("file_write", is_read_only=False)
    assert not decision.allowed
    assert not decision.requires_confirmation


def test_conversation_dir_write_is_auto_allowed(tmp_path):
    """会话临时目录内的写文件免确认；目录外仍需确认；执行类工具不受影响。"""
    from pathlib import Path

    root = Path(tmp_path) / "conversations" / "abc123abc123"
    (root / "scripts").mkdir(parents=True)
    secret = Path(tmp_path) / "outside.txt"

    gate = _gate(
        [
            {"tool": "write_file", "action": "confirm"},
            {"tool": "bash", "action": "confirm"},
        ]
    )
    checker = DangerousPermissionChecker(
        PermissionSettings(mode=PermissionMode.DEFAULT),
        gate,
        conversation_root=root,
        auto_allow_tools=["write_file", "edit_file"],
    )

    inside = checker.evaluate(
        "write_file", is_read_only=False, file_path=str(root / "scripts" / "analyze.py")
    )
    assert inside.allowed and "会话临时目录" in inside.reason

    outside = checker.evaluate("write_file", is_read_only=False, file_path=str(secret))
    assert not outside.allowed and outside.requires_confirmation

    # 执行类工具（bash / run_script）即使目标在会话目录内也仍需确认
    bash = checker.evaluate("bash", is_read_only=False, command="python scripts/a.py")
    assert not bash.allowed and bash.requires_confirmation


def test_conversation_dir_does_not_override_sensitive_or_hard_deny(tmp_path):
    from pathlib import Path

    root = Path(tmp_path) / "conversations" / "abc123abc123"
    (root / ".ssh").mkdir(parents=True)

    gate = _gate([])
    checker = DangerousPermissionChecker(
        PermissionSettings(mode=PermissionMode.DEFAULT, denied_tools=["edit_file"]),
        gate,
        conversation_root=root,
        auto_allow_tools=["write_file", "edit_file"],
    )
    # 敏感路径恒拒
    sensitive = checker.evaluate(
        "write_file", is_read_only=False, file_path=str(root / ".ssh" / "id_rsa")
    )
    assert not sensitive.allowed
    # settings 硬拒绝优先于会话目录放行
    denied = checker.evaluate(
        "edit_file", is_read_only=False, file_path=str(root / "scripts" / "a.py")
    )
    assert not denied.allowed and not denied.requires_confirmation


def test_traversal_out_of_conversation_dir_still_confirms(tmp_path):
    from pathlib import Path

    root = Path(tmp_path) / "conversations" / "abc123abc123"
    root.mkdir(parents=True)
    gate = _gate([{"tool": "write_file", "action": "confirm"}])
    checker = DangerousPermissionChecker(
        PermissionSettings(mode=PermissionMode.DEFAULT),
        gate,
        conversation_root=root,
        auto_allow_tools=["write_file"],
    )
    escaped = checker.evaluate(
        "write_file",
        is_read_only=False,
        file_path=str(root / ".." / ".." / "evil.txt"),
    )
    assert not escaped.allowed and escaped.requires_confirmation
