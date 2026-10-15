"""高危操作门禁。

将 ``dangerous-actions.json`` 的声明式策略接入 OpenHarness 的权限流程。
能力不阉割——文件写入、删除、bash 等工具仍然保留，只是被策略 + 审批流程
放行或阻断。

职责划分：
- :class:`DangerousActionGate` 负责从配置解析出单个工具调用的策略
  （deny / confirm / allow / 无规则命中）。
- :class:`DangerousPermissionChecker` 在既有 :class:`PermissionChecker`
  之上叠加危险操作策略，供 ``QueryEngine`` 在工具执行前调用。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openharness.config.settings import PermissionSettings
from openharness.permissions.checker import (
    SENSITIVE_PATH_PATTERNS,
    PermissionChecker,
    PermissionDecision,
    _policy_match_paths,
)
from openharness.permissions.modes import PermissionMode
from qaapp.config import DangerousActions, DangerousRule

ACTION_DENY = "deny"
ACTION_CONFIRM = "confirm"
ACTION_ALLOW = "allow"


class DangerousActionGate:
    """解析 ``dangerous-actions.json`` 并判断单个调用的策略。"""

    def __init__(self, config: DangerousActions) -> None:
        self._config = config
        self._rules: list[DangerousRule] = config.rules.copy()
        self.last_reason = ""

    @property
    def config(self) -> DangerousActions:
        return self._config

    def _matching_rules(self, tool_name: str) -> list[DangerousRule]:
        """返回命中的规则：精确工具名在前，``*`` 通配在后。"""
        exact = [rule for rule in self._rules if rule.tool == tool_name]
        wildcard = [rule for rule in self._rules if rule.tool == "*"]
        return exact + wildcard

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> str | None:
        """返回 ``deny`` / ``confirm`` / ``allow`` / ``None``（无规则命中）。

        聚合所有命中规则，优先级 deny > confirm > allow；命中后写入
        ``last_reason`` 供上层展示。
        """
        self.last_reason = ""
        best_action: str | None = None
        for rule in self._matching_rules(tool_name):
            decision = _rule_decision(
                rule,
                is_read_only=is_read_only,
                file_path=file_path,
                command=command,
            )
            if decision is None:
                continue
            action, reason = decision
            if action == ACTION_DENY:
                self.last_reason = reason
                return ACTION_DENY
            if action == ACTION_CONFIRM:
                self.last_reason = reason
                best_action = ACTION_CONFIRM
            elif action == ACTION_ALLOW and best_action is None:
                self.last_reason = reason
                best_action = ACTION_ALLOW
        return best_action


def _match_any(value: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(value, pattern):
            return True
    return False


def _path_matches(value: str, patterns: list[str]) -> bool:
    """对路径的多种形式做 glob 匹配（含目录尾斜杠形式）。"""
    if not value:
        return False
    normalized = value.rstrip("/")
    candidates = (value, normalized, normalized + "/")
    for candidate in candidates:
        if _match_any(candidate, patterns):
            return True
    return False


def _rule_decision(
    rule: DangerousRule,
    *,
    is_read_only: bool,
    file_path: str | None,
    command: str | None,
) -> tuple[str, str] | None:
    """计算单条规则对本次调用的动作；规则不适用时返回 None。

    作用域判定：``paths`` 非空时仅当 file_path 命中才生效；否则对 tool 的所有
    调用生效。``paths`` 为路径级拒绝/作用域，与只读无关——只读访问敏感路径
    同样应被拒绝；但命令级覆盖与整体 ``action`` 只对“写”操作有意义，只读
    工具跳过（避免把只读工具误判为高危操作）。
    """
    # 作用域：paths 非空则要求 file_path 命中。
    if rule.paths and not (file_path and _path_matches(file_path, rule.paths)):
        return None

    # 路径级拒绝：无论读写，命中即拒（防止读取敏感文件）。
    # 注意：这里 rule.paths 本身即作用域，action=deny 时对整个作用域拒绝。
    if rule.action == ACTION_DENY and rule.paths:
        return ACTION_DENY, _reason(rule, "路径命中拒绝规则", file_path)

    # 以下为命令级覆盖与整体 action，仅对写操作有意义。
    if is_read_only:
        return None

    if command and _match_any(command, rule.deny_commands):
        return ACTION_DENY, _reason(rule, "命令命中拒绝规则", command)
    if command and _match_any(command, rule.confirm_commands):
        return ACTION_CONFIRM, _reason(rule, "命令命中需确认规则", command)
    return rule.action, _reason(rule, "命中高危操作规则", rule.tool)


def _reason(rule: DangerousRule, prefix: str, detail: str | None) -> str:
    base = rule.reason or f"高危操作 {rule.tool} 需按策略处理"
    if detail:
        return f"{prefix}: {detail}。{base}"
    return base


def _is_within(path: str | Path, root: Path) -> bool:
    """判断 path 是否位于 root 目录内（解析符号链接后再比较，防越界）。"""
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


class DangerousPermissionChecker(PermissionChecker):
    """在基础权限检查之上叠加危险操作策略。

    优先级（从高到低）：
    1. 敏感路径（.ssh/.aws 等）—— 恒拒，不可覆盖
    2. settings.json 的 denied_tools / denied_commands / 路径拒绝规则 —— 恒拒
    3. 会话临时目录内的写操作 —— 免确认（可配置，见 ``conversation_workspace``）
    4. dangerous-actions 的 deny —— 拒
    5. dangerous-actions 的 confirm —— 需确认
    6. dangerous-actions 的 allow —— 放行
    7. settings.json 的 allowed_tools —— 放行
    8. 基础模式逻辑（full_auto 放行 / plan 阻断 / default 写操作需确认 / 只读放行）
    """

    def __init__(
        self,
        settings: PermissionSettings,
        gate: DangerousActionGate,
        *,
        conversation_root: str | Path | None = None,
        auto_allow_tools: Iterable[str] = (),
    ) -> None:
        super().__init__(settings)
        self._gate = gate
        #: 会话临时目录：该目录内的「写文件」类工具免确认，避免每次生成脚本都弹窗。
        self._conversation_root = (
            Path(conversation_root).expanduser().resolve() if conversation_root else None
        )
        self._auto_allow_tools = frozenset(auto_allow_tools)

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        # 1. 敏感路径恒拒
        if file_path:
            for candidate_path in _policy_match_paths(file_path):
                for pattern in SENSITIVE_PATH_PATTERNS:
                    if fnmatch.fnmatch(candidate_path, pattern):
                        return PermissionDecision(
                            allowed=False,
                            reason=(
                                f"Access denied: {file_path} is a sensitive credential path "
                                f"(matched built-in pattern '{pattern}')"
                            ),
                        )

        # 2. settings 硬拒绝（denied_tools / denied_commands / 路径拒绝）
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(allowed=False, reason=f"{tool_name} is explicitly denied")
        if command:
            for pattern in self._settings.denied_commands:
                if isinstance(pattern, str) and fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(
                        allowed=False, reason=f"Command matches deny pattern: {pattern}"
                    )
        if file_path and self._path_rules:
            for candidate_path in _policy_match_paths(file_path):
                for rule in self._path_rules:
                    if fnmatch.fnmatch(candidate_path, rule.pattern) and not rule.allow:
                        return PermissionDecision(
                            allowed=False,
                            reason=f"Path {file_path} matches deny rule: {rule.pattern}",
                        )

        # 3. 会话临时目录内的写操作免确认
        #    「写文件」是可逆的低风险动作，而 agent 生成脚本会反复写同一个目录；
        #    执行类工具（bash / run_script）不在此名单内，仍走审批。
        if (
            self._conversation_root is not None
            and file_path
            and tool_name in self._auto_allow_tools
            and _is_within(file_path, self._conversation_root)
        ):
            return PermissionDecision(
                allowed=True, reason="会话临时目录内的文件写入免确认"
            )

        # 4-6. dangerous-actions 策略
        action = self._gate.evaluate(
            tool_name,
            is_read_only=is_read_only,
            file_path=file_path,
            command=command,
        )
        if action == ACTION_DENY:
            return PermissionDecision(allowed=False, reason=self._gate.last_reason)
        if action == ACTION_CONFIRM:
            return PermissionDecision(
                allowed=False, requires_confirmation=True, reason=self._gate.last_reason
            )
        if action == ACTION_ALLOW:
            return PermissionDecision(allowed=True, reason="高危操作策略允许")

        # 7. settings allowed_tools
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(allowed=True, reason=f"{tool_name} is explicitly allowed")

        # 8. 基础模式逻辑
        if self._settings.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(allowed=True, reason="Auto mode allows all tools")
        if is_read_only:
            return PermissionDecision(allowed=True, reason="read-only tools are allowed")
        if self._settings.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False, reason="Plan mode blocks mutating tools until the user exits plan mode"
            )
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason="Mutating tools require user confirmation in default mode.",
        )


def build_permission_gate(
    dangerous: DangerousActions,
) -> DangerousActionGate:
    """从配置构建门禁。"""
    return DangerousActionGate(dangerous)


def build_permission_settings(
    dangerous: DangerousActions,
    *,
    permission_mode: str | None = None,
) -> dict[str, Any]:
    """将危险操作策略映射进 ``PermissionSettings`` 兼容字段。

    仅做一次“静态”映射（deny 规则并入 denied_tools/denied_commands/路径拒绝），
    ``confirm`` 与运行时的动态策略由 :class:`DangerousPermissionChecker` 处理。
    返回的 dict 可并入 settings.json 的 permission 覆盖。
    """
    denied_tools: list[str] = []
    denied_commands: list[str] = []
    path_rules: list[dict[str, Any]] = []
    for rule in dangerous.rules:
        # 无作用域、无命令覆盖的整工具 deny → 直接并入 denied_tools。
        if (
            rule.action == ACTION_DENY
            and rule.tool != "*"
            and not rule.paths
            and not rule.deny_commands
        ):
            denied_tools.append(rule.tool)
        denied_commands.extend(rule.deny_commands)
        if rule.action == ACTION_DENY and rule.paths:
            for pattern in rule.paths:
                path_rules.append({"pattern": pattern, "allow": False})
    result: dict[str, Any] = {
        "denied_tools": denied_tools,
        "denied_commands": denied_commands,
        "path_rules": path_rules,
    }
    if permission_mode:
        result["mode"] = permission_mode
    return result


__all__ = [
    "ACTION_ALLOW",
    "ACTION_CONFIRM",
    "ACTION_DENY",
    "DangerousActionGate",
    "DangerousPermissionChecker",
    "build_permission_gate",
    "build_permission_settings",
]
