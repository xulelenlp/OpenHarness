# 同比环比分析示例

## 场景描述

用户请求分析指标的同比或环比变化。

**用户请求示例**：
- "分析厦门近3个月售电量的同比环比"
- "福建2025年12月售电量的同比情况"
- "对比泉州最近两个月的营业收入"

## 处理步骤

### 步骤1：提取查询参数

从用户请求"分析厦门近3个月售电量的同比环比"中识别：
- **指标名称**：售电量
- **地区**：厦门
- **时间范围**：近3个月
- **分析类型**：同比、环比

### 步骤2：计算需要查询的时间点

对于同比环比分析，需要查询多个特定时间点：

**当前月**：2025-12
**环比对比月**：2025-11（上个月）
**同比对比月**：2024-12（去年同期）

```python
from datetime import datetime
from dateutil.relativedelta import relativedelta

def get_comparison_periods(base_month: str, months_count: int = 1):
    """获取对比分析需要的时间点"""
    base_date = datetime.strptime(base_month, "%Y-%m")
    periods = []
    
    for i in range(months_count):
        current = base_date - relativedelta(months=i)
        # 当前月
        periods.append(current.strftime("%Y-%m"))
        # 环比月（上个月）
        prev_month = current - relativedelta(months=1)
        periods.append(prev_month.strftime("%Y-%m"))
        # 同比月（去年同期）
        prev_year = current - relativedelta(years=1)
        periods.append(prev_year.strftime("%Y-%m"))
    
    # 去重并转换为月末日期
    unique_periods = list(set(periods))
    return [convert_to_month_end(p) for p in unique_periods]

def convert_to_month_end(year_month: str) -> str:
    """转换为月末日期"""
    date = datetime.strptime(year_month, "%Y-%m")
    next_month = date + relativedelta(months=1)
    last_day = next_month - relativedelta(days=1)
    return last_day.strftime("%Y-%m-%d")
```

### 步骤3：生成SQL查询

使用 IN 子句查询多个时间点：

```sql
SELECT index_id, stat_date, val, val_tb, val_hb, unit
FROM zhss_result_data_ds
WHERE index_id = 'IDX-1001-00010001'
  AND org_id = '210000000000128'  -- 厦门
  AND period = '月'
  AND stat_date IN ('2025-12-31', '2025-11-30', '2025-10-31', 
                     '2024-12-31', '2024-11-30', '2024-10-31')
  AND dim_val IS NULL
ORDER BY stat_date DESC
```

### 步骤4：计算同比环比

```python
def calculate_comparison(data_list):
    """计算同比环比"""
    # 按日期排序
    sorted_data = sorted(data_list, key=lambda x: x['stat_date'], reverse=True)
    
    results = []
    for i, current in enumerate(sorted_data):
        result = {
            "period": current['stat_date'][:7],  # YYYY-MM
            "value": float(current['val']),
            "unit": current['unit']
        }
        
        # 环比：与上个月对比
        prev_month_date = (datetime.strptime(current['stat_date'], "%Y-%m-%d") 
                          - relativedelta(months=1))
        prev_month = next((d for d in sorted_data 
                          if d['stat_date'].startswith(prev_month_date.strftime("%Y-%m"))), None)
        
        if prev_month:
            if current['val_hb'] is not None:
                # 使用数据库中的环比值
                result['month_on_month'] = float(current['val_hb'])
            else:
                # 手动计算环比
                result['month_on_month'] = ((float(current['val']) - float(prev_month['val'])) 
                                           / float(prev_month['val']) * 100)
        
        # 同比：与去年同期对比
        prev_year_date = (datetime.strptime(current['stat_date'], "%Y-%m-%d") 
                         - relativedelta(years=1))
        prev_year = next((d for d in sorted_data 
                         if d['stat_date'].startswith(prev_year_date.strftime("%Y-%m"))), None)
        
        if prev_year:
            if current['val_tb'] is not None:
                # 使用数据库中的同比值
                result['year_on_year'] = float(current['val_tb'])
            else:
                # 手动计算同比
                result['year_on_year'] = ((float(current['val']) - float(prev_year['val'])) 
                                         / float(prev_year['val']) * 100)
        
        results.append(result)
    
    return results
```

### 步骤5：返回结果

```python
{
    "indicator": "售电量",
    "region": "厦门",
    "analysis_type": ["同比", "环比"],
    "data": [
        {
            "period": "2025-12",
            "value": 45.23,
            "unit": "亿千瓦时",
            "year_on_year": 6.5,  # 同比增长6.5%
            "month_on_month": 3.2  # 环比增长3.2%
        },
        {
            "period": "2025-11",
            "value": 43.82,
            "unit": "亿千瓦时",
            "year_on_year": 5.8,
            "month_on_month": 2.1
        },
        {
            "period": "2025-10",
            "value": 42.92,
            "unit": "亿千瓦时",
            "year_on_year": 4.9,
            "month_on_month": 1.5
        }
    ]
}
```

## 完整Python实现

```python
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.utils.mysql_tools import MysqlHelper
from app.config.settings import settings

def query_comparison_analysis(
    indicator_name: str,
    region: str,
    base_month: str,
    months_count: int = 3
):
    """查询同比环比分析"""
    db = MysqlHelper(**settings.db_info)
    
    # 1. 查询指标和组织信息（同单指标查询）
    indicator_info = db.find_one(f"""
        SELECT index_id, periods, unit
        FROM zhss_index_basic_info
        WHERE index_name = '{indicator_name}'
          AND (scene IS NULL OR scene = '')
          AND periods LIKE '%月%'
        LIMIT 1
    """)
    
    org_info = db.find_one(f"""
        SELECT org_code, type
        FROM zhss_org_new
        WHERE org_name = '{region}' OR org_short_name = '{region}'
        LIMIT 1
    """)
    
    # 2. 确定数据表
    table_map = {
        3001: 'zhss_result_data_qs',
        3002: 'zhss_result_data_ds',
        3003: 'zhss_result_data_qx',
        3005: 'zhss_result_data_gds'
    }
    table_name = table_map.get(org_info['type'], 'zhss_result_data_qs')
    
    # 3. 计算需要查询的时间点
    periods = get_comparison_periods(base_month, months_count)
    periods_str = "', '".join(periods)
    
    # 4. 查询数据
    data_sql = f"""
        SELECT index_id, stat_date, val, val_tb, val_hb, unit
        FROM {table_name}
        WHERE index_id = '{indicator_info['index_id']}'
          AND org_id = '{org_info['org_code']}'
          AND period = '月'
          AND stat_date IN ('{periods_str}')
          AND dim_val IS NULL
        ORDER BY stat_date DESC
    """
    data_list = db.find_all(data_sql)
    
    # 5. 计算同比环比
    results = calculate_comparison(data_list)
    
    return {
        "indicator": indicator_name,
        "region": region,
        "analysis_type": ["同比", "环比"],
        "data": results
    }

# 使用示例
result = query_comparison_analysis("售电量", "厦门", "2025-12", 3)
print(result)
```

## 关键注意事项

1. **时间点计算**：需要准确计算当前月、环比月、同比月
2. **数据完整性**：确保所有需要的时间点都有数据
3. **优先使用数据库值**：优先使用 val_tb 和 val_hb，如果为空再手动计算
4. **百分比格式**：同比环比通常以百分比形式展示
5. **趋势判断**：正值表示增长，负值表示下降
