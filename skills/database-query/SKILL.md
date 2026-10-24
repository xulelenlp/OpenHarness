---
name: database-query
description: 查询客户业务库（MySQL）的指标、维度、机构、天气、政策事件数据。需要具体数值、趋势、对比、排名、明细，或需要核验智能体给出的数字时使用。包含表路由、月末日期、维度匹配、无数据诊断和数据质量规则。
---

# 业务库取数（database-query）

## 何时使用

- 用户要具体指标数值、明细、趋势、同比环比、九地市/维度对比、排名。
- 需要核验外部智能体给出的数字。
- 要做业务分析前，先确认指标口径、最新可用日期、可用维度。

## 唯一入口：`db_query`

不要自己写 SQL（除非已开启 `mysql.allow_raw_sql` 且确实需要）。`db_query` 已处理：

- 指标别名/近义词/复合地市（“福州售电量”）/自然语言维度（“工业售电量”）；
- A1/A2/B1/B2/T/四率表路由；
- 月末日期、周期回退、机构级别回退、维度回退；
- 无数据诊断、数据质量检查（恒定序列/陈旧/异常/空值/单位）；
- 结果预览 + 完整 CSV。

### 常用操作

| operation | 用途 | 示例参数 |
| --- | --- | --- |
| `resolve` | 看指标/机构/维度候选 | `indicators=["售电量"]` |
| `describe` | 看口径、单位、周期、最新日期、维度 | `indicators=["售电量"]` |
| `query` | 取数 | `indicators=["售电量"], org="厦门", period="月", start="2026-09", end="2026-09"` |
| `explore` | EDA：趋势/九地市/维度/问题候选 | `indicators=["售电量"], org="福建", start="2025-10", end="2026-09"` |
| `outline` | 按场景生成大纲 | `theme="2026年9月福建售电量分析", scenario="sales"` |
| `news` | 政策/企业事件 | `keywords=["售电","电价"], start="2026-01-01"` |
| `chart` | 生成图表 | 同 query + `chart_type="line"` |
| `forecast` | 读取 Redis 预测/特征缓存（辅助） | `start="2026-09-30"` |
| `sql` | 受控只读 SQL（默认关闭） | 仅高级场景 |

## 取数之后（分析场景，必做）

单值查询到此结束。若用户要的是“分析/研判/专题/报告”，取数只是输入，下一步必须补外部信息（完整做法见 `business-analysis` 技能 Phase 3）：

- `db_query(operation="news")`：查库内政策/企业事件，对齐业务口径。
- `web_search` / `web_fetch`：查公开政策原文、行业与宏观数据、企业动态、天气与舆情；每次查询加地域、时间、信息类型词，关键条目打开原文核对。
- 每条外部信息记“机构 + 发布日期 + 链接”，落 `outputs/external/sources.csv`，并与 `findings.md` 的问题点做交叉验证。
- 数字仍以 `db_query` 为准；外部信息只用于解释、对照与前瞻，不能替代取数。
- 检索失败或无外网：说明原因，用 `news` + `knowledge-qa` 兜底，并在报告局限性中披露。

## 表路由规则（按优先级）

1. **特殊表**
   - 温度/气温/天气 → `zhss_result_data_tqyb_mx`；最高温 `dim_value_b`、最低温 `dim_value_c`、平均温 `(b+c)/2`。
   - 四率合一（投资完成率/物资领用率/入账完成率/支出完成率）→ `zhss_result_data_slhyzt`；`dim_val='1'` 主网、`'2'` 配网。

2. **`ai_data_struct` 优先**
   - 它记录“指标 → 实际表/周期/维度/机构”；业务库里 93 个 `index_table_name` 有 73 个不存在，不能盲信文档里的表名。
   - 有维度优先 `_ssx` 表；无维度优先与机构级别匹配的 `qs/ds/qx/gds`。

3. **文档规则回退**
   - `one_table=1`：`index_table_name`（存在时）→ 层级表。
   - `one_table=0`：无维度 → 层级表（3001→qs、3002→ds、3003→qx、3005→gds）；带维度 → `index_table_name + '_ssx'` → 层级表。

4. **无总量行**：先 `dim_val IS NULL`，再 `dim_val=''`；仍无则返回维度明细，标记 `no_total_dim`，不要猜某个维度是总量。

## 关键口径

- 月度 `stat_date` 是**月末**（2026-09 → 2026-09-30）。
- 年度数据不一定是 12-31：可能是“截至某月的年累计”（如发电量 2026-06-30），按实际行查。
- 数据查询用 `index_id`，**不要用 `index_name` 过滤**：同一 `index_id` 可能同时有“用电最高负荷/最高用电负荷”等名称。
- 机构：福建=`210000000000127`；九地市 `parent_code=210000000000127 AND type=3002`；区县 type=3003；供电所 type=3005。
- 维度 `dim_val` 是字符串，`qlyy_dimension.wd_id` 是 bigint；匹配用 `ai_index_dim`（指标维度）优先，`qlyy_dimension` 兜底。
- 维度可能多匹配（“工业” vs “第二产业”）：逐个查，返回有数据的并标注。
- 指标同名/别名冲突：先看 `candidates`/`needs_confirmation`，不要擅自选。

## 已知数据坑（务必看）

- 电费回收率、综合线损率在 2026 年各月 `val` 恒为 `256.0803`：`constant_series` 高优先级，禁止据此写趋势。
- `index_table_name` 大量不存在：用 `ai_data_struct` + 层级表回退。
- 不同指标最新日期不同：问答前先看 `diagnostics.latest_available`，不要假设“本月一定有数”。
- `ai_power_news` 有重复/未来日期/日历噪声：`news` 已去重限时；仍要核对来源与时间。
- 预测指标：名称含“预测/预估”或 `PRE-` 前缀；仅支持省/地市；报告中标注“预测”。

## 数据质量处理

- `high`：`constant_series`、`no_data` → 必须在回答/报告披露，不得写趋势。
- `warn`：`stale_data/period_fallback/table_fallback/dim_fallback/org_fallback/outlier/missing_ratio/unit_mismatch/duplicate_rows` → 说明口径或限制。
- 任何数字都带：指标 + 机构 + 周期 + 日期 + 单位 + 来源表/SQL。

## 示例

```text
用户：厦门2026年9月售电量
→ db_query(operation="query", indicators=["售电量"], org="厦门", period="月", start="2026-09", end="2026-09")
→ 9.8389 亿千瓦时，同比 -2.48%，环比 -6.49%，来源 zhss_result_data_ds

用户：福建2026年9月第一产业售电量
→ db_query(operation="query", indicators=["售电量"], dimensions=["第一产业"], org="福建", period="月", start="2026-09", end="2026-09")
→ 1.1044 亿千瓦时，同比 -13.85%

用户：分析福建2026年9月售电量
→ 先 db_query(operation="outline", scenario="sales", theme=...)，确认大纲
→ db_query(operation="explore", indicators=["售电量"], org="福建", start="2025-10", end="2026-09")
→ 取数后 web_search 查“福建 2026 售电量 增速 原因 / 电价政策 / 高温台风”，web_fetch 核对原文
→ 再 consult_agents / run_script / write_report_docx
```
