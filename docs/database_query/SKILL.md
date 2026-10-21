---
name: database_query
description: "查询项目数据库的指标数据。触发条件：用户提到'查询'、'数据'、'指标名称'（如售电量、营业收入）、'同比'、'环比'、'趋势'等关键词。用于获取福建电力项目的各类指标数据，支持多维度、多时间段查询。"
metadata:
  category: data-access
  version: 1.0.0
  author: MindCube Team
  tags: [数据库, 查询, SQL, 指标, 福建电力, 售电量, 营业收入, 线损率, 电费回收率]
  dependencies: [mysql_tools]
  capabilities:
    - indicator_query      # 指标查询
    - time_range_filter    # 时间范围过滤
    - multi_indicator      # 多指标查询
    - org_filter           # 组织过滤
    - comparison_analysis  # 对比分析
  input_requirements:
    - indicators           # 指标列表(必需)
    - time_range           # 时间范围(可选)
    - org                  # 组织名称(可选)
    - analysis_type        # 分析类型(可选)
  output_format:
    - query_results        # 查询结果
    - analysis_results     # 分析结果
    - sql_queries          # SQL查询记录
    - charts               # 图表数据
  estimated_time: 30       # 预估执行时间(秒)
  priority: 1              # 优先级(1-10, 1最高)
---

# 数据库查询SKILL

## 何时使用此SKILL

当用户请求以下内容时使用：
- 查询指标数据（售电量、营业收入、线损率、电费回收率等）
- 分析数据趋势（同比、环比、月度变化）
- 对比不同地区/时间段的数据
- 获取排名信息
- 预测数据查询

**触发关键词**：查询、数据、指标、售电量、营业收入、线损率、同比、环比、趋势、对比、排名、预测

## 如何使用此SKILL

### 步骤1：从 Neo4j 图谱获取关联信息

先调用 `indicator_graph_query`：
- 获取主指标的关联指标与预测指标
- 获取关联指标查询元数据（如 `query_type=dimension/city_level/weather/external`）
- 当 Neo4j 不可用时，使用本地回退配置并保留上述元数据

### 步骤2：解析查询参数

从用户请求中提取：
- 指标名称（主指标）
- 时间范围（start/end）
- 组织名称（org）
- 周期（日/周/月/年）
- 分析类型（同比/环比/趋势）

### 步骤3：按 `one_table + 维度关键词` 做表路由（A1/A2/B1/B2）

由 `TableSelector.get_table_decision(...)` 统一决策：

- **Path A1**：`one_table=1` 且无维度关键词 → 按 `org_type` 走层级表（qs/ds/qx/gds）
- **Path A2**：`one_table=1` 且有维度关键词 → 使用 `index_table_name`，`JOIN qlyy_dimension`
- **Path B1**：`one_table=0` 且无维度关键词 → 按 `org_type` 走层级表（qs/ds/qx/gds）
- **Path B2**：`one_table=0` 且有维度关键词 → 使用 `index_table_name + '_ssx'`，`JOIN qlyy_dimension`

> 重点：`one_table=1` 场景带维度时走 A2（`index_table_name`），**不拼接 `_ssx`**。

### 步骤4：关联指标与预测指标处理

- 关联指标（`role=related`）
  - `query_type=dimension`：使用图谱下发的维度元数据（维度名/维度ID/表名）
  - `query_type=city_level`：使用地市 `org_id` 在地市表查询
  - `query_type=weather`：走天气表 `zhss_result_data_tqyb_mx`
  - `query_type=external`：跳过数据库查询
- 预测指标（`role=prediction`）
  - 仅支持省级/地市
  - 月度默认向未来扩展 3 个月，日度向未来扩展 7 天

### 步骤5：SQL 生成与执行

- 仅允许执行 `SELECT`（执行前做 SQL 安全校验）
- 普通主指标走基础层级表时追加 `dim_val IS NULL`
- 维度查询支持 `base_join` / `ssx_join` / `direct_dimval`
- 维度查询无数据时，自动回退到对应层级基础表（A1/B1）

## 关键规则

1. **月份转月末**：`2025-12 -> 2025-12-31`
2. **A1/A2/B1/B2 路由**：由 `TableSelector.get_table_decision` 统一决策
3. **one_table=1 带维度**：必须走 A2（`index_table_name + JOIN qlyy_dimension`），不走 `_ssx`
4. **维度容错回退**：维度查询无数据，回退到层级基础表
5. **普通主指标基础查询**：追加 `AND dim_val IS NULL`
6. **四率指标**：使用专用表与 `dim_val`（主网=990201，配网=990202）
7. **预测指标层级限制**：仅支持 qs/ds
8. **SQL 安全限制**：禁止 UPDATE/DELETE/INSERT 等非 SELECT 语句


## 常见场景

### 场景1：单指标查询
用户："查询福建2025年12月的售电量"
→ 参考 `examples/indicator-query.md`

### 场景2：同比环比分析
用户："分析厦门近3个月售电量的同比环比"
→ 参考 `examples/comparison.md`

### 场景3：多指标对比
用户："对比售电量和营业收入的趋势"
→ 参考 `examples/trend-analysis.md`

### 场景4：预测数据
用户："查询福建售电量预测"
→ 参考 `examples/prediction-query.md`

## 数据表映射

| org_type | 层级 | 数据表 |
|----------|------|--------|
| 3001 | 省级 | zhss_result_data_qs |
| 3002 | 地市 | zhss_result_data_ds |
| 3003 | 区县 | zhss_result_data_qx |
| 3005 | 供电所 | zhss_result_data_gds |

特殊表：
- 四率合一：zhss_result_data_slhyzt
