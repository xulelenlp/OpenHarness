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

项目内新增三个技能，统一放在仓库根目录 **`skills/`**（随 git 维护，不再放 `.openharness/skills/`）：

- `skills/database-query/`：取数细节、表路由、数据坑；
- `skills/business-analysis/`：10 类业务场景、四阶段、提纲/EDA/深挖/报告模板；
- `skills/multi-agent-orchestration/`：三智能体提示词模板、并行/顺序、综合检查表。

`config/backend/.env` 的 `OPENHARNESS_PROJECT_SKILL_DIRS` 已把 `skills` 放到最后（同层同名时优先级最高）；新增/修改技能只需改这个目录，下一轮自动生效，不需要改 Python 代码。

通用助手仍是默认 Agent，原有开放任务能力不变；只有任务明确是业务分析/报告时才加载 `business-analysis`，不强制四阶段。

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
