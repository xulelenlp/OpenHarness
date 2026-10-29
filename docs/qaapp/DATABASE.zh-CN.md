# qaapp 业务库直连与多智能体编排

## 12. 业务库直连与多智能体编排（新增）

### 12.1 数据源：只维护 `datasources.json`

MySQL/Redis 的连接信息**不再写进 `webapp.json`/代码**，统一放在 `datasources.json`：

```jsonc
{
  "mysql": {
    "enabled": true,
    "host": "${DB_HOST}",
    "port": "${DB_PORT}",
    "user": "${DB_USER}",
    "password": "${DB_PASSWORD}",
    "database": "${DB_NAME}",
    "read_only": true,
    "allow_raw_sql": false
  },
  "redis": {
    "enabled": true,
    "host": "${REDIS_HOST}",
    "port": "${REDIS_PORT}",
    "password": "${REDIS_PASSWORD}",
    "db": 0,
    "key_prefixes": ["fj_", "daily_"]
  }
}
```

- 解析顺序：`--config-dir` / `QAAPP_CONFIG_DIR` → `<cwd>/config/datasources.json` → `<workspace>/datasources.json`；
- `${VAR}` 由 `qaapp/data/datasources.py` 统一解析：**进程环境变量 > 就近 `.env` > JSON 字面值**；
- 推荐把真实凭据放本机 `~/.ohmo/datasources.json`（0600），仓库里只放占位符版本；
- `redis.db` 强制为 0（db1 含平台密钥）；MySQL 必须 `read_only=true`；
- 仓库里的 `config/datasources.json` 是**占位符模板**：未设置 `DB_*` / `REDIS_*` 时 `abcqa config` 会提示缺少 host，这是预期；把真实值放本机 workspace 或设置环境变量即可；
- 查看/检查：

```bash
abcqa datasource show    # 密码脱敏，打印实际配置文件路径
abcqa datasource check   # 只读检查 MySQL/Redis 连通性、关键表/键
abcqa config             # 配置文件（webapp/agents/dangerous/datasources）路径与校验
```

### 12.2 新工具

| 工具 | 作用 | 权限 |
| --- | --- | --- |
| `db_query` | 指标/维度/机构查询、EDA、大纲、新闻、图表、Redis 预测缓存、受控 SQL | 只读 |
| `consult_agents` | 1–4 个外部智能体并行/顺序调用，重试、超时、截断、证据落盘 | 只读 |
| `write_report_docx` | Markdown/结构化章节 → Word（国网公文格式；标题/单位/日期、可选目录、表格/图片/页码/来源） | 写（默认确认） |

- `db_query` 直连业务库，已处理 A1/A2/B1/B2/T/四率路由、月末日期、周期/维度回退、无数据诊断、数据质量；
- `consult_agents` 不替代 `external_agent`；复杂任务用它一次批量调用三个智能体，`shared_context` 共享已核验数据；
- 数字以 `db_query` 为准；智能体输出只是素材，最终回答必须重新组织；
- `write_report_docx` 只写 `<会话目录>/outputs/reports/`；默认需确认，若要免确认：
  `{"tool":"write_report_docx","action":"allow","reason":"仅写入会话目录 outputs/reports"}`。

### 12.3 业务分析技能

项目内新增四个技能，统一放在仓库根目录 **`skills/`**（随 git 维护，不再放 `.openharness/skills/`）：

- `skills/database-query/`：取数细节、表路由、数据坑；指标口径问答先用 `describe`，多候选让用户选；
- `skills/business-analysis/`：面向国网领导的正式报告工作法（六步：主题口径 → 取数校验 → 公网信息补充（国内/国外政策事件）→ 因素筛选 → 结构设计 → 图文撰写），含时间重合判定、自适应结构框架与写作红线；
- `skills/document-analysis/`：上传 Excel/Word/TXT 的深入分析与总结（盘点文件与结构 → 抽取事实与数据 → 交叉核验 → 对比/趋势/异常/原因分析 → 结构化成文）；
- `skills/multi-agent-orchestration/`：三智能体提示词模板、并行/顺序、综合检查表。

`config/backend/.env` 的 `OPENHARNESS_PROJECT_SKILL_DIRS` 已把 `skills` 放到最后（同层同名时优先级最高）；新增/修改技能只需改这个目录，下一轮自动生效，不需要改 Python 代码。

通用助手仍是默认 Agent，原有开放任务能力不变；只有任务明确是业务分析/报告时才加载 `business-analysis`，不强制套流程。报告口径为“面向领导”：数据错误剔除、来源存疑不写、只保留 1–3 个真正有影响的因素，不写情景假设、分析过程和数据质量章节；取数后补充公网信息（国内/国外政策事件），先判断与报告期是重合、前置还是后置，再看能否传导到指标；报告结构按主题选择趋势归因/结构诊断/目标进度/风险处置/政策专题等框架，选 4–7 个有内容的章节并写主题化标题，不套固定八章模板。

分析报告、上传文档分析总结、指标口径查询三类任务统一遵循「先对齐再执行」：关键参数（主题/期间/口径/机构层级/交付物）缺失或多解时用 `ask_user_question` 给 2–4 个选项，两项以上缺失用一张「任务简报」卡；能从对话/附件/会话目录/`db_query(describe)` 推断的直接说明默认值，不重复问。`describe` 会返回候选指标、`needs_confirmation` 与备注，多候选时必须让用户确认。

### 12.4 依赖

`pyproject.toml` 新增 `pymysql` / `redis` / `matplotlib`。升级后执行：

```bash
pip install -e .          # 或 pip install pymysql redis matplotlib
abcqa datasource check
```

### 12.5 真实库校验（默认跳过）

```bash
QAAPP_TEST_DB=1 DB_HOST=... DB_PORT=... DB_USER=... DB_PASSWORD=... DB_NAME=... pytest tests/test_qaapp/test_db_smoke.py -q
```

覆盖：厦门售电量、福建第一产业、电费回收率无数据/恒定序列、发电量最新日期、售电场景大纲。

---


## 相关参考

- 实测库结构与文档差异：[`database/IMPLEMENTATION.zh-CN.md`](database/IMPLEMENTATION.zh-CN.md)
- 数据库表结构：[`database/SCHEMA.zh-CN.md`](database/SCHEMA.zh-CN.md)
- 查询示例：[`database/examples/`](database/examples/)
- 运行时技能：[`skills/database-query/SKILL.md`](../../skills/database-query/SKILL.md)
