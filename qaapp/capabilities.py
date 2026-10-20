"""动态能力清单：把 ``agents.json`` 变成模型可读的编排提示。

**为什么需要它**：qaapp 的目标是「用户提出任意任务，系统自主决定直接回答、
调用哪个专业智能体、还是在服务器 Python 环境里执行」。如果通用助手的系统提示
里手写一份 Agent 列表，每新增一个智能体都要改提示词，既容易漏，也会让提示词与
配置漂移。

本模块在**每轮** harness 执行前，根据当前注册表与「本轮实际可用的工具」生成
一段能力清单，追加到系统提示后面。因此：

- 新增 ``agents.json`` 条目 → 下一轮模型就能看到，无需改代码 / 改提示词；
- ``tools`` / ``disallowed_tools`` 裁掉了某个工具 → 清单里不会出现对应能力，
  模型不会尝试调用不存在的工具；
- 专业智能体的 ``description`` 就是路由依据，写清楚「做什么 + 什么时候用」即可。
"""

from __future__ import annotations

from collections.abc import Iterable

from qaapp.config import KIND_EXTERNAL, AgentEntry, AgentRegistry

#: 决策顺序：这是产品「自主编排」的核心策略，放在清单最前面。
_DECISION_RULES = """\
### 决策顺序（按此顺序判断，不要跳步）
1. **能直接回答就直接回答**：通用知识、写作、代码、推理、简单换算等，用你自身能力完成，不要为了“显得专业”而调用工具。
2. **需要业务系统里的数据/指标/政策时，选专业智能体**：按下方「专业智能体」的 description 匹配用户意图；用户明确点名某个智能体时优先用它。
3. **需要真实计算或处理文件时，用 run_script 在服务器执行 Python**：Excel/CSV 统计、批量转换、生成图表/报告/新文件等，不要靠心算或让用户自己跑脚本。
4. **需要最新外部事实时，用 web_search / web_fetch 查证**，并说明来源；不要编造。
5. **信息不足时，只问一个最小澄清问题**：能从对话、附件、会话目录推断的，不要重复问用户。
6. **绝不向用户索要 token、接口地址、内部实现细节**；这些属于部署配置，不是用户任务的一部分。
7. **多步任务边做边报告**：先完成能确定的部分；某一步失败时说明原因、给出替代方案，不要假装成功。
"""


def _external_agents(
    registry: AgentRegistry, current_agent_id: str
) -> list[AgentEntry]:
    """当前可调度的 external Agent（排除正在执行的 harness 自身）。"""
    return [
        agent
        for agent in registry.enabled_agents()
        if agent.kind == KIND_EXTERNAL and agent.id != current_agent_id
    ]


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def build_capability_catalog(
    registry: AgentRegistry,
    *,
    current_agent_id: str = "",
    available_tools: Iterable[str] | None = None,
) -> str:
    """生成追加到 harness 系统提示的能力清单。

    ``available_tools`` 为本轮实际可用的工具名；传 ``None`` 表示不做工具过滤
    （纯单测 / 未接工具策略时）。返回空串表示没有可编排能力，调用方不追加即可。
    """
    tools = set(available_tools) if available_tools is not None else None

    def _has(name: str) -> bool:
        return tools is None or name in tools

    sections: list[str] = []

    external = _external_agents(registry, current_agent_id)
    if external and _has("external_agent"):
        lines = [
            "### 专业智能体（调用 external_agent 工具，`agent` 参数用反引号里的 id）",
        ]
        for agent in external:
            name = _clean(agent.name) or agent.id
            description = _clean(agent.description) or "（未填写 description，路由时请谨慎）"
            lines.append(f"- `{agent.id}`（{name}）：{description}")
        lines.append(
            "选择规则：先判断用户意图属于哪个 description 的领域，再选 id；"
            "多个都沾边时选 description 最具体、最贴近用户目标的一个。"
            "把用户原话作为 message 传入，不要把问题改写成另一个问题。"
        )
        sections.append("\n".join(lines))

    if _has("run_script"):
        sections.append(
            "### 本机执行能力（run_script）\n"
            "- `run_script`：在本会话目录执行 Python/Node 脚本（服务端共享环境，已装 "
            "pandas / openpyxl / python-docx）。脚本自动写入 `scripts/`，工作目录是会话目录根，"
            "因此可用 `uploads/<文件名>` 读用户上传的文件、把产物写到 `outputs/`。\n"
            "- 适用：数据统计与透视、Excel/CSV 批量处理、格式转换、生成图表/报告/新文件、"
            "需要精确计算或可复现结果的任务。\n"
            "- 不适用：纯知识问答、写作、无需计算的解释；这些直接回答。\n"
            "- 执行类工具可能触发审批；被拒绝时向用户说明需要什么权限，不要绕道尝试其他执行方式。"
        )

    if not sections:
        return ""

    parts = [
        "## 可调度能力（由 agents.json 自动生成，新增 Agent 无需改本段）",
        _DECISION_RULES,
    ]
    parts.extend(sections)
    parts.append(
        "### 兜底\n"
        "- 如果用户需要的能力不在上面（缺少对应智能体 / 工具），明确说明「当前缺少哪项能力」，"
        "并给出可执行的替代方案；不要编造数据，也不要声称调用了不存在的智能体。"
    )
    return "\n\n".join(parts)


__all__ = ["build_capability_catalog"]
