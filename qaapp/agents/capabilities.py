"""动态能力清单：把 ``agents.json`` 与数据/报告工具变成模型可读的编排提示。

**为什么需要它**：qaapp 的目标是「用户提出任意任务，系统自主决定直接回答、
调用哪个专业智能体、还是在服务器 Python 环境里执行」。如果通用助手的系统提示
里手写一份 Agent 列表，每新增一个智能体都要改提示词，既容易漏，也会让提示词与
配置漂移。

本模块在**每轮** harness 执行前，根据当前注册表与「本轮实际可用的工具」生成
一段能力清单，追加到系统提示后面。因此：

- 新增 ``agents.json`` 条目 → 下一轮模型就能看到，无需改代码 / 改提示词；
- ``tools`` / ``disallowed_tools`` 裁掉了某个工具 → 清单里不会出现对应能力；
- 专业智能体的 ``description`` 就是路由依据，写清楚「做什么 + 什么时候用」即可；
- **业务分析是条件分支**：通用问答/写作/代码等仍按原能力直接完成，不强制套流程。
"""

from __future__ import annotations

from collections.abc import Iterable

from qaapp.config import KIND_EXTERNAL, AgentEntry, AgentRegistry

#: 决策顺序：这是产品「自主编排」的核心策略，放在清单最前面。
_DECISION_RULES = """\
### 决策顺序（按此顺序判断，不要跳步）
1. **能直接回答就直接回答**：通用知识、写作、代码、推理、简单换算等，用你自身能力完成，不要为了“显得专业”而调用工具。
2. **普通业务取数用 `db_query`**：用户要一个数值、明细、趋势、对比时，先查业务库；不要凭记忆或让外部智能体代查。
3. **深度业务分析/报告才加载 `business-analysis` 技能**：先判断用户是否真的要“分析/研判/报告”；不是的话不要启动四阶段流程。
4. **需要业务系统里的专业判断/政策背景时，组合专业智能体**：按下方「专业智能体」的 description 匹配；一个任务可以调用 0–3 个，不要只选一个就结束。
5. **需要真实计算或处理文件时，用 run_script 在服务器执行 Python**：Excel/CSV 统计、批量转换、生成图表/报告/新文件等。
6. **需要最新外部事实时，用 web_search / web_fetch 查证**，并说明来源；不要编造。
7. **信息不足且会影响结果时，先用 `ask_user_question` 确认（若该工具不可用则直接用文字提问）**：当口径、时间范围、统计维度、目标对象、输出格式等会改变结果且无法从上下文推断时，提供 2–4 个具体选项（label 简短、description 说明差异、recommended 标推荐项），等用户选择后再执行；能从对话、附件、会话目录推断的不要重复问。
8. **绝不向用户索要 token、接口地址、内部实现细节**；这些属于部署配置，不是用户任务的一部分。
9. **多步任务边做边报告**：先完成能确定的部分；某一步失败时说明原因、给出替代方案，不要假装成功。
10. **不要向用户展示内部路由推理/工具名/agent id**；直接给结果或简短进度。
"""

def _composition_rules(external: list[AgentEntry]) -> str:
    """按 registry 的 role 生成组合规则，避免把 agent id 写死在提示词里。"""
    def _ids(role: str, fallback: str) -> str:
        found = [agent.id for agent in external if (agent.role or "") == role]
        return "、".join(f"`{item}`" for item in found) if found else fallback

    data = _ids("data", "取数类智能体")
    analysis = _ids("analysis", "分析类智能体")
    knowledge = _ids("knowledge", "知识类智能体")
    return f"""\
### 组合编排（复杂任务必须考虑，不要固定只调一个，也不要只选一个就结束）
- **取数/明细**：`db_query` 优先；需要自然语言复杂口径或第二意见时，再调 {data} 对照。
- **趋势/归因/研判**：`db_query` + `run_script` 出事实和统计；{analysis} 给领域假设/影响链/建议。
- **政策/文件/背景**：{knowledge} 解读；`db_query(news)` / `web_search` 补事件与出处。
- **深度分析/报告**：上述组合 + `consult_agents` 批量调用 + `write_report_docx`。
- 数字冲突以业务库为准；智能体输出只是素材，最终必须由你重新组织，不能整段粘贴。
- 单个智能体失败：重试一次，仍失败就换另一个或 `db_query`；不要编造。"""




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


def _agent_line(agent: AgentEntry) -> str:
    name = _clean(agent.name) or agent.id
    description = _clean(agent.description) or "（未填写 description，路由时请谨慎）"
    lines = [f"- `{agent.id}`（{name}）：{description}"]
    if getattr(agent, "best_for", None):
        lines.append(f"  擅长：{'；'.join(_clean(item) for item in agent.best_for[:4])}")
    if getattr(agent, "not_for", None):
        lines.append(f"  不适用：{'；'.join(_clean(item) for item in agent.not_for[:3])}")
    if getattr(agent, "examples", None):
        lines.append(f"  示例：{'；'.join(_clean(item) for item in agent.examples[:2])}")
    return "\n".join(lines)


def build_capability_catalog(
    registry: AgentRegistry,
    *,
    current_agent_id: str = "",
    available_tools: Iterable[str] | None = None,
    now: str = "",
    data_boundary: str = "",
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
    if external and (_has("external_agent") or _has("consult_agents")):
        lines = [
            "### 专业智能体（调用 `consult_agents` 批量编排，或 `external_agent` 单个调用）",
        ]
        for agent in external:
            lines.append(_agent_line(agent))
        lines.append(
            "选择规则：先判断用户意图属于哪个 description 的领域，再选 id；"
            "复杂任务可以同时调用多个（取数/分析/政策），不要只选一个。"
            "把用户原话/具体子任务作为 message 传入，不要把问题改写成另一个问题。"
        )
        sections.append("\n".join(lines))

    if _has("db_query"):
        sections.append(
            "### 业务库查询（db_query，优先用于事实）\n"
            "- 任何业务数字/明细/趋势/对比，先用 `db_query` 取数；它已处理指标别名、表路由、"
            "月末日期、周期/维度回退、无数据诊断和数据质量检查。\n"
            "- 常用操作：`query`（取数）、`explore`（EDA/问题候选）、`outline`（场景大纲）、"
            "`describe`（口径/最新日期）、`news`（政策/事件）、`chart`（图表）。\n"
            "- 取数结果里的 `data_quality`/`diagnostics` 必须看；无数据时不要编造，先说明最新可用日期。"
        )

    if _has("consult_agents"):
        sections.append(
            "### 多智能体批量调用（consult_agents）\n"
            "- 需要 2–4 个智能体协作时用一次 `consult_agents`：`calls` 写清 agent/message/purpose，"
            "`shared_context` 放已核验数据，`mode=parallel` 并行、`sequential` 顺序。\n"
            "- 调用结果会截断并落盘；完整原文在 `outputs/agents/`。用 `db_query` 核验数字后再写。\n"
            "- 单个失败不会阻断其他；失败项会标注，不要用记忆数据补齐。"
        )

    if _has("run_script"):
        sections.append(
            "### 本机执行能力（run_script）\n"
            "- `run_script`：在本会话目录执行 Python/Node 脚本（服务端共享环境，已装 "
            "pandas / openpyxl / python-docx）。脚本自动写入 `scripts/`，工作目录是会话目录根，"
            "因此可用 `uploads/<文件名>` 读用户上传的文件、把产物写到 `outputs/`。\n"
            "- 适用：数据统计与透视、贡献度、Excel/CSV 批量处理、格式转换、生成图表/报告/新文件、"
            "需要精确计算或可复现结果的任务。\n"
            "- 不适用：纯知识问答、写作、无需计算的解释；这些直接回答。\n"
            "- 执行类工具可能触发审批；被拒绝时向用户说明需要什么权限，不要绕道尝试其他执行方式。"
        )

    if _has("write_report_docx"):
        sections.append(
            "### Word 报告（write_report_docx）\n"
            "- 深度分析完成后，用 `write_report_docx` 生成 `.docx`：可传 `sections`（结构化）或 "
            "`markdown`/`markdown_path`（长文先落 Markdown 再转换）。默认标题/单位/日期与正文同页；"
            "长报告可传 `include_toc=true` 单独生成目录页，传 `include_page_numbers=false` 可关闭页码。\n"
            "- 报告必须包含：摘要、背景与口径、总体情况、分维度、问题诊断、成因、建议、数据质量/来源。\n"
            "- **版式固定为国网公文格式**：A4，上下 35mm、左右 28mm；标题宋体二号加粗；作者/日期楷体三号加粗；"
            "一级标题黑体三号；二级标题楷体三号加粗；三级标题宋体三号加粗；四级标题与正文仿宋三号；固定行距 29 磅。"
            "标题用 1–4 级，按“一、/（二）/3./（4）”编号；正文首行缩进两字符。\n"
            "- 所有 Word 输出统一走 `write_report_docx`；不要用 `run_script` 直接写 `.docx`，以免绕过公文版式。\n"
            "- 数字先用 `db_query` 核验；不要直接把智能体原文粘进报告。"
        )

    if _has("ask_user_question"):
        sections.append(
            "### 选项式澄清（ask_user_question）\n"
            "- 当口径、时间范围、统计维度、目标对象、输出格式或方案取舍会改变结果，且无法从对话/附件/会话目录推断时，"
            "先调用 `ask_user_question`，传入 2–4 个互斥的 `options`（`label` 简短、`description` 说明差异、"
            "`recommended` 标推荐项），等用户确认后再继续。\n"
            "- 能用合理默认值推进的不要问；已经问过的不要重复问。\n"
            "- 用户可以选择「其他」输入自定义答案；不要假设用户只会选给出的选项。\n"
            "- 用户回答后直接按该答案执行，不要再次确认同一个点。"
        )

    if _has("skill"):
        sections.append(
            "### 可按需加载的技能（用 skill 工具按名加载）\n"
            "- `database-query`：取数细节、表路由、数据坑；\n"
            "- `business-analysis`：业务分析场景、四阶段、提纲/EDA/深挖/报告模板；\n"
            "- `multi-agent-orchestration`：三智能体提示词模板、并行/顺序、综合检查表。"
        )

    if not sections:
        return ""

    parts = [
        "## 可调度能力（由 agents.json / 工具表自动生成，新增能力无需改本段）",
        "通用任务仍按你原有的能力直接完成；下面是按需使用的扩展能力，不是强制流程。",
        _DECISION_RULES,
    ]
    if external and (_has("external_agent") or _has("consult_agents")):
        parts.append(_composition_rules(external))
    parts.extend(sections)
    if now or data_boundary:
        lines = ["### 当前时间与数据边界"]
        if now:
            lines.append(f"- 当前日期：{now}（Asia/Shanghai）。")
        if data_boundary:
            lines.append(f"- 业务库最新可用：{data_boundary}。")
        lines.append("- 用户指定未来日期时，不得声称已有实际值；先查最新可用日期，再说明实际/预测口径。")
        parts.append("\n".join(lines))
    parts.append(
        "### 兜底\n"
        "- 如果用户需要的能力不在上面（缺少对应智能体 / 工具），明确说明「当前缺少哪项能力」，"
        "并给出可执行的替代方案；不要编造数据，也不要声称调用了不存在的智能体。"
    )
    return "\n\n".join(parts)


__all__ = ["build_capability_catalog"]
