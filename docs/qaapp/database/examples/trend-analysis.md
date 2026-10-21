# 趋势分析示例

## 场景描述

用户请求分析指标在一段时间内的变化趋势。

**用户请求示例**：
- "分析福建近12个月售电量的趋势"
- "查看厦门2025年全年营业收入的变化"
- "对比售电量和营业收入的趋势"

## 单指标趋势分析

### 步骤1：提取查询参数

从用户请求"分析福建近12个月售电量的趋势"中识别：
- **指标名称**：售电量
- **地区**：福建
- **时间范围**：近12个月
- **分析类型**：趋势

### 步骤2：计算时间范围

```python
from datetime import datetime
from dateutil.relativedelta import relativedelta

def get_time_range(months_count: int = 12):
    """获取时间范围"""
    end_date = datetime.now()
    start_date = end_date - relativedelta(months=months_count-1)
    
    # 转换为月末日期
    start_month_end = (start_date.replace(day=1) + relativedelta(months=1) 
                       - relativedelta(days=1))
    end_month_end = (end_date.replace(day=1) + relativedelta(months=1) 
                     - relativedelta(days=1))
    
    return {
        "start": start_month_end.strftime("%Y-%m-%d"),
        "end": end_month_end.strftime("%Y-%m-%d")
    }
```

### 步骤3：生成SQL查询

使用时间范围查询：

```sql
SELECT index_id, stat_date, val, val_tb, val_hb, unit
FROM zhss_result_data_qs
WHERE index_id = 'IDX-1001-00010001'
  AND org_id = '210000000000127'  -- 福建
  AND period = '月'
  AND stat_date >= '2024-12-31'
  AND stat_date <= '2025-12-31'
  AND dim_val IS NULL
ORDER BY stat_date ASC
```

### 步骤4：分析趋势

```python
def analyze_trend(data_list):
    """分析数据趋势"""
    if len(data_list) < 2:
        return {"trend": "数据不足"}
    
    # 计算总体变化率
    first_val = float(data_list[0]['val'])
    last_val = float(data_list[-1]['val'])
    total_change = ((last_val - first_val) / first_val) * 100
    
    # 判断趋势方向
    if total_change > 5:
        trend_direction = "上升"
    elif total_change < -5:
        trend_direction = "下降"
    else:
        trend_direction = "平稳"
    
    # 计算平均增长率
    growth_rates = []
    for i in range(1, len(data_list)):
        prev_val = float(data_list[i-1]['val'])
        curr_val = float(data_list[i]['val'])
        if prev_val > 0:
            rate = ((curr_val - prev_val) / prev_val) * 100
            growth_rates.append(rate)
    
    avg_growth = sum(growth_rates) / len(growth_rates) if growth_rates else 0
    
    return {
        "trend_direction": trend_direction,
        "total_change_rate": round(total_change, 2),
        "average_growth_rate": round(avg_growth, 2),
        "data_points": len(data_list)
    }
```

## 多指标对比趋势分析

### 场景：对比多个指标的趋势

**用户请求**："对比福建近6个月售电量和营业收入的趋势"

### 步骤1：查询多个指标

```python
def query_multi_indicator_trend(
    indicators: list,
    region: str,
    time_range: dict
):
    """查询多指标趋势"""
    db = MysqlHelper(**settings.db_info)
    results = {}
    
    for indicator_name in indicators:
        # 查询指标信息
        indicator_info = db.find_one(f"""
            SELECT index_id, unit
            FROM zhss_index_basic_info
            WHERE index_name = '{indicator_name}'
              AND (scene IS NULL OR scene = '')
              AND periods LIKE '%月%'
            LIMIT 1
        """)
        
        if not indicator_info:
            continue
        
        # 查询组织信息
        org_info = db.find_one(f"""
            SELECT org_code, type
            FROM zhss_org_new
            WHERE org_name = '{region}'
            LIMIT 1
        """)
        
        # 确定数据表
        table_map = {
            3001: 'zhss_result_data_qs',
            3002: 'zhss_result_data_ds',
            3003: 'zhss_result_data_qx',
            3005: 'zhss_result_data_gds'
        }
        table_name = table_map.get(org_info['type'], 'zhss_result_data_qs')
        
        # 查询数据
        data_sql = f"""
            SELECT index_id, stat_date, val, unit
            FROM {table_name}
            WHERE index_id = '{indicator_info['index_id']}'
              AND org_id = '{org_info['org_code']}'
              AND period = '月'
              AND stat_date >= '{time_range['start']}'
              AND stat_date <= '{time_range['end']}'
              AND dim_val IS NULL
            ORDER BY stat_date ASC
        """
        data_list = db.find_all(data_sql)
        
        results[indicator_name] = {
            "data": data_list,
            "trend": analyze_trend(data_list),
            "unit": indicator_info['unit']
        }
    
    return results
```

### 步骤2：生成对比图表数据

```python
def generate_comparison_chart_data(multi_results):
    """生成多指标对比图表数据"""
    # 提取所有时间点
    all_periods = set()
    for indicator_data in multi_results.values():
        for item in indicator_data['data']:
            all_periods.add(item['stat_date'][:7])  # YYYY-MM
    
    sorted_periods = sorted(list(all_periods))
    
    # 构建图表数据
    datasets = []
    for indicator_name, indicator_data in multi_results.items():
        values = []
        for period in sorted_periods:
            # 查找对应时间点的值
            period_data = next((d for d in indicator_data['data'] 
                               if d['stat_date'].startswith(period)), None)
            values.append(float(period_data['val']) if period_data else None)
        
        datasets.append({
            "label": f"{indicator_name} ({indicator_data['unit']})",
            "data": values
        })
    
    return {
        "labels": [p.replace('-', '年') + '月' for p in sorted_periods],
        "datasets": datasets
    }
```

### 步骤3：返回结果

```python
{
    "region": "福建",
    "time_range": "2025-07 至 2025-12",
    "indicators": {
        "售电量": {
            "trend": {
                "trend_direction": "上升",
                "total_change_rate": 8.5,
                "average_growth_rate": 1.4,
                "data_points": 6
            },
            "unit": "亿千瓦时",
            "data": [...]
        },
        "营业收入": {
            "trend": {
                "trend_direction": "上升",
                "total_change_rate": 10.2,
                "average_growth_rate": 1.7,
                "data_points": 6
            },
            "unit": "亿元",
            "data": [...]
        }
    },
    "chart_data": {
        "labels": ["2025年7月", "2025年8月", ...],
        "datasets": [...]
    }
}
```

## 完整Python实现

```python
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.utils.mysql_tools import MysqlHelper
from app.config.settings import settings

def query_trend_analysis(
    indicators: list,
    region: str,
    months_count: int = 12
):
    """查询趋势分析"""
    # 1. 计算时间范围
    time_range = get_time_range(months_count)
    
    # 2. 查询多指标数据
    results = query_multi_indicator_trend(indicators, region, time_range)
    
    # 3. 生成图表数据
    chart_data = generate_comparison_chart_data(results)
    
    return {
        "region": region,
        "time_range": f"{time_range['start'][:7]} 至 {time_range['end'][:7]}",
        "indicators": results,
        "chart_data": chart_data
    }

# 使用示例
result = query_trend_analysis(["售电量", "营业收入"], "福建", 6)
print(result)
```

## 关键注意事项

1. **时间范围**：使用 >= 和 <= 进行范围查询，而非 IN 子句
2. **数据排序**：按时间升序排列，便于趋势分析
3. **趋势判断**：基于总体变化率和平均增长率判断
4. **多指标对齐**：确保所有指标的时间点对齐
5. **单位处理**：不同指标可能有不同单位，需要在图表中标注
6. **数据归一化**：如果指标量级差异大，考虑归一化处理
