# database_query 落地实现说明（qaapp）

> 本文件说明 `docs/database_query/` 里的客户参考资料如何落到 qaapp 的 `db_query` 工具。
> 原始 `SKILL.md` / `schema/db_structure.md` / `examples/` **保留不动**，作为口径参考；
> 实际实现以本文件 + 代码为准。

## 1. 为什么不能直接运行原 `scripts/executor.py`

原执行器依赖：

- `skills.base_skill.SkillExecutor`
- `app.utils.query_parser` / `indicator_matcher` / `table_selector` / `org_mapper`
- `app.config.unified_indicator_config`

这些模块不在本仓库。qaapp 的实现是**原生重写**：

| 原参考 | qaapp 实现 |
| --- | --- |
| `DatabaseQuerySkill.execute` | `qaapp/db_query_tool.py` + `qaapp/data_query.py` |
| `IndicatorMatcher` | `qaapp/data_catalog.py`（`zhss_index_basic_info` + `zhss_index_name_sim` + `ai_index_config`） |
| `TableSelector` A1/A2/B1/B2 | `qaapp/data_query.py::_candidate_tables` |
| `OrgMapper` | `qaapp/data_catalog.py::resolve_orgs` |
| 维度匹配 | `qaapp/data_catalog.py::resolve_dimensions`（`ai_index_dim` 优先） |
| 执行器/SQL 安全 | `qaapp/data_client.py::MySqlClient` + `SqlGuard` |
| 数据质量 | `qaapp/data_quality.py` |

## 2. 已实测的库结构与坑

- 共 140 张表；`zhss_index_basic_info` 621 条（619 条 `status=1`）。
- `index_table_name` 93 个不同值，**73 个不存在**：
  - `zhss_result_data_khydl` 不存在 → 实际是 `zhss_result_data_khydl_ssx` / `_gds`；
  - `zhss_result_data_dfhsl` 不存在 → 电费回收率数据在 `qs/ds/qx/gds`；
  - `zhss_result_data_zhxslnew` 不存在 → 综合线损率数据在 `qs/ds/qx`。
- `ai_data_struct`（294 条）记录实际表/周期/维度/机构，**表路由第一优先级**。
- `ai_index_dim`（1,032 条 / 126 个指标）比 `qlyy_dimension`（7,269 条）更适合按指标匹配维度。
- `zhss_index_system`（776 条）提供部门/场景/主指标，用于大纲；`ai_index_config`（427 条）提供定义/计算方法。
- 数据查询必须用 `index_id`：`IDX-1043-00010006` 同一 ID 下同时有“用电最高负荷/最高用电负荷”两种名称。
- 月度 `stat_date` 为月末；年度数据可能是“截至某月的年累计”（发电量 2026-06-30）。
- 电费回收率、综合线损率 2026 年各月 `val` 恒为 `256.0803`，必须标记 `constant_series`。
- 发电量：月度/年度最新都到 2026-06；部分月份只有维度行、没有总量行，需 `no_total_dim` 回退。
- `ai_power_news` 35,833 条，含重复/未来日期/日历噪声；`fujian_power_events` 仅 5 条；事件检索必须去重、限时。
- Redis db0 是预测/特征缓存，db1 含平台配置/密钥；只读 db0 + 白名单前缀。

## 3. 表路由（实现顺序）

```text
1) 温度/天气 → zhss_result_data_tqyb_mx
2) 四率 → zhss_result_data_slhyzt（dim_val=1 主网 / 2 配网）
3) ai_data_struct：
   - 过滤 period / org
   - 有维度优先 _ssx；无维度优先与 org 级别匹配的 qs/ds/qx/gds
4) 文档规则回退：
   - one_table=1：index_table_name（存在）→ 层级表
   - one_table=0：无维度 → 层级表；有维度 → index_table_name_ssx → 层级表
5) 表存在性 + 轻量数据探测；全部无数据返回诊断
6) 无总量行：dim_val IS NULL → dim_val='' → 任意维度明细（标记 no_total_dim）
```

机构级别：3001→qs、3002→ds、3003→qx、3005→gds。

## 4. 工具操作

`db_query` 支持：

- `resolve`：指标/机构/维度候选；
- `describe`：口径、单位、周期、最新日期、维度；
- `outline`：按 `scenario` 生成数据可得性驱动的大纲；
- `query`：取数（`group_by=date/org/dimension/none`，`aggregate=auto/none/sum/avg/latest`）；
- `explore`：趋势 + 九地市 + 维度拆分 + 贡献度 + 问题候选 + 数据质量；
- `news`：政策/企业事件（去重、限时）；
- `chart`：折线/柱状/饼图；
- `forecast`：读取 Redis db0 的预测/特征缓存（辅助数据，非权威值）；
- `sql`：受控只读 SQL（默认关闭）。

返回：摘要 + 少量预览 + `data_quality` + `diagnostics` + SQL 审计 + `outputs/data/*.csv`。

## 5. 数据质量

| 代码 | 处理 |
| --- | --- |
| `no_data` | high；给最新可用日期/周期/维度/建议查询 |
| `constant_series` | high；禁止据此写趋势 |
| `stale_data` | warn；说明最新日期 |
| `period_fallback` / `table_fallback` / `dim_fallback` / `org_fallback` | warn；说明实际口径 |
| `missing_ratio` / `outlier` / `unit_mismatch` / `duplicate_rows` | warn；报告中披露 |
| `derived_metric` | info；写明公式 |

## 6. 配置与安全

- 唯一数据源文件：`datasources.json`（`${DB_HOST}` 等占位符 + 环境变量/就近 `.env`）；
- 唯一加载器：`qaapp/datasources.py`；其他模块不读 `DB_*`/`REDIS_*`、不建连接；
- MySQL 只读、连接池、执行超时、行数上限；
- 原始 SQL 默认关闭，开启后只允许单条 SELECT/SHOW/DESCRIBE/EXPLAIN，拒绝写操作/注释/敏感表/系统库；
- Redis 只读 db0、白名单前缀，禁止 db1/写/KEYS；
- 凭据不进仓库、日志、提示词、前端；`abcqa datasource show` 密码脱敏。

## 7. 与原始文档的差异

| 文档说法 | 实际实现 |
| --- | --- |
| A1 直接查 `index_table_name` | 先查 `ai_data_struct`；表不存在时回退层级表 |
| 用 `index_name` 过滤数据 | 用 `index_id`；`index_name` 可能随记录变化 |
| 月度月末 | 保留；年度不强制 12-31 |
| 维度 JOIN `qlyy_dimension` | 保留；优先用 `ai_index_dim` 收敛维度 |
| 只追加 `dim_val IS NULL` | 再试 `dim_val=''`；无总量行返回维度明细并标记 |
| 预测指标 | 名称含“预测/预估”或 `PRE-`；仅省/地市 |
