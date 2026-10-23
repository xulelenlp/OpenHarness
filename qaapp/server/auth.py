"""qaapp 账户登录：密码哈希、用户文件、会话与登录限流。

设计约束（对外网暴露时必须满足）：

- **不存明文密码**：用户文件里只有带盐的 ``scrypt`` 摘要；默认账户只在首次
  生成文件时写入摘要，文件权限固定为 0600。
- **会话在服务端**：Cookie 里是 256 位随机 token，服务端保存用户名 / 角色 /
  过期时间；支持空闲超时与绝对过期，退出即失效，改密后吊销该用户全部会话。
- **防暴力破解**：按用户名和来源 IP 分别计数，超过阈值锁定一段时间。
- **角色最小权限**：``admin`` 可读写，``viewer`` 只允许 GET/HEAD。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16

#: 首次生成用户文件时写入的两个默认账户；生产环境务必用环境变量覆盖后首启。
DEFAULT_ACCOUNTS: tuple[dict[str, str], ...] = (
    {
        "username": "admin",
        "password": "Admin@Qa2026",
        "role": "admin",
        "display_name": "管理员",
    },
    {
        "username": "viewer",
        "password": "Viewer@Qa2026",
        "role": "viewer",
        "display_name": "只读用户",
    },
)

ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
ROLES = {ROLE_ADMIN, ROLE_VIEWER}

_MIN_PASSWORD_LENGTH = 10
#: 同一 IP 的失败阈值放宽到账号阈值的倍数，避免办公网共用出口被轻易锁死。
_IP_ATTEMPT_FACTOR = 4


class AuthError(Exception):
    """可映射成 HTTP 响应的登录/改密错误。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class AuthUser:
    """通过鉴权后的身份。"""

    username: str
    role: str
    display_name: str
    #: ``cookie`` = 登录会话；``bearer`` = 配置里的 api_token（自动化用）。
    via: str = "cookie"

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name,
        }


@dataclass
class _Account:
    username: str
    role: str
    display_name: str
    password_hash: str
    default_password: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name,
            "password_hash": self.password_hash,
            "default_password": self.default_password,
        }


@dataclass
class _Session:
    user: AuthUser
    created: float
    last_seen: float
    expires: float


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``scrypt`` 摘要；格式 ``scrypt$n$r$p$salt$hash``。"""
    salt = salt or secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验摘要；格式非法或算法不匹配时返回 False（不抛异常）。"""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(digest_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# 未知用户名也跑一次 scrypt，避免用响应时间探测账号是否存在。
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class AuthManager:
    """账户与会话的唯一入口；由 ``QaWebService`` 持有一个实例。"""

    def __init__(
        self,
        *,
        enabled: bool,
        users_file: str | Path,
        session_ttl_seconds: int,
        idle_timeout_seconds: int,
        cookie_name: str,
        max_failed_attempts: int,
        lockout_seconds: int,
        trust_proxy_headers: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.users_file = Path(users_file).expanduser()
        self.session_ttl_seconds = int(session_ttl_seconds)
        self.idle_timeout_seconds = int(idle_timeout_seconds)
        self.cookie_name = cookie_name
        self.max_failed_attempts = max(1, int(max_failed_attempts))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self.trust_proxy_headers = bool(trust_proxy_headers)

        self._accounts: dict[str, _Account] = {}
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, list[float]] = {}
        self._seeded = False

        if self.enabled:
            self._load_or_seed()

    # -- 账户文件 -------------------------------------------------------

    @property
    def default_passwords(self) -> bool:
        """是否仍有账户在用默认密码（登录页用来提示尽快修改）。"""
        return any(account.default_password for account in self._accounts.values())

    def _load_or_seed(self) -> None:
        if self.users_file.is_file():
            self._load()
            return
        self._seed()
        logger.warning(
            "登录已启用：已生成默认账户 admin / viewer（密码见部署文档），"
            "用户文件 %s（权限 0600）。请尽快修改密码或重新生成用户文件。",
            self.users_file,
        )

    def _seed(self) -> None:
        accounts: list[_Account] = []
        for spec in DEFAULT_ACCOUNTS:
            password = os.environ.get(
                f"QAAPP_DEFAULT_{spec['username'].upper()}_PASSWORD", spec["password"]
            )
            accounts.append(
                _Account(
                    username=spec["username"],
                    role=spec["role"],
                    display_name=spec["display_name"],
                    password_hash=hash_password(password),
                    default_password=password == spec["password"],
                )
            )
        self._accounts = {account.username: account for account in accounts}
        self._save()
        self._seeded = True

    def _load(self) -> None:
        try:
            payload = json.loads(self.users_file.read_text(encoding="utf-8"))
            accounts = payload["users"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"用户文件无法解析：{self.users_file}（{exc}）") from exc
        loaded: dict[str, _Account] = {}
        for item in accounts:
            username = str(item.get("username", "")).strip()
            role = str(item.get("role", ""))
            password_hash = str(item.get("password_hash", ""))
            if not username or role not in ROLES or not password_hash:
                raise RuntimeError(f"用户文件存在非法账户：{self.users_file}")
            loaded[username] = _Account(
                username=username,
                role=role,
                display_name=str(item.get("display_name") or username),
                password_hash=password_hash,
                default_password=bool(item.get("default_password", False)),
            )
        if not loaded:
            raise RuntimeError(f"用户文件没有账户：{self.users_file}")
        self._accounts = loaded

    def _save(self) -> None:
        payload = {
            "version": 1,
            "users": [account.as_dict() for account in self._accounts.values()],
        }
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".users-", suffix=".json", dir=str(self.users_file.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.users_file)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    # -- 登录 / 会话 ----------------------------------------------------

    def _prune_failures(self, key: str, now: float) -> list[float]:
        recent = [ts for ts in self._failures.get(key, []) if now - ts < self.lockout_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def _ensure_not_locked(self, keys: list[tuple[str, int]], now: float) -> None:
        for key, limit in keys:
            recent = self._prune_failures(key, now)
            if len(recent) >= limit:
                wait = int(self.lockout_seconds - (now - recent[-1]))
                raise AuthError(
                    429,
                    "rate_limited",
                    f"尝试次数过多，请 {max(wait, 1)} 秒后再试",
                    retry_after=max(wait, 1),
                )

    def _record_failure(self, keys: list[tuple[str, int]], now: float) -> None:
        for key, _ in keys:
            self._failures.setdefault(key, []).append(now)

    def _clear_failures(self, keys: list[tuple[str, int]]) -> None:
        for key, _ in keys:
            self._failures.pop(key, None)

    def _sweep_failures(self, now: float) -> None:
        """随机用户名爆破会让失败表无限增长，超过阈值时清掉已过期条目。"""
        if len(self._failures) <= 1000:
            return
        for key, stamps in list(self._failures.items()):
            if not stamps or now - stamps[-1] >= self.lockout_seconds:
                self._failures.pop(key, None)

    def login(self, username: str, password: str, *, ip: str = "") -> tuple[str, AuthUser]:
        """校验账号密码并创建会话；失败抛 :class:`AuthError`。"""
        now = time.time()
        self._sweep_failures(now)
        username = (username or "").strip()
        keys = [
            (f"user:{username.lower()}", self.max_failed_attempts),
            (f"ip:{ip or '-'}", self.max_failed_attempts * _IP_ATTEMPT_FACTOR),
        ]
        self._ensure_not_locked(keys, now)

        account = self._accounts.get(username)
        stored = account.password_hash if account else _DUMMY_HASH
        if not verify_password(password or "", stored) or account is None:
            self._record_failure(keys, now)
            raise AuthError(401, "invalid_credentials", "用户名或密码错误")

        self._clear_failures(keys)
        user = AuthUser(
            username=account.username,
            role=account.role,
            display_name=account.display_name,
        )
        token = self._create_session(user, now)
        return token, user

    def _create_session(self, user: AuthUser, now: float) -> str:
        self._purge_sessions(now)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(
            user=user,
            created=now,
            last_seen=now,
            expires=now + self.session_ttl_seconds,
        )
        if len(self._sessions) > 500:
            # 异常情况下兜底：保留最近活跃的 200 个会话。
            ordered = sorted(self._sessions.items(), key=lambda item: item[1].last_seen)
            for old_token, _ in ordered[: len(self._sessions) - 200]:
                self._sessions.pop(old_token, None)
        return token

    def resolve(self, token: str) -> AuthUser | None:
        """把 Cookie token 还原成用户；过期 / 空闲超时 / 不存在返回 None。"""
        if not token:
            return None
        now = time.time()
        session = self._sessions.get(token)
        if session is None:
            return None
        if now >= session.expires or now - session.last_seen > self.idle_timeout_seconds:
            self._sessions.pop(token, None)
            return None
        session.last_seen = now
        return session.user

    def revoke(self, token: str) -> None:
        self._sessions.pop(token or "", None)

    def revoke_user_sessions(self, username: str) -> None:
        for token, session in list(self._sessions.items()):
            if session.user.username == username:
                self._sessions.pop(token, None)

    def _purge_sessions(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        for token, session in list(self._sessions.items()):
            if now >= session.expires or now - session.last_seen > self.idle_timeout_seconds:
                self._sessions.pop(token, None)

    # -- 改密 -----------------------------------------------------------

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        account = self._accounts.get((username or "").strip())
        if account is None:
            raise AuthError(401, "invalid_credentials", "用户不存在")
        if not verify_password(old_password or "", account.password_hash):
            raise AuthError(400, "wrong_password", "原密码不正确")
        self._validate_new_password(account.username, new_password)
        account.password_hash = hash_password(new_password)
        account.default_password = False
        self._save()
        self.revoke_user_sessions(account.username)

    @staticmethod
    def _validate_new_password(username: str, password: str) -> None:
        if len(password or "") < _MIN_PASSWORD_LENGTH:
            raise AuthError(400, "weak_password", f"新密码至少 {_MIN_PASSWORD_LENGTH} 位")
        if password == username:
            raise AuthError(400, "weak_password", "新密码不能与用户名相同")
        if not any(char.isalpha() for char in password) or not any(
            char.isdigit() for char in password
        ):
            raise AuthError(400, "weak_password", "新密码需同时包含字母和数字")

    # -- 供测试 / 诊断 --------------------------------------------------

    @property
    def seeded(self) -> bool:
        return self._seeded

    @property
    def account_count(self) -> int:
        return len(self._accounts)
