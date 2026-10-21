# 单指标查询示例

## 场景描述

用户请求查询单个指标在特定时间和地区的数据。

**用户请求示例**：
- "查询福建2025年12月的售电量"
- "厦门2025年11月的营业收入是多少"
- "泉州最近一个月的线损率"

## 处理步骤

### 步骤1：提取查询参数

从用户请求中识别：
- **指标名称**：售电量
- **地区**：福建
- **时间**：2025年12月
- **周期**：月（默认）

### 步骤2：查询指标元数据

```sql
SELECT index_id, index_table_name, periods, unit
FROM zhss_index_basic_info
WHERE index_name = '售电量'
  AND (scene IS NULL OR scene = '')
  AND periods LIKE '%月%'
LIMIT 1
```

**查询结果**：
```
index_id: IDX-1001-00010001
index_table_name: zhss_result_data
periods: 日,周,月,年
unit: 亿千瓦时
```

### 步骤3：查询组织信息

```sql
SELECT org_code, type
FROM zhss_org_new
WHERE org_name = '福建' OR org_short_name = '福建'
LIMIT 1
```

**查询结果**：
```
org_code: 210000000000127
type: 3001 (省级)
```

### 步骤4：确定数据表

根据 org_type = 3001（省级），使用表：`zhss_result_data_qs`

### 步骤5：生成并执行SQL

```sql
SELECT index_id, stat_date, val, val_tb, val_hb, unit
FROM zhss_result_data_qs
WHERE index_id = 'IDX-1001-00010001'
  AND org_id = '210000000000127'
  AND period = '月'
  AND stat_date = '2025-12-31'  -- 转换为月末日期
  AND dim_val IS NULL
ORDER BY stat_date DESC
```

### 步骤6：返回结果

```python
{
    "indicator": "售电量",
    "region": "福建",
    "period": "2025-12",
    "value": 125.67,
    "unit": "亿千瓦时",
    "year_on_year": 5.2,  # 同比增长5.2%
    "month_on_month": 2.1  # 环比增长2.1%
}
```

## Python实现示例

```python
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.utils.mysql_tools import MysqlHelper
from app.config.settings import settings

def query_single_indicator(
    indicator_name: str,
    region: str,
    year_month: str,
    period: str = "月"
):
    """查询单个指标数据"""
    db = MysqlHelper(**settings.db_info)
    
    # 1. 查询指标信息
    indicator_sql = f"""
        SELECT index_id, index_table_name, periods, unit
        FROM zhss_index_basic_info
        WHERE index_name = '{indicator_name}'
          AND (scene IS NULL OR scene = '')
          AND periods LIKE '%{period}%'
        LIMIT 1
    """
    indicator_info = db.find_one(indicator_sql)
    
    if not indicator_info:
        return {"error": f"未找到指标: {indicator_name}"}
    
    # 2. 查询组织信息
    org_sql = f"""
        SELECT org_code, type
        FROM zhss_org_new
        WHERE org_name = '{region}' OR org_short_name = '{region}'
        LIMIT 1
    """
    org_info = db.find_one(org_sql)
    
    if not org_info:
        return {"error": f"未找到组织: {region}"}
    
    # 3. 确定数据表
    org_type = org_info['type']
    table_map = {
        3001: 'zhss_result_data_qs',
        3002: 'zhss_result_data_ds',
        3003: 'zhss_result_data_qx',
        3005: 'zhss_result_data_gds'
    }
    table_name = table_map.get(org_type, 'zhss_result_data_qs')
    
    # 4. 转换时间为月末日期
    date = datetime.strptime(year_month, "%Y-%m")
    next_month = date + relativedelta(months=1)
    last_day = next_month - relativedelta(days=1)
    stat_date = last_day.strftime("%Y-%m-%d")
    
    # 5. 查询数据
    data_sql = f"""
        SELECT index_id, stat_date, val, val_tb, val_hb, unit
        FROM {table_name}
        WHERE index_id = '{indicator_info['index_id']}'
          AND org_id = '{org_info['org_code']}'
          AND period = '{period}'
          AND stat_date = '{stat_date}'
          AND dim_val IS NULL
    """
    result = db.find_one(data_sql)
    
    if not result:
        return {"error": "未找到数据"}
    
    return {
        "indicator": indicator_name,
        "region": region,
        "period": year_month,
        "value": float(result['val']),
        "unit": result['unit'],
        "year_on_year": float(result['val_tb']) if result['val_tb'] else None,
        "month_on_month": float(result['val_hb']) if result['val_hb'] else None
    }

# 使用示例
result = query_single_indicator("售电量", "福建", "2025-12")
print(result)
```

## 关键注意事项

1. **时间转换**：必须将 YYYY-MM 转换为月末日期 YYYY-MM-DD
2. **周期验证**：确保指标支持请求的周期（日/周/月/年）
3. **表选择**：根据 org_type 正确选择数据表
4. **空值处理**：同比环比可能为 NULL，需要处理
5. **单位显示**：返回结果时包含单位信息
