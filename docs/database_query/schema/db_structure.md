# 数据库查询决策指南

## 1. 查询决策流程

根据指标名确定查询表和查询方式的完整流程：

```
指标名 → 判断是否为温度指标（关键词：温度/气温/最高温/最低温/天气）
			├─ 是温度 ──→ 直接查询 zhss_result_data_tqyb_mx ............... [Path T]
			│
			└─ 非温度 ──→ 查 zhss_index_basic_info
	      → 查 zhss_index_basic_info 
	         → 获取 one_table, index_table_name
	              │
	              ├─ one_table = 1 ──→ 判断用户查询是否包含维度关键词（维度表：qlyy_dimension.wd_name）
		              │                     │
		              │                     ├─ （不带维度）机构维度匹配 →直接查询index_table_name .. [Path A1]
		              │                     │   
		              │                     │
		              │                     └─ 有维度匹配 → index_table_name [Path A2]
		              │                          JOIN qlyy_dimension ON wd_id = dim_val
		              │                          │
		              │                          ├─ 匹配多个维度 → 逐个查询，取有数据的维度结果
		              │                          │
		              │                          └─ 维度查询无结果 → 回退到 Path A1
	              │
	              └─ one_table = 0 ──→ 判断用户查询是否包含维度关键词（维度表：qlyy_dimension.wd_name）
	                                       │
	                                       ├─ （不带维度）机构维度匹配 → 按 org_type 选层级表 .. [Path B1]
	                                       │    3001→qs, 3002→ds, 3003→qx, 3005→gds
	                                       │
	                                       └─ 有维度匹配 → index_table_name + "_ssx" [Path B2]
	                                            JOIN qlyy_dimension ON wd_id = dim_val
	                                            │
	                                            ├─ 匹配多个维度 → 逐个查询，取有数据的维度结果
	                                            │
	                                            └─ 维度查询无结果 → 回退到 Path B1（按 org_type 选层级表）
```

### Step 1: 查询指标元数据

根据指标名称从 `zhss_index_basic_info` 获取指标信息：

```sql
SELECT index_id, index_name, unit, one_table, index_table_name, predict_flag
FROM zhss_index_basic_info
WHERE index_name = '{指标名}'
  AND (scene IS NULL OR scene = '')
```

关键字段说明：
- `one_table`: 是否固定查询表。**1=固定基础表（仍需判断是否带维度）；0=无固定单表，需进一步判断**
- `index_table_name`: one_table=1 时为维度查询基表；one_table=0 时为维度查询的基础表名


### Step 2: 判断 one_table 字段

#### Path A: one_table = 1（固定基础表 + 维度分流）

当 `one_table=1` 时，先判断用户查询是否包含维度关键词：

- **Path A1（不带维度）**：直接查询index_table_name
- **Path A2（带维度）**：使用 `index_table_name` 与 `qlyy_dimension` JOIN 按维度查询

Path A1（不带维度）示例：

```sql
SELECT index_id, val, val_tb, val_hb, unit, stat_date
FROM {index_table_name}
WHERE index_id = '{index_id}'
  AND org_id = '{org_id}'
  AND period = '{period}'
  AND stat_date = '{stat_date}'
  AND dim_val IS NULL
```

Path A2（带维度）示例：

```sql
SELECT a.index_id, a.stat_date, a.val, a.val_tb, a.val_hb, a.unit, b.wd_name
FROM {index_table_name} a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE a.index_id = '{index_id}'
  AND a.org_id = '{org_id}'
  AND a.period = '{period}'
  AND a.stat_date = '{stat_date}'
  AND a.dim_val IS NOT NULL
  AND b.wd_name IN ('{维度名}')
```

> **容错**：Path A2 维度查询无结果时，回退到 Path A1。

#### Path B: one_table = 0（需进一步判断）

`index_table_name` 不是最终查询表，需要进入 Step 3 判断是否涉及维度。

### Step 3: 判断是否带维度查询（one_table=0）

**维度检测方法**：查询 `qlyy_dimension` 表获取所有维度名称，与用户查询文本进行关键词匹配。

```sql
SELECT wd_name, wd_id FROM qlyy_dimension
```

将查询结果中的 `wd_name` 与用户问题进行关键词匹配：
- "福建省**第一产业**售电量" → 包含"第一产业"，匹配到维度 → **带维度查询**
- "福建省售电量" → 无维度关键词匹配 → **不带维度查询**

#### Path B1: 机构维度（不带维度）→ 按 org_type 选层级表

**适用场景**：`one_table=0` 且用户查询中无维度关键词匹配。

**核心逻辑**：提取机构名称 → 查 `zhss_org_new` 获取 `type` 和 `org_id` → 按 type 选表 → 用 `index_id + org_id + period` 定位数据。

**Step 1：从用户查询提取机构名称**

系统按以下优先级识别机构：
1. 配置表直接匹配（省/市名称映射）
2. 数据库精确匹配（`org_short_name`）
3. 数据库模糊匹配（`province`/`city`/`county` 字段）
4. 名称后缀推断（以"县"/"区" → 3003，以"市" → 3002，以"省" → 3001）

示例：
- "福建省售电量" → 机构 = 福建
- "厦门市售电量" → 机构 = 厦门

**Step 2：查 zhss_org_new 获取 org_id 和 type**

```sql
SELECT org_code, type FROM zhss_org_new WHERE org_short_name = '{机构名}'
```

**Step 3：按 type 选表**

| type | 层级 | 查询表 | 示例机构 |
|------|------|--------|----------|
| 3001 | 全省 | zhss_result_data_qs | 福建 |
| 3002 | 地市 | zhss_result_data_ds | 厦门、泉州、福州等 |
| 3003 | 区县 | zhss_result_data_qx | 各区县 |
| 3005 | 供电所 | zhss_result_data_gds | 各供电所 |

**Step 4：执行查询**

所有层级使用统一模板，仅 `table_name` 和 `org_id` 不同：

```sql
SELECT index_id, val, val_tb, val_hb, unit, stat_date
FROM {table_name}
WHERE index_id = '{index_id}'
  AND org_id = '{org_id}'
  AND period = '{period}'
  AND stat_date = '{stat_date}'
  and dim_val is null 
```

> **说明**：`index_id` 已唯一标识指标，不需要额外用 `index_name` 过滤。

---

**特殊情况：复合指标（指标名含地市名称）**

当指标名称本身包含地市名（如"福州售电量"、"厦门售电量"）时，需先拆分再走标准流程：

1. 拆分：城市名 + 基础指标名（"福州售电量" → 福州 + 售电量）
2. 用基础指标名在 `zhss_index_basic_info` 获取 `index_id`
3. 用城市名走 Step 2-4 标准流程（福州 → type=3002 → zhss_result_data_ds）

```sql
-- 示例：查询福州售电量（复合指标拆分后）
SELECT index_id, val, val_tb, val_hb, unit, stat_date
FROM zhss_result_data_ds
WHERE index_id = 'IDX-1006-00010001'
  AND org_id = '210000000000143'
  AND period = '月'
  AND stat_date = '{stat_date}'
    and dim_val is null 
```


#### Path B2: 带维度 → index_table_name + "_ssx" 后缀

查询表名 = `index_table_name` + `_ssx`，并 JOIN `qlyy_dimension` 过滤维度：

```sql
SELECT a.stat_date, a.val, a.val_tb, a.val_hb, a.index_name, a.unit, b.wd_name
FROM {index_table_name}_ssx a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE a.index_id = '{index_id}'
  AND a.index_name = '{指标名}'
  AND a.org_id = '{org_id}'
  AND a.period = '{period}'
  AND a.stat_date = '{stat_date}'
  AND b.wd_name IN ('{维度名}')
```

**容错处理（重要）**：

1. **匹配到多个维度**：逐个维度执行查询，取有返回数据的维度结果。例如用户问"福建省工业售电量"，可能同时匹配到"工业"和"大工业"两个 wd_name，应分别查询，返回有数据的那个。

2. **维度查询无结果**（匹配的维度均无数据，或未匹配到任何维度）：**回退到 Path B1**，按 org_type 选择对应的层级表进行无维度查询，确保用户至少能获得该指标的总量数据。

```
维度查询容错流程：
匹配到 N 个维度关键词
  → 逐个查询 _ssx 表 + JOIN qlyy_dimension
     → 某个维度有数据 → 返回该维度结果
     → 所有维度均无数据 → 回退 Path B1，查层级表返回总量数据
```

---

## 2. 完整查询范例

### 范例1: Path A1 — one_table=1，不带维度

**用户问**：福建省2025年12月发电量

**决策过程**：
1. 查 zhss_index_basic_info → index_name=发电量, index_id=IDX-1043-00010002
2. one_table=1, index_table_name=zhss_result_data_qlzb → 先判断维度关键词
3. 未匹配维度关键词 → **Path A1（按 org_type 选层级表）**

**最终SQL**：
```sql
SELECT index_id, val, val_tb, val_hb, unit, stat_date
FROM {table_name}
WHERE index_id = 'IDX-1043-00010002'
  AND org_id = '210000000000127'
  AND stat_date = '2025-12-31'
  AND period = '月'
  AND dim_val IS NULL
```

### 范例1-补充: Path A2 — one_table=1，带维度

**用户问**：福建省2025年10月水力发电

**决策过程**：
1. 查 zhss_index_basic_info → index_name=发电量, index_id=IDX-1043-00010002
2. one_table=1, index_table_name=zhss_result_data_qlzb → 先判断维度关键词
3. 匹配到“水力发电”维度关键词 → **Path A2（基表 JOIN 维度表）**

**最终SQL**：
```sql
SELECT a.index_id, a.stat_date, a.val, a.val_tb, a.val_hb, a.unit, b.wd_name
FROM zhss_result_data_qlzb a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE a.index_id = 'IDX-1043-00010002'
  AND a.org_id = '210000000000127'
  AND a.period = '月'
  AND a.stat_date = '2025-10-31'
  AND a.dim_val IS NOT NULL
  AND b.wd_name IN ('水力发电')
```

### 范例2: Path B1 — one_table=0，不带维度

**用户问**：厦门市2025年11月售电量

**决策过程**：
1. 查 zhss_index_basic_info → index_name=售电量, index_id=IDX-1006-00010001
2. one_table=0 → 进入 Step 3
3. "厦门市2025年11月售电量" 无维度关键词匹配 → **Path B1**
4. 厦门 → zhss_org_new → type=3002 → 表名 zhss_result_data_ds

**最终SQL**：
```sql
SELECT index_id, index_name, val, val_tb, val_hb, unit
FROM zhss_result_data_ds
WHERE index_id = 'IDX-1006-00010001'
  AND index_name = '售电量'
  AND stat_date = '2025-11-30'
  AND period = '月'
  AND org_id = '210000000000128'
    and dim_val is null 
```

### 范例3: Path B2 — one_table=0，带维度

**用户问**：福建省2026年2月第一产业售电量

**决策过程**：
1. 查 zhss_index_basic_info → index_name=售电量, index_id=IDX-1006-00010001
2. one_table=0, index_table_name=zhss_result_data_khydl → 进入 Step 3
3. "第一产业" 匹配 qlyy_dimension 的 wd_name → **Path B2**
4. 查询表 = zhss_result_data_khydl + _ssx = **zhss_result_data_khydl_ssx**
5. 执行维度查询，如果有数据则返回；如果无数据则回退 Path B1 查 zhss_result_data_qs

**最终SQL**：
```sql
-- 第一步：带维度查询
SELECT a.stat_date, a.val, a.val_tb, a.val_hb, a.index_name, a.unit, b.wd_name
FROM zhss_result_data_khydl_ssx a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE a.index_id = 'IDX-1006-00010001'
  AND a.index_name = '售电量'
  AND a.period = '月'
  AND a.stat_date = '2026-02-28'
  AND a.org_id = '210000000000127'
  AND b.wd_name IN ('第一产业')

-- 第二步（容错）：如果上述查询无结果，回退到无维度查询
SELECT index_id, index_name, val, val_tb, val_hb, unit
FROM zhss_result_data_qs
WHERE index_id = 'IDX-1006-00010001'
  AND index_name = '售电量'
  AND stat_date = '2026-02-28'
  AND period = '月'
  AND org_id = '210000000000127'
    and dim_val is null 
```

### 范例4: Path B2 容错 — 多维度匹配

**用户问**：福建省2026年2月工业售电量

**决策过程**：
1. 查 zhss_index_basic_info → index_name=售电量, index_id=IDX-1006-00010001
2. one_table=0, index_table_name=zhss_result_data_khydl → 进入 Step 3
3. "工业" 可能匹配到多个 wd_name（如"工业"、"大工业"） → **Path B2，逐个尝试**
4. 对每个匹配的维度分别查询 zhss_result_data_khydl_ssx，取有数据的结果
5. 如果所有维度均无数据，回退 Path B1 查 zhss_result_data_qs 返回总量


### 范例5: Path T — 温度指标

**用户问**：福建省2026年2月平均温度

**决策过程**：
1. 提问为温度指标，查zhss_result_data_tqyb_mx


**最终SQL**：
```sql
-- 第一步：带维度查询
select name,(dim_value_b+dim_value_c)/2
from  zhss_result_data_tqyb_mx
where stat_date='2024-01-31'
and period='月'
and org_id='210000000000127'
```

---

## 3. 参考表结构

### 3.1 指标字典表（zhss_index_basic_info）

存储所有指标的元数据，是查询的入口表。查询普通指标时默认过滤场景指标：`scene IS NULL OR scene = ''`。

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| id | bigint | 主键ID | 315 |
| index_id | varchar(64) | 指标ID（唯一标识） | IDX-1006-00010001 |
| index_name | varchar(64) | 指标名称 | 售电量 |
| unit | varchar(255) | 单位 | 亿千瓦时 |
| one_table | tinyint(1) | 是否固定查询表 | 1=固定基础表（需先判断维度关键词）, 0=需进一步判断 |
| index_table_name | varchar(50) | 表名（含义取决于one_table） | zhss_result_data_khydl |
| predict_flag | int(1) | 是否预测指标 | 0=否, 1=是 |

**数据示例**：
```
index_id=IDX-1006-00010001, index_name=售电量, unit=亿千瓦时, one_table=0, index_table_name=zhss_result_data_khydl, predict_flag=1
index_id=IDX-1043-00010002, index_name=发电量, unit=亿千瓦时, one_table=1, index_table_name=zhss_result_data_qlzb, predict_flag=0
```

### 3.2 维度表（qlyy_dimension）

存储所有维度信息，用于维度关键词匹配和维度查询 JOIN。

| 字段 | 类型 | 说明 |
|------|------|------|
| wd_name | varchar | 维度名称（用于关键词匹配） |
| wd_id | varchar | 维度ID（用于 JOIN dim_val） |

**查询所有维度**：
```sql
SELECT wd_name, wd_id FROM qlyy_dimension
```

**使用方式**：
1. 获取所有 `wd_name` 值
2. 与用户查询文本进行关键词匹配
3. 匹配到的维度用于 WHERE 条件中的 `wd_name IN (...)` 过滤

### 3.3 组织结构表（zhss_org_new）

存储组织机构的层级关系。

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| org_code | varchar(64) | 机构编码（主键） | 210000000000127 |
| org_name | varchar(64) | 机构名称 | 福建 |
| org_short_name | varchar(64) | 机构简称 | 福建 |
| type | int(11) | 机构类型 | 3001=省, 3002=市, 3003=县, 3004=直属, 3005=所 |
| province | varchar(255) | 省份 | 福建 |
| city | varchar(255) | 城市 | 厦门 |
| county | varchar(255) | 区县 | - |
| parent_code | varchar(64) | 父机构编码 | - |

**数据示例**：
```
org_code=210000000000127, org_name=福建, type=3001
org_code=210000000000128, org_name=厦门, type=3002, parent_code=210000000000127
org_code=210000000000142, org_name=漳州, type=3002, parent_code=210000000000127
```

**九地市清单**：
- 210000000000128 - 厦门
- 210000000000142 - 漳州
- 210000000000143 - 福州
- 210000000000144 - 南平
- 210000000000145 - 泉州
- 210000000000146 - 龙岩
- 210000000000167 - 三明
- 210000000000168 - 宁德
- 210000000000169 - 莆田

**查询组织信息**：
```sql
SELECT org_code, org_name, org_short_name, `type`, parent_code
FROM zhss_org_new
WHERE org_name = '厦门'
```

### 3.4 数据表层级与通用字段

根据组织类型（type），数据存储在不同的表中：

| 表名 | 层级 | type | 说明 | 示例机构 |
|------|------|----------|------|----------|
| zhss_result_data_qs | 全省 | 3001 | 省级汇总数据 | 福建 |
| zhss_result_data_ds | 地市 | 3002 | 九地市数据 | 厦门、泉州、福州等 |
| zhss_result_data_qx | 区县 | 3003 | 区县级数据 | 各区县 |
| zhss_result_data_gds | 供电所 | 3005 | 供电所数据 | 各供电所 |

**所有数据表通用字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | varchar(40) | 主键 |
| index_id | varchar(54) | 指标ID |
| index_name | varchar(255) | 指标名称 |
| stat_date | date | 统计日期（月度数据存为月末日期） |
| unit | varchar(64) | 单位 |
| org_id | varchar(64) | 机构ID |
| city_type | int(11) | 地市类型（同org_type） |
| period | varchar(10) | 统计周期（日/周/月/年） |
| dim_val | varchar(255) | 维度值（用于 JOIN qlyy_dimension） |
| val | decimal(20,4) | 当期值 |
| val_tb | decimal(20,2) | 同比（%） |
| val_hb | decimal(20,2) | 环比（%） |

**索引字段**：index_id, stat_date, org_id, index_name, period, city_type

### 3.5 天气预报表（zhss_result_data_tqyb_mx）

存储天气预报数据，用于分析天气对指标的影响。

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| index_id | varchar(255) | 指标ID | IDX-1099-00010008 |
| org_id | varchar(255) | 组织ID | 210000000000167 |
| city_type | int | 地市类型 | 3002 |
| name | varchar(255)  | 地市名称 | 厦门 |
| dim_value_a | varchar(255) | 天气名称 | 晴/中雨 |
| dim_value_b | double | 最高温度（℃） | 15.6 |
| dim_value_c | double | 最低温度（℃） | 5.1 |
| dim_value_d | varchar(255) | 时间 | 2024-01-01 |
| dim_value_e | double | 经度 | 15.6 |
| dim_value_f | double | 纬度 | 5.1 |
| dim_value_g | varchar(255) | 风向 | 5.1 |
| stat_date | varchar(255) | 统计日期 | 2024-01-01 |
| period | varchar(255) | 周期 | 日 |


**查询天气数据**：
-- 示例：查询福建省2025年10月平均温度
```sql
SELECT name,(dim_value_b+dim_value_c)/2
FROM zhss_result_data_tqyb_mx
WHERE org_id = '210000000000127'
  AND stat_date = '2026-10-31'
  AND period = '月'
```

---

## 4. 特殊场景

### 4.1 四率合一指标

**表名**：`zhss_result_data_slhyzt`（不走常规决策流程，直接使用此表）

**四率指标**：投资完成率、物资领用率、入账完成率、支出完成率

**特殊字段** `dim_val`：
- dim_val = '1' → 主网数据
- dim_val = '2' → 配网数据

```sql
SELECT index_id, stat_date, val, val_tb, val_hb, unit
FROM zhss_result_data_slhyzt
WHERE index_id = '{index_id}'
  AND org_id = '{org_id}'
  AND period = '月'
  AND dim_val = '1'
  AND stat_date = '{stat_date}'
```

### 4.2 预测指标

**识别方式**：index_name LIKE '%预测%' 或 predict_flag=1

**支持层级**：仅全省（zhss_result_data_qs）和地市（zhss_result_data_ds），不支持区县和供电所。

```sql
SELECT id, index_id, index_name, unit, index_table_name
FROM zhss_index_basic_info
WHERE (scene IS NULL OR scene = '')
  AND index_name LIKE '%预测%'
```

---

## 5. 关键规则速查

### 5.1 时间格式转换

数据库 `stat_date` 为 DATE 类型，月度数据存储为**月末日期**：

| 输入 | 转换结果 |
|------|----------|
| 2025-01 | 2025-01-31 |
| 2025-02 | 2025-02-28 |
| 2025-11 | 2025-11-30 |
| 2025-12 | 2025-12-31 |

**转换方法**：取输入月份的下个月第1天，减去1天，即为月末日期。

### 5.2 周期匹配

查询前需确认指标支持请求的周期（日/周/月/年）。period 字段必须与数据实际存储的周期一致。

---

## 6. SQL 模板

### 基础查询模板（不带维度）

```sql
SELECT index_id, index_name, stat_date, val, val_tb, val_hb, unit
FROM {table_name}
WHERE index_id = '{index_id}'
  AND org_id = '{org_id}'
  AND period = '{period}'
  AND stat_date >= '{start_date}'
  AND stat_date <= '{end_date}'
    and dim_val is null 
ORDER BY stat_date DESC
```

### 带维度查询模板

**注意**：带维度查询需要容错处理。如果匹配到多个维度，逐个查询取有数据的结果；如果所有维度均无数据，回退到基础查询模板。

```sql
-- 维度查询（对每个匹配的维度分别执行）
SELECT a.index_id, a.index_name, a.stat_date, a.val, a.val_tb, a.val_hb, a.unit, b.wd_name
FROM {index_table_name}_ssx a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE a.index_id = '{index_id}'
  AND a.org_id = '{org_id}'
  AND a.period = '{period}'
  AND a.stat_date >= '{start_date}'
  AND a.stat_date <= '{end_date}'
  AND b.wd_name IN ('{维度名}')
ORDER BY a.stat_date DESC

-- 容错回退：如果上述查询无结果，使用基础查询模板查对应层级表
```

### 多时间点查询模板（同比环比）

```sql
SELECT index_id, index_name, stat_date, val, val_tb, val_hb, unit
FROM {table_name}
WHERE index_id = '{index_id}'
  AND org_id = '{org_id}'
  AND period = '{period}'
  AND stat_date IN ('{date1}', '{date2}', '{date3}')
    and dim_val is null 
ORDER BY stat_date DESC
```
