"""qaapp 配置模型与加载器。

三份配置文件均放置在 ohmo workspace 根目录（``OHMO_WORKSPACE`` / ``~/.ohmo``），
缺失时回退到内置默认值：

- ``webapp.json``           —— Web 服务配置
- ``agents.json``           —— Agent 注册表与路由规则
- ``dangerous-actions.json``—— 高危操作策略
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from ohmo.workspace import get_workspace_root

# ---------------------------------------------------------------------------
# 路径助手
# ---------------------------------------------------------------------------

WEBAPP_FILENAME = "webapp.json"
AGENTS_FILENAME = "agents.json"
DANGEROUS_FILENAME = "dangerous-actions.json"

KIND_EXTERNAL = "external"
KIND_SUBAGENT = "subagent"
KIND_HARNESS = "harness"
AGENT_KINDS = (KIND_EXTERNAL, KIND_SUBAGENT, KIND_HARNESS)

DANGEROUS_ACTIONS = ("allow", "confirm", "deny")
ROUTING_MODES = ("rule_then_llm", "llm", "rule", "explicit")


def get_webapp_config_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / WEBAPP_FILENAME


def get_agents_config_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / AGENTS_FILENAME


def get_dangerous_config_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / DANGEROUS_FILENAME


# ---------------------------------------------------------------------------
# Web 服务配置
# ---------------------------------------------------------------------------


class ConversationPoolConfig(BaseModel):
    """会话池配置。"""

    max_active_bundles: int = 100
    idle_timeout_seconds: int = 86400


#: 允许上传的文档后缀（解析器支持的范围）。
DEFAULT_ATTACHMENT_SUFFIXES = [
    ".txt",
    ".md",
    ".csv",
    ".docx",
    ".xlsx",
    ".xls",
    ".json",
    ".log",
    ".yaml",
    ".yml",
    ".xml",
]

#: 会话目录内「写文件」默认免确认的工具（执行类工具 bash / run_script 不在此列）。
DEFAULT_AUTO_ALLOW_WRITE_TOOLS = ["write_file", "edit_file", "notebook_edit"]


class AttachmentsConfig(BaseModel):
    """``webapp.json:attachments`` —— 会话附件上传与解析配置。"""

    enabled: bool = True
    #: 单条消息（单次上传请求）最多几个文件。
    max_files_per_message: int = 2
    #: 单个文件大小上限（MB）。
    max_file_mb: float = 20.0
    #: 单个会话累计附件数上限（防止磁盘被写满）。
    max_files_per_conversation: int = 50
    allowed_suffixes: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ATTACHMENT_SUFFIXES)
    )
    #: 解析出的纯文本落盘上限（字符）；超出部分截断。
    max_extract_chars: int = 400_000
    #: 每个附件注入提示词的节选长度（字符）。
    preview_chars: int = 1_500
    #: 「会话上下文块」总长度上限（字符）。
    context_max_chars: int = 8_000
    #: Excel/CSV 每个工作表最多解析多少行（超出截断并标注）。
    max_excel_rows_per_sheet: int = 2_000


class ConversationWorkspaceConfig(BaseModel):
    """``webapp.json:conversation_workspace`` —— 会话临时目录与脚本执行配置。"""

    #: 会话目录根；空 = <OHMO_WORKSPACE>/conversations
    root: str = ""
    #: 会话目录内这些工具的写操作免确认（bash / run_script 默认仍需确认）。
    auto_allow_write_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_AUTO_ALLOW_WRITE_TOOLS)
    )
    #: 会话目录文件清单最多列几条给模型。
    inventory_limit: int = 40
    #: run_script 默认/最大超时（秒）。
    script_timeout_seconds: int = 300
    script_code_max_chars: int = 200_000
    script_output_max_chars: int = 40_000


class WebappConfig(BaseModel):
    """``webapp.json`` —— Web 服务配置。"""

    #: 默认监听所有网卡，便于同一局域网内的 APP / 其他设备直接访问。
    #: 注意：这会扩大暴露面，务必设置 ``api_tokens`` 并在可信网络中部署
    #: （详见 README §9.1 鉴权暴露面）。如需仅本机访问，改回 ``127.0.0.1``。
    host: str = "0.0.0.0"
    port: int = 8787
    #: 为空 = 继承 settings.json 的 active_profile；否则显式指定 profile。
    provider_profile: str = ""
    api_tokens: list[str] = Field(default_factory=list)
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    static_dir: str = ""
    conversation: ConversationPoolConfig = Field(default_factory=ConversationPoolConfig)
    approval_timeout_seconds: int = 300
    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    conversation_workspace: ConversationWorkspaceConfig = Field(
        default_factory=ConversationWorkspaceConfig
    )

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 0 <= value <= 65535:
            raise ValueError("port must be in 0..65535")
        return value


# ---------------------------------------------------------------------------
# Agent 注册表
# ---------------------------------------------------------------------------


class RoutingRule(BaseModel):
    """关键词路由规则。"""

    agent: str
    keywords: list[str] = Field(default_factory=list)
    priority: int = 0


class RoutingConfig(BaseModel):
    """路由策略。"""

    mode: str = "rule_then_llm"
    llm_model: str = ""
    rules: list[RoutingRule] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        if value not in ROUTING_MODES:
            raise ValueError(f"routing.mode must be one of {ROUTING_MODES}")
        return value


class ExternalAgentSpec(BaseModel):
    """``kind=external`` 的**内联**访问配置。

    目的是让 ``agents.json`` 成为**唯一需要维护的 Agent 文件**：外部 Agent 的
    endpoint / method / headers / request_body 直接写在这里，qaapp 在会话 bundle
    建好后注入 ``external_agent`` 工具（内存注入，不再生成第二份配置文件）。

    字段名与 ``plugins/external-agents`` 的 ``AgentConfig`` 对齐，因此旧的
    ``external-agents.json`` 里的 ``config`` / ``response`` 两段可以整段拷过来。
    """

    #: 传输类型：http | stdio（与插件 transport 注册表一致）。
    type: str = "http"
    enabled: bool = True
    #: auto（默认）= 用 qaapp 的会话 ID；固定值 = 每次都复用该 ID。
    #: 对端「会话创建失败」时，填一个已知可用的会话 ID 即可绕过创建过程。
    conversation_id: str = "auto"
    timeout_seconds: float = 180.0
    max_response_chars: int = 200_000
    progress_max_chars: int = 4_000
    #: 响应解析：{"mode": "auto|sse|ndjson|text", "json_text_paths": [...], "extract_tags": [...]}
    response: dict[str, Any] = Field(default_factory=dict)
    #: 传输配置：http 需要 endpoint/method/headers/request_body；stdio 需要 command。
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_transport(self) -> ExternalAgentSpec:
        if self.type == "http" and not str(self.config.get("endpoint") or "").strip():
            raise ValueError("external.type=http 时必须提供 external.config.endpoint")
        if self.type == "stdio" and not str(self.config.get("command") or "").strip():
            raise ValueError("external.type=stdio 时必须提供 external.config.command")
        return self


class AgentEntry(BaseModel):
    """一个可接入的 Agent 定义。

    ``kind`` 决定派发方式：

    - ``external`` —— 调用外部智能体。**推荐**直接用 ``external`` 内联完整的
      访问配置（唯一维护文件 = ``agents.json``）；``external_ref`` 仅用于兼容
      旧的 ``external-agents.json``（指向其中的 agent id）。
    - ``subagent`` —— 复用 ``coordinator.agent_definitions``（``subagent_type``
      指向 AgentDefinition 名）
    - ``harness``  —— 以 ``system_prompt``/``tools``/``permission_mode`` 内联运行
    """

    id: str
    kind: str = KIND_HARNESS
    name: str = ""
    description: str = ""
    enabled: bool = True

    # kind=external
    #: 兼容旧配置：指向 external-agents.json 里的 agent id（与 id 不同时才需要）。
    external_ref: str = ""
    #: 推荐：内联外部 Agent 的完整访问配置（endpoint/request_body/response…）。
    external: ExternalAgentSpec | None = None
    # kind=subagent
    subagent_type: str = ""
    # kind=harness
    system_prompt: str = ""
    system_prompt_file: str = ""
    tools: list[str] | None = None  # None/['*'] = 全部工具
    disallowed_tools: list[str] = Field(default_factory=list)
    model: str = ""
    max_turns: int | None = None
    permission_mode: str | None = None

    @property
    def external_agent_id(self) -> str:
        """调用 external_agent 时使用的 id（内联优先，其次 external_ref，最后 id）。"""
        if self.external is not None:
            return self.external_ref or self.id
        return self.external_ref or self.id

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if value not in AGENT_KINDS:
            raise ValueError(f"agent kind must be one of {AGENT_KINDS}")
        return value


class AgentRegistry(BaseModel):
    """``agents.json`` —— Agent 注册表与路由规则。"""

    default_agent: str = ""
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    agents: list[AgentEntry] = Field(default_factory=list)

    def get(self, agent_id: str) -> AgentEntry | None:
        return next((a for a in self.agents if a.id == agent_id), None)

    def enabled_agents(self) -> list[AgentEntry]:
        return [a for a in self.agents if a.enabled]


# ---------------------------------------------------------------------------
# 高危操作策略
# ---------------------------------------------------------------------------


class DangerousRule(BaseModel):
    """一条高危操作规则。

    规则语义：

    - ``tool`` 为工具名或 ``*``（通配），决定规则作用于哪些工具。
    - ``action`` 为该规则的策略（allow / confirm / deny）。
    - ``paths`` 非空时，仅当 ``file_path`` 命中其中任一 glob 模式才生效；
      为空时对 ``tool`` 的所有调用生效。
    - ``deny_commands`` / ``confirm_commands`` 为命令级覆盖：命中
      ``deny_commands`` 一律拒绝，命中 ``confirm_commands`` 一律需确认，
      二者优先级高于 ``action``（deny 高于 confirm）。

    示例：

    .. code-block:: json

        {"tool": "file_write", "action": "confirm"},
        {"tool": "bash", "action": "confirm",
         "deny_commands": ["rm -rf *"], "confirm_commands": ["pip install*"]},
        {"tool": "*", "action": "deny", "paths": ["**/*.pem", "**/.env"]}
    """

    tool: str
    action: str = "confirm"
    paths: list[str] = Field(default_factory=list)
    deny_commands: list[str] = Field(default_factory=list)
    confirm_commands: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        if value not in DANGEROUS_ACTIONS:
            raise ValueError(f"action must be one of {DANGEROUS_ACTIONS}")
        return value


class DangerousActions(BaseModel):
    """``dangerous-actions.json`` —— 高危操作策略。

    仅声明高危操作本身；未被任何规则命中的操作回退到 settings.json 的
    权限体系（``permission.mode`` 等）处理。
    """

    approval_timeout_seconds: int = 300
    rules: list[DangerousRule] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


def _load_json_model(path: Path, model: type[BaseModel]) -> tuple[BaseModel, str | None]:
    """读取并校验一个 JSON 配置文件。

    返回 ``(model, error)``；文件缺失返回默认模型与 None；内容非法返回默认模型
    与错误信息（不抛出，便于服务启动时仅告警）。
    """
    if not path.exists():
        return model(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return model(), f"无法读取 {path}: {exc}"
    try:
        return model.model_validate(raw), None
    except (ValueError, OSError) as exc:  # pydantic ValidationError 等
        return model(), f"配置非法 {path}: {exc}"


def load_webapp_config(workspace: str | Path | None = None) -> WebappConfig:
    config, _ = _load_json_model(get_webapp_config_path(workspace), WebappConfig)
    return config  # type: ignore[return-value]


def load_agent_registry(workspace: str | Path | None = None) -> AgentRegistry:
    registry, _ = _load_json_model(get_agents_config_path(workspace), AgentRegistry)
    return registry  # type: ignore[return-value]


def load_dangerous_actions(workspace: str | Path | None = None) -> DangerousActions:
    config, _ = _load_json_model(get_dangerous_config_path(workspace), DangerousActions)
    return config  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _validation_error(path: Path, exc: Exception) -> str:
    return f"配置非法 {path}: {exc}"


def validate_webapp_config(workspace: str | Path | None = None) -> list[str]:
    """返回 webapp.json 的校验错误列表（空 = 合法）。"""
    path = get_webapp_config_path(workspace)
    if not path.exists():
        return []
    try:
        WebappConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]
    return []


def validate_agent_registry(workspace: str | Path | None = None) -> list[str]:
    """返回 agents.json 的校验错误列表（空 = 合法）。"""
    path = get_agents_config_path(workspace)
    if not path.exists():
        return []
    errors: list[str] = []
    try:
        registry = AgentRegistry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]

    ids: set[str] = set()
    for agent in registry.agents:
        if not agent.id:
            errors.append(f"{path}: agent 缺少 id")
            continue
        if agent.id in ids:
            errors.append(f"{path}: agent id 重复: {agent.id}")
        ids.add(agent.id)
        if agent.kind == KIND_EXTERNAL and not agent.external_ref and agent.external is None:
            errors.append(
                f"{path}: agent '{agent.id}' 为 external 但既没有 external 内联配置，"
                "也没有 external_ref"
            )
        if agent.kind == KIND_SUBAGENT and not agent.subagent_type:
            errors.append(f"{path}: agent '{agent.id}' 为 subagent 但缺少 subagent_type")

    known_ids = {agent.id for agent in registry.agents}
    if registry.default_agent and registry.default_agent not in known_ids:
        errors.append(f"{path}: default_agent '{registry.default_agent}' 未在 agents 中定义")
    for rule in registry.routing.rules:
        if rule.agent not in known_ids:
            errors.append(f"{path}: 路由规则指向未知 agent: {rule.agent}")
    return errors


def validate_dangerous_actions(workspace: str | Path | None = None) -> list[str]:
    """返回 dangerous-actions.json 的校验错误列表（空 = 合法）。"""
    path = get_dangerous_config_path(workspace)
    if not path.exists():
        return []
    try:
        DangerousActions.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError) as exc:
        return [_validation_error(path, exc)]
    return []


def config_status(workspace: str | Path | None = None) -> dict[str, Any]:
    """返回三份配置的存在性与校验结果摘要。"""
    paths = {
        "webapp": get_webapp_config_path(workspace),
        "agents": get_agents_config_path(workspace),
        "dangerous": get_dangerous_config_path(workspace),
    }
    validators = {
        "webapp": validate_webapp_config,
        "agents": validate_agent_registry,
        "dangerous": validate_dangerous_actions,
    }
    result: dict[str, Any] = {"workspace": str(get_workspace_root(workspace))}
    for name, path in paths.items():
        result[name] = {
            "path": str(path),
            "exists": path.exists(),
            "errors": validators[name](workspace),
        }
    return result


__all__ = [
    "AgentEntry",
    "AgentRegistry",
    "AttachmentsConfig",
    "ConversationPoolConfig",
    "ConversationWorkspaceConfig",
    "DangerousActions",
    "DangerousRule",
    "ExternalAgentSpec",
    "RoutingConfig",
    "RoutingRule",
    "WebappConfig",
    "config_status",
    "get_agents_config_path",
    "get_dangerous_config_path",
    "get_webapp_config_path",
    "load_agent_registry",
    "load_dangerous_actions",
    "load_webapp_config",
    "validate_agent_registry",
    "validate_dangerous_actions",
    "validate_webapp_config",
]
