"""业务库表名/字段名/特殊规则的集中定义。

所有 SQL 模板只引用这里的常量，避免表名、层级映射散落在多个模块。
"""

from __future__ import annotations

T_INDEX = "zhss_index_basic_info"
T_INDEX_SIM = "zhss_index_name_sim"
T_INDEX_CONFIG = "ai_index_config"
T_INDEX_SYSTEM = "zhss_index_system"
T_SCENE = "ai_scene"
T_INDEX_DIM = "ai_index_dim"
T_DIM = "qlyy_dimension"
T_ORG = "zhss_org_new"
T_DATA_STRUCT = "ai_data_struct"
T_NEWS = "ai_power_news"
T_EVENTS = "fujian_power_events"
T_WEATHER = "zhss_result_data_tqyb_mx"
T_FOUR_RATE = "zhss_result_data_slhyzt"

T_QS = "zhss_result_data_qs"
T_DS = "zhss_result_data_ds"
T_QX = "zhss_result_data_qx"
T_GDS = "zhss_result_data_gds"

#: 机构类型 → 层级表。
LEVEL_TABLE_BY_ORG_TYPE: dict[int, str] = {
    3001: T_QS,
    3002: T_DS,
    3003: T_QX,
    3005: T_GDS,
}

#: 层级表 → 机构类型。
ORG_TYPE_BY_LEVEL_TABLE: dict[str, int] = {v: k for k, v in LEVEL_TABLE_BY_ORG_TYPE.items()}

#: 层级表识别顺序（从高到低）。
LEVEL_TABLES: tuple[str, ...] = (T_QS, T_DS, T_QX, T_GDS)

#: 数据表通用指标列（不含 id/时间戳）。
DATA_COLUMNS: tuple[str, ...] = (
    "index_id",
    "index_name",
    "stat_date",
    "period",
    "org_id",
    "city_type",
    "dim_val",
    "unit",
    "val",
    "val_lj",
    "val_tq",
    "val_jh",
    "val_mb",
    "val_wcl",
    "val_tb",
    "val_hb",
    "val_tb_zje",
    "val_hb_zje",
    "val_pm",
    "special_val",
)

#: 允许模型请求的指标列。
ALLOWED_METRICS: frozenset[str] = frozenset(DATA_COLUMNS)

#: 四率合一指标名称（走专用表 + dim_val 1/2）。
FOUR_RATE_NAMES: tuple[str, ...] = (
    "投资完成率",
    "物资领用率",
    "入账完成率",
    "支出完成率",
)
FOUR_RATE_DIM_MAIN = "1"
FOUR_RATE_DIM_DISTRIBUTION = "2"

#: 天气/温度指标关键词。
WEATHER_KEYWORDS: tuple[str, ...] = ("温度", "气温", "最高温", "最低温", "天气", "平均温")

#: “问题诊断”默认阈值，可由场景/调用方覆盖。
DEFAULT_YOY_DECLINE_THRESHOLD = -5.0
DEFAULT_REGION_DIVERGENCE_PP = 10.0
DEFAULT_CONCENTRATION_SHARE = 0.5

#: 省级机构 ID。
PROVINCE_ORG_ID = "210000000000127"

#: 用于“数据边界”摘要的关键指标（名称 → index_id）。
BOUNDARY_INDICATORS: tuple[tuple[str, str], ...] = (
    ("售电量", "IDX-1006-00010001"),
    ("电费回收率", "IDX-1006-00010003"),
    ("综合线损率", "IDX-1002-00020125"),
    ("发电量", "IDX-1043-00010002"),
)

#: 指标名/维度名中常见的“问句噪声”词，用于规范化。
QUERY_NOISE_WORDS: tuple[str, ...] = (
    "查询",
    "查一下",
    "查下",
    "看一下",
    "看下",
    "是多少",
    "多少",
    "情况",
    "数据",
    "指标",
    "请",
    "帮我",
    "分析",
    "一下",
)
