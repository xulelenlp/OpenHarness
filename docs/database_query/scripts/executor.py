"""database-query SKILL执行器"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from typing import Dict, Any, List, Optional, Callable
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

from skills.base_skill import SkillExecutor
from app.utils.query_parser import QueryParser
from app.utils.indicator_matcher import IndicatorMatcher
from app.utils.table_selector import TableSelector
from app.utils.org_mapper import OrgMapper
from app.utils.logger import setup_logger
from app.utils.dimension_dict import split_compound_indicator
from app.utils.calendar_helper import CalendarHelper
from app.config.unified_indicator_config import CITIES, PROVINCE_ORG_ID
from app.config.unified_indicator_config import DIMENSION_INDICATORS

logger = setup_logger('database_query_skill', 'logs/database_query_skill.log')


def _is_missing_table_error(error: Exception) -> bool:
    text = str(error) or ""
    lower_text = text.lower()
    return "1146" in text or ("table" in lower_text and ("doesn't exist" in lower_text or "does not exist" in lower_text))


class DatabaseQuerySkill(SkillExecutor):
    """数据库查询SKILL执行器
    
    职责：
    1. 解析和验证查询参数
    2. 匹配指标配置
    3. 生成SQL查询
    4. 执行查询并返回结果
    """
    
    def __init__(self, context: Dict[str, Any]):
        super().__init__(context)
        self.db = context['db']
        self.indicator_matcher = IndicatorMatcher(self.db)
        db_indicator_names = context.get('db_indicator_names') or list(set(
            k.split('|')[0] for k in self.indicator_matcher.name_to_id_map.keys()
        ))
        self.query_parser = QueryParser(
            db_indicator_names=db_indicator_names,
            indicator_keywords=context.get('indicator_keywords'),
            indicator_alias_map=context.get('indicator_alias_map'),
        )
        self.table_selector = TableSelector()
        self.org_mapper = OrgMapper(self.db)
    
    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """验证参数"""
        # 如果有raw_query，可以通过QueryParser解析
        if 'raw_query' in params:
            return True, None
        
        # 否则检查必需参数
        required = ['indicators', 'time_range', 'org']
        missing = [k for k in required if k not in params]
        
        if missing:
            return False, f"缺少必需参数: {', '.join(missing)}"
        
        return True, None
    
    def execute(self, params: Dict[str, Any], progress_callback: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """
        执行数据库查询

        输入参数：
        - indicators: List[str] - 指标名称列表
        - time_range: Dict - 时间范围 {start, end}
        - org: str - 组织名称
        - period: str - 数据周期（日/月/年）
        - is_main: int - 主网/配网（1/2）
        - analysis_type: List[str] - 分析类型
        - context_type: str - 使用场景（report/analysis/chart）
        - raw_query: str - 原始查询（可选，用于解析）
        
        返回：
        {
            "status": "success" | "error",
            "data": List[Dict] - 查询结果列表，每个包含indicator和DataFrame,
            "metadata": Dict - 元数据
        }
        """
        try:
            self.indicator_matcher.refresh_if_needed()
            context_keywords = self.context.get('indicator_keywords') if isinstance(self.context, dict) else None
            context_alias_map = self.context.get('indicator_alias_map') if isinstance(self.context, dict) else None
            context_names = self.context.get('db_indicator_names') if isinstance(self.context, dict) else None
            if context_keywords is not None:
                self.query_parser.indicator_keywords = context_keywords
            if context_alias_map is not None:
                self.query_parser.indicator_alias_map = context_alias_map
            if context_names is not None:
                self.query_parser.db_indicator_names = context_names

            # 1. 如果有raw_query，使用QueryParser解析
            if 'raw_query' in params:
                parsed = self.query_parser.parse(params['raw_query'])
                parsed = self.query_parser.validate_and_fix(parsed)
                params = self._merge_params(params, parsed)
            
            time_range_period = (params.get('time_range') or {}).get('period')
            if time_range_period:
                params['period'] = time_range_period

            # 1.5 如果有上游图谱查询结果，提取关联指标和预测指标
            graph_result = params.get('indicator_graph_query_result')
            related_indicator_names = []
            graph_related_details = []  # 完整的关联指标元数据（含 query_type 等）
            prediction_indicator_names = []
            if graph_result and graph_result.get('status') == 'success':
                graph_data = graph_result.get('data', {})
                related_indicator_names = graph_data.get('all_related_names', [])
                prediction_indicator_names = graph_data.get('prediction_indicators', [])

                # 提取完整的关联指标详情（包含 query_type, dimension_name 等）
                indicators_info = graph_data.get('indicators', {})
                for indicator_name, indicator_detail in indicators_info.items():
                    for rel in indicator_detail.get('related_indicators', []):
                        graph_related_details.append(rel)

                logger.info(f"从图谱获取关联指标: {len(related_indicator_names)} 个, 预测指标: {prediction_indicator_names}")

            logger.info(f"执行查询，指标: {params.get('indicators')}")

            # 2. 匹配指标配置（主指标）
            indicator_configs = self._match_indicators(params)
            
            if not indicator_configs:
                return {
                    "status": "error",
                    "data": [],
                    "metadata": {},
                    "error": "未能匹配到有效的指标"
                }
            
            # 为主指标标记 role
            for config in indicator_configs:
                config['role'] = 'main'

            # 2.5 匹配关联指标和预测指标（来自图谱查询）
            if graph_related_details:
                existing_names = {c['name'] for c in indicator_configs}
                existing_query_keys = {
                    self._make_dedup_key(c)
                    for c in indicator_configs
                }

                detail_names_seen = set()
                details_to_process = []
                for detail in graph_related_details:
                    related_name = str(detail.get('name') or '').strip()
                    if not related_name:
                        continue
                    detail_names_seen.add(related_name)
                    details_to_process.append((related_name, detail))
                for related_name in related_indicator_names:
                    if related_name not in detail_names_seen:
                        details_to_process.append((related_name, {}))

                for related_name, detail in details_to_process:
                    query_type = detail.get('query_type', '')
                    if related_name in existing_names and query_type not in ('city_level', 'dimension'):
                        continue

                    query_type = detail.get('query_type', '')
                    relation_path = detail.get('relation_path') or []
                    if not isinstance(relation_path, list):
                        relation_path = [str(relation_path)]
                    is_core_driver = 'CORE_DRIVER_OF' in relation_path

                    # 跳过外部数据指标（节假日等，但不包括天气数据）
                    if query_type == 'external':
                        logger.debug(f"跳过外部数据指标: {related_name}")
                        continue

                    # 天气数据指标：从 zhss_result_data_tqyb_mx 表查询
                    if query_type == 'weather':
                        weather_index_id = detail.get('index_id', 'IDX-1099-00010008')
                        weather_table = detail.get('table_name', 'zhss_result_data_tqyb_mx')
                        org_name = params.get('org', '福建')
                        org_id = self.org_mapper.get_org_id(org_name)

                        # 虚拟指标（如"平均气温""最高温"等）：标记 is_virtual，走 _generate_*_temp_sql 路径
                        virtual_info = self.indicator_matcher.VIRTUAL_INDICATORS.get(related_name)
                        is_virtual_weather = virtual_info is not None
                        virtual_type = virtual_info["type"] if virtual_info else "avg_temp"
                        config = {
                            "name": related_name,
                            "original_name": related_name,
                            "id": f"VIRTUAL_{virtual_type}" if is_virtual_weather else weather_index_id,
                            "table": weather_table,
                            "org_id": org_id,
                            "org_type": 'qs',
                            "confidence": 1.0,
                            "is_four_rate": False,
                            "is_virtual": is_virtual_weather,
                            "virtual_type": virtual_type,
                            "is_weather": not is_virtual_weather,
                            "role": "weather"
                        }
                        if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names):
                            logger.info(f"添加天气数据指标: {related_name} (table={weather_table}, is_virtual={is_virtual_weather})")
                        continue

                    org_name = params.get('org', '福建')
                    org_type = self.org_mapper.get_org_type(org_name)
                    org_id = self.org_mapper.get_org_id(org_name)

                    # 维度指标：直接使用图谱元数据构建 config，无需 IndicatorMatcher
                    if query_type == 'dimension':
                        config = self._build_dimension_related_config(related_name, detail, org_id, org_type)
                        if config:
                            if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names):
                                logger.info(
                                    f"添加维度关联指标: {related_name} "
                                    f"(dim={config.get('dimension_name')}, table={config.get('table')}, "
                                    f"mode={config.get('dimension_query_mode')})"
                                )
                            continue

                    configured_dimension_config = self._build_configured_dimension_related_config(
                        related_name,
                        indicator_configs,
                        org_id,
                        org_type,
                    )
                    if configured_dimension_config is not None:
                        if self._try_append_indicator_config(configured_dimension_config, indicator_configs, existing_query_keys, existing_names):
                            logger.info(
                                f"添加配置维度关联指标: {related_name} "
                                f"(dim={configured_dimension_config.get('dimension_name')}, "
                                f"table={configured_dimension_config.get('table')})"
                            )
                        continue

                    # 地市级指标：使用父指标的 index_id，但用不同 org_id 在 ds 表查询
                    if query_type == 'city_level':
                        city_org_id = detail.get('city_org_id', '')
                        parent_index_id = detail.get('index_id', '')
                        table_name = detail.get('table_name', '')
                        logger.info(f"检测到城市级指标: {related_name}, query_type={query_type}, index_id={parent_index_id}, city_org_id={city_org_id}")

                        if parent_index_id and city_org_id:
                            # 始终推断 query_mode，确保 period/dim_val 过滤正确
                            _ind_info = self.indicator_matcher.get_indicator_info(parent_index_id)
                            _tf = self._build_table_decision_fields(
                                indicator_info=_ind_info,
                                org_type=3002,
                                user_query="",
                                indicator_name=detail.get('parent_indicator', related_name),
                                enable_dimension_detection=False,
                            )
                            city_query_mode = _tf["query_mode"] or "CITY_LEVEL"
                            if not table_name:
                                table_name = _tf["table"] or 'zhss_result_data_ds'
                            logger.info(f"地市指标 {related_name} 表: {table_name} (mode={city_query_mode})")

                            config = {
                                "name": related_name,
                                "original_name": related_name,
                                "id": parent_index_id,
                                "table": table_name,
                                "org_id": city_org_id,
                                "org_type": 3002,
                                "confidence": 1.0,
                                "is_four_rate": False,
                                "is_city_level": True,
                                "city_org_id": city_org_id,
                                "parent_indicator": detail.get('parent_indicator', ''),
                                "query_mode": city_query_mode,
                                "role": "related"
                            }
                            if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names):
                                logger.info(f"添加地市关联指标: {related_name} (org_id={city_org_id}, table={table_name})")
                        else:
                            logger.warning(f"地市指标 {related_name} 配置不完整: index_id={parent_index_id}, city_org_id={city_org_id}")
                        continue

                    # 先尝试将复合地市指标按 city_level 处理（避免"福州发电量"被降级为"发电量+福建"）
                    city_config = self._resolve_compound_city_indicator(related_name, params.get('period', '月'))
                    if city_config is not None:
                        if self._try_append_indicator_config(city_config, indicator_configs, existing_query_keys, existing_names):
                            logger.info(f"添加复合地市关联指标: {related_name} -> {city_config['parent_indicator']} (org_id={city_config['org_id']}, table={city_config['table']})")
                        continue

                    # 非维度指标：通过 IndicatorMatcher 匹配
                    match_result = self.indicator_matcher.match_indicator(related_name, params.get('period', '月'))
                    if match_result:
                        index_id, matched_name, confidence = match_result

                        # 虚拟指标（如"平均气温""最高温""最低温"）：直接构建虚拟指标配置，走 _generate_weather_sql 路径
                        if index_id.startswith("VIRTUAL_"):
                            vtype = index_id[len("VIRTUAL_"):] or "avg_temp"
                            config = self._build_virtual_indicator_config(matched_name, related_name, index_id, org_id, confidence, vtype)
                            if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names):
                                logger.info(f"添加虚拟关联指标: {related_name} -> {matched_name} (VIRTUAL/{vtype}, 走 _generate_weather_sql 路径)")
                            continue

                        indicator_info = self.indicator_matcher.get_indicator_info(index_id)
                        if not indicator_info:
                            logger.warning(f"关联指标 {related_name} 无法获取 indicator_info，跳过")
                            continue
                        table_fields = self._build_table_decision_fields(
                            indicator_info=indicator_info,
                            org_type=org_type,
                            user_query="",
                            indicator_name=matched_name,
                            enable_dimension_detection=False,
                        )
                        config = self._build_standard_indicator_config(
                            matched_name, related_name, index_id, table_fields, org_id, org_type, confidence,
                            role="core_driver" if is_core_driver else "related"
                        )
                        if self._is_prediction_indicator_name(related_name, matched_name):
                            self._mark_prediction_config(config, params, related_name)
                        if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names):
                            logger.info(f"添加{'核心驱动' if is_core_driver else '关联'}指标: {related_name} -> {matched_name} ({table_fields['query_mode']})")
            elif related_indicator_names:
                # 无图谱详情时的回退：仅使用名称列表匹配
                existing_names = {c['name'] for c in indicator_configs}
                existing_query_keys = {
                    self._make_dedup_key(c, full=False)
                    for c in indicator_configs
                }

                for related_name in related_indicator_names:
                    if related_name in existing_names:
                        continue

                    org_name = params.get('org', '福建')
                    org_type = self.org_mapper.get_org_type(org_name)
                    org_id = self.org_mapper.get_org_id(org_name)

                    configured_dimension_config = self._build_configured_dimension_related_config(
                        related_name,
                        indicator_configs,
                        org_id,
                        org_type,
                    )
                    if configured_dimension_config is not None:
                        if self._try_append_indicator_config(configured_dimension_config, indicator_configs, existing_query_keys, existing_names, full_key=False):
                            logger.info(
                                f"添加配置维度关联指标(回退路径): {related_name} "
                                f"(dim={configured_dimension_config.get('dimension_name')}, "
                                f"table={configured_dimension_config.get('table')})"
                            )
                        continue

                    # 先尝试将复合地市指标按 city_level 处理（避免"福州发电量"被降级为"发电量+福建"）
                    city_config = self._resolve_compound_city_indicator(related_name, params.get('period', '月'))
                    if city_config is not None:
                        if self._try_append_indicator_config(city_config, indicator_configs, existing_query_keys, existing_names, full_key=False):
                            logger.info(f"添加复合地市关联指标(回退路径): {related_name} -> {city_config['parent_indicator']} (org_id={city_config['org_id']}, table={city_config['table']})")
                        continue

                    match_result = self.indicator_matcher.match_indicator(related_name, params.get('period', '月'))
                    if match_result:
                        index_id, matched_name, confidence = match_result

                        # 虚拟指标（如"平均气温""最高温""最低温"）：直接构建虚拟指标配置，走 _generate_weather_sql 路径
                        if index_id.startswith("VIRTUAL_"):
                            vtype = index_id[len("VIRTUAL_"):] or "avg_temp"
                            config = self._build_virtual_indicator_config(matched_name, related_name, index_id, org_id, confidence, vtype)
                            if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names, full_key=False):
                                logger.info(f"添加虚拟关联指标: {related_name} -> {matched_name} (VIRTUAL/{vtype}, 走 _generate_weather_sql 路径)")
                            continue

                        indicator_info = self.indicator_matcher.get_indicator_info(index_id)
                        if not indicator_info:
                            logger.warning(f"关联指标 {related_name} 无法获取 indicator_info，跳过")
                            continue
                        table_fields = self._build_table_decision_fields(
                            indicator_info=indicator_info,
                            org_type=org_type,
                            user_query="",
                            indicator_name=matched_name,
                            enable_dimension_detection=False,
                        )
                        config = self._build_standard_indicator_config(
                            matched_name, related_name, index_id, table_fields, org_id, org_type, confidence
                        )
                        if self._is_prediction_indicator_name(related_name, matched_name):
                            self._mark_prediction_config(config, params, related_name)
                        if self._try_append_indicator_config(config, indicator_configs, existing_query_keys, existing_names, full_key=False):
                            logger.info(f"添加关联指标: {related_name} -> {matched_name} ({table_fields['query_mode']})")

            if prediction_indicator_names:
                existing_names = {c['name'] for c in indicator_configs}
                for pred_name in prediction_indicator_names:
                    existing_config = next(
                        (
                            c for c in indicator_configs
                            if pred_name in {c.get('name'), c.get('original_name')}
                            or self._is_prediction_indicator_name(c.get('name'), c.get('original_name')) and c.get('name') == pred_name
                        ),
                        None
                    )
                    if existing_config:
                        self._mark_prediction_config(existing_config, params, pred_name)
                        existing_names.add(existing_config.get('name', ''))
                        logger.info(f"已将已有指标配置升级为预测指标: {pred_name}")
                        continue

                    if pred_name not in existing_names:
                        match_result = self.indicator_matcher.match_indicator(pred_name, params.get('period', '月'))
                        if match_result:
                            index_id, matched_name, confidence = match_result
                            # 匹配到的必须是预测指标（名称含"预测"），否则说明数据库无此预测指标，跳过
                            if '预测' not in matched_name:
                                logger.info(f"预测指标 {pred_name} 未找到对应预测数据（匹配到 {matched_name}），跳过")
                                continue
                            org_name = params.get('org', '福建')
                            org_type = self.org_mapper.get_org_type(org_name)
                            # 预测指标仅支持省级(3001)和地市(3002)
                            if org_type not in [3001, 3002]:
                                logger.info(f"预测指标 {pred_name} 不支持 {org_type} 层级（仅支持省级3001和地市级3002），跳过")
                                continue
                            org_id = self.org_mapper.get_org_id(org_name)
                            indicator_info = self.indicator_matcher.get_indicator_info(index_id)
                            table_fields = self._build_table_decision_fields(
                                indicator_info=indicator_info,
                                org_type=org_type,
                                user_query=params.get('raw_query', ''),
                                indicator_name=matched_name,
                                enable_dimension_detection=True,
                            )
                            config = {
                                "name": matched_name,
                                "original_name": pred_name,
                                "id": index_id,
                                "table": table_fields["table"],
                                "org_id": org_id,
                                "org_type": org_type,
                                "confidence": confidence,
                                "is_four_rate": self.table_selector.is_four_rate_indicator(matched_name),
                                "query_mode": table_fields["query_mode"],
                                "is_dimension": table_fields["is_dimension"],
                                "dimension_query_mode": table_fields["dimension_query_mode"],
                                "matched_dimensions": table_fields["matched_dimensions"],
                                "fallback_table": table_fields["fallback_table"],
                                "role": "prediction"
                            }
                            self._mark_prediction_config(config, params, pred_name)
                            indicator_configs.append(config)
                            existing_names.add(matched_name)
                            logger.info(f"添加预测指标: {pred_name} -> {matched_name}")

            # 3. 生成SQL查询
            sql_queries = self._generate_sql_queries(indicator_configs, params)

            # 3.5 天气温度指标去重：avg_temp/max_temp/min_temp 指向同一张表同一时间范围，
            #     只需执行一次查询，结果各列派生即可，避免重复查询膨胀 prompt。
            sql_queries, weather_derived = self._dedup_weather_sql_queries(sql_queries)

            # 4. 执行查询（支持流式进度回调）
            if progress_callback:
                progress_callback(f"准备查询 {len(sql_queries)} 个指标...")

            query_results = self._execute_queries(sql_queries, progress_callback)

            # 4.5 从已执行的天气查询结果中派生出去重掉的温度指标结果
            if weather_derived:
                query_results = self._expand_derived_weather_results(query_results, weather_derived)

            executed_sql_queries = self._build_executed_sql_queries(query_results)

            overview_sql_queries = []
            overview_query_results = []
            overview_executed_sql_queries = []
            if params.get('enable_multi_main_overview_base_first'):
                overview_sql_queries = self._build_multi_main_overview_sql_queries(indicator_configs, params)
                if overview_sql_queries:
                    if progress_callback:
                        progress_callback(f"准备查询 {len(overview_sql_queries)} 个指标总体情况...")
                    overview_query_results = self._execute_queries(overview_sql_queries, progress_callback)
                    overview_executed_sql_queries = self._build_executed_sql_queries(overview_query_results)

            logger.info(f"查询完成，返回 {len(query_results)} 个结果，指标总体情况 {len(overview_query_results)} 个结果")

            return {
                "status": "success",
                "data": query_results,
                "overview_data": overview_query_results,
                "sql_queries": sql_queries,
                "overview_sql_queries": overview_sql_queries,
                "executed_sql_queries": executed_sql_queries,
                "overview_executed_sql_queries": overview_executed_sql_queries,
                "indicators": params.get('indicators', []),
                "metadata": {
                    "indicator_count": len(query_results),
                    "overview_indicator_count": len(overview_query_results),
                    "sql_queries": sql_queries,
                    "overview_sql_queries": overview_sql_queries,
                    "generated_sql_queries": sql_queries,
                    "executed_sql_queries": executed_sql_queries,
                    "overview_executed_sql_queries": overview_executed_sql_queries,
                    "indicator_configs": indicator_configs
                }
            }
            
        except Exception as e:
            logger.error(f"查询执行失败: {e}", exc_info=True)
            return {
                "status": "error",
                "data": [],
                "metadata": {},
                "error": str(e)
            }
    
    def _merge_params(self, params: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
        """合并参数和解析结果"""
        merged = params.copy()
        
        # 从parsed中补充缺失的参数
        if 'indicators' not in merged or not merged['indicators']:
            merged['indicators'] = parsed.get('indicators', [])
        
        if 'time_range' not in merged or not merged['time_range']:
            merged['time_range'] = parsed.get('time_range', {})
        
        if 'org' not in merged:
            merged['org'] = parsed.get('filters', {}).get('org', '福建')
        
        if 'period' not in merged:
            merged['period'] = parsed.get('filters', {}).get('period', '月')
        
        if 'is_main' not in merged:
            merged['is_main'] = parsed.get('filters', {}).get('is_main', 1)
        
        if 'analysis_type' not in merged:
            merged['analysis_type'] = parsed.get('analysis_type', [])
        
        return merged

    # ─────────────────────────────────────────────────────────────────────────
    # 关联指标去重 / config 构建辅助（供 Path 1 和 Path 2 共用）
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_dedup_key(config: Dict[str, Any], full: bool = True) -> tuple:
        """构建关联指标去重键。

        Args:
            config: 指标配置 dict
            full: True（Path 1）= 含地市/维度字段的 9-tuple；
                  False（Path 2 回退路径）= 简化 4-tuple
        """
        if full:
            dim_key = config.get('dimension_id', '') or config.get('dimension_name', '')
            return (
                config.get('id'),
                config.get('org_id'),
                config.get('role', 'main'),
                config.get('parent_indicator', ''),
                bool(config.get('is_city_level')),
                config.get('city_org_id', ''),
                bool(config.get('is_dimension')),
                dim_key,
                config.get('dimension_group_key', ''),
            )
        # 回退路径：维度指标带 dimension_id，避免同父指标被误去重
        return (
            config.get('id'),
            config.get('org_id'),
            config.get('role', 'main'),
            config.get('dimension_id', '') if config.get('is_dimension') else '',
        )

    @classmethod
    def _try_append_indicator_config(
        cls,
        config: Dict[str, Any],
        indicator_configs: List[Dict[str, Any]],
        existing_query_keys: set,
        existing_names: set,
        full_key: bool = True,
    ) -> bool:
        """若 config 不在已有集合中则追加，返回是否成功追加。"""
        dedup_key = cls._make_dedup_key(config, full=full_key)
        if dedup_key in existing_query_keys:
            logger.info(
                f"跳过重复关联指标配置{'(回退路径)' if not full_key else ''}: "
                f"name={config.get('name')}, id={config.get('id')}, "
                f"org_id={config.get('org_id')}, role={config.get('role')}, "
                f"dimension_id={config.get('dimension_id', '')}"
            )
            return False
        indicator_configs.append(config)
        existing_query_keys.add(dedup_key)
        existing_names.add(config.get('name', ''))
        existing_names.add(config.get('original_name', ''))
        return True

    @staticmethod
    def _build_virtual_indicator_config(
        matched_name: str,
        related_name: str,
        index_id: str,
        org_id: Any,
        confidence: float,
        vtype: str,
    ) -> Dict[str, Any]:
        """构建虚拟指标（天气类）config dict。"""
        return {
            "name": matched_name,
            "original_name": related_name,
            "id": index_id,
            "table": "zhss_result_data_tqyb_mx",
            "org_id": org_id,
            "org_type": "qs",
            "confidence": confidence,
            "is_four_rate": False,
            "is_virtual": True,
            "virtual_type": vtype,
            "is_weather": False,
            "role": "weather",
        }

    def _build_standard_indicator_config(
        self,
        matched_name: str,
        related_name: str,
        index_id: str,
        table_fields: Dict[str, Any],
        org_id: Any,
        org_type: Any,
        confidence: float,
        role: str = 'related',
    ) -> Dict[str, Any]:
        """构建标准关联指标 config dict。"""
        return {
            "name": matched_name,
            "original_name": related_name,
            "id": index_id,
            "table": table_fields["table"],
            "org_id": org_id,
            "org_type": org_type,
            "confidence": confidence,
            "is_four_rate": self.table_selector.is_four_rate_indicator(matched_name),
            "query_mode": table_fields["query_mode"],
            "is_dimension": table_fields["is_dimension"],
            "dimension_query_mode": table_fields["dimension_query_mode"],
            "matched_dimensions": table_fields["matched_dimensions"],
            "fallback_table": table_fields["fallback_table"],
            "role": role,
        }

    @staticmethod
    def _is_prediction_indicator_name(*names: Any) -> bool:
        """判断指标名称是否表示预测值指标。"""
        for name in names:
            text = str(name or '').strip()
            if text.endswith('预测') or text.endswith('预测值'):
                return True
        return False

    @staticmethod
    def _month_key(value: Any) -> str:
        text = str(value or '').strip()
        return text[:7] if len(text) >= 7 else text

    @staticmethod
    def _day_key(value: Any) -> str:
        text = str(value or '').strip()
        return text[:10] if len(text) >= 10 else text

    def _build_prediction_time_range(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """预测指标默认向报告期后扩展：月级 3 个月，日级 7 天。"""
        time_range = dict(params.get('time_range') or {})
        end = time_range.get('end')
        if not end:
            return None

        period = params.get('period') or time_range.get('period', '月')
        try:
            if period == '月':
                end_month = self._month_key(end)
                end_date = datetime.strptime(end_month, "%Y-%m")
                extended_end = end_date + relativedelta(months=3)
                time_range['end'] = extended_end.strftime("%Y-%m")
                logger.info(f"预测指标时间范围扩展: {end_month} -> {time_range['end']} (增加3个月)")
                return time_range
            if period == '日':
                end_day = self._day_key(end)
                end_date = datetime.strptime(end_day, "%Y-%m-%d")
                extended_end = end_date + relativedelta(days=7)
                time_range['end'] = extended_end.strftime("%Y-%m-%d")
                logger.info(f"预测指标时间范围扩展: {end_day} -> {time_range['end']} (增加7天)")
                return time_range
        except Exception as e:
            logger.warning(f"预测指标时间范围扩展失败: {e}")
        return None

    def _mark_prediction_config(self, config: Dict[str, Any], params: Dict[str, Any], original_name: str = '') -> None:
        config['role'] = 'prediction'
        extended_time_range = self._build_prediction_time_range(params)
        if extended_time_range:
            config['extended_time_range'] = extended_time_range
        if original_name and not config.get('original_name'):
            config['original_name'] = original_name
        parent = str(config.get('parent_indicator') or '').strip()
        name = str(config.get('name') or original_name or '').strip()
        if not parent and name.endswith('预测'):
            config['parent_indicator'] = name[:-2]

    def _build_dimension_related_config(
        self,
        related_name: str,
        detail: Dict[str, Any],
        org_id: str,
        org_type: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        dimension_name = detail.get('dimension_name', '')
        dimension_id = detail.get('dimension_id', '')
        parent_index_id = detail.get('index_id', '')
        table_name = detail.get('table_name', '')
        parent_indicator = detail.get('parent_indicator', '')

        if not (parent_index_id and table_name and dimension_name):
            return None

        dimension_query_mode = "ssx_join" if table_name.endswith("_ssx") else "base_join"
        fallback_table = self.table_selector.get_table_decision(
            indicator_info=None,
            org_type=org_type,
            user_query="",
            indicator_name=parent_indicator
        ).get("table_name", "")
        return {
            "name": related_name,
            "original_name": related_name,
            "id": parent_index_id,
            "table": table_name,
            "org_id": org_id,
            "org_type": org_type,
            "confidence": 1.0,
            "is_four_rate": False,
            "is_dimension": True,
            "dimension_query_mode": dimension_query_mode,
            "dimension_name": dimension_name,
            "dimension_id": dimension_id,
            "matched_dimensions": [dimension_name],
            "dimension_group_key": detail.get('dimension_group_key', ''),
            "dimension_group_name": detail.get('dimension_group_name', ''),
            "dimension_type": detail.get('dimension_type', ''),
            "fallback_table": fallback_table,
            "parent_indicator": parent_indicator,
            "query_mode": "GRAPH_DIMENSION",
            "role": "related"
        }

    def _build_configured_dimension_related_config(
        self,
        related_name: str,
        indicator_configs: List[Dict[str, Any]],
        org_id: str,
        org_type: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        dim_config = DIMENSION_INDICATORS.get(related_name)
        if not dim_config:
            return None

        parent_indicator = dim_config.get('parent', '')
        parent_configs = [
            config for config in indicator_configs
            if config.get('role') == 'main' and config.get('name') == parent_indicator
        ]
        if not parent_configs:
            return None

        parent_index_id = parent_configs[0].get('id') or dim_config.get('index_id', '')
        detail = {
            'index_id': parent_index_id,
            'table_name': dim_config.get('table', ''),
            'dimension_name': dim_config.get('dimension_name', related_name),
            'dimension_id': dim_config.get('dimension_id', ''),
            'parent_indicator': parent_indicator,
            'dimension_group_key': dim_config.get('group_key', ''),
            'dimension_group_name': dim_config.get('group_name', ''),
            'dimension_type': dim_config.get('dimension_type', ''),
        }
        return self._build_dimension_related_config(related_name, detail, org_id, org_type)

    def _resolve_compound_city_indicator(
        self,
        related_name: str,
        period: str,
    ) -> Optional[Dict[str, Any]]:
        """Try to resolve a compound city indicator name (e.g. "福州发电量") into a config dict.

        Returns a config dict if the name is a compound city indicator that can be matched,
        or None if it should be handled by the normal matching path.
        """
        split_related = split_compound_indicator(related_name)
        if not (split_related.get("is_compound") and split_related.get("org_type") == 3002):
            return None
        city_org_id = split_related.get("org_id", "")
        pure_indicator = split_related.get("indicator", "")
        city_match = self.indicator_matcher.match_indicator(pure_indicator, period)
        if not (city_org_id and city_match):
            return None
        parent_index_id, parent_name, _ = city_match
        _ind_info = self.indicator_matcher.get_indicator_info(parent_index_id)
        _tf = self._build_table_decision_fields(
            indicator_info=_ind_info,
            org_type=3002,
            user_query="",
            indicator_name=parent_name,
            enable_dimension_detection=False,
        )
        return {
            "name": related_name,
            "original_name": related_name,
            "id": parent_index_id,
            "table": _tf["table"] or 'zhss_result_data_ds',
            "org_id": city_org_id,
            "org_type": 3002,
            "confidence": 1.0,
            "is_four_rate": False,
            "is_city_level": True,
            "city_org_id": city_org_id,
            "parent_indicator": parent_name,
            "query_mode": _tf["query_mode"] or "CITY_LEVEL",
            "role": "related",
        }

    def _build_table_decision_fields(
        self,
        indicator_info: Optional[Dict[str, Any]],
        org_type: Optional[int],
        user_query: str,
        indicator_name: str,
        enable_dimension_detection: bool = True,
    ) -> Dict[str, Any]:
        """构建表选择决策字段（A1/A2/B1/B2）"""
        table_decision = self.table_selector.get_table_decision(
            indicator_info=indicator_info,
            org_type=org_type,
            user_query=user_query if enable_dimension_detection else "",
            indicator_name=indicator_name
        )

        is_dimension_query = table_decision.get("is_dimension_query", False)
        fallback_table = table_decision.get("fallback_table", "")
        if is_dimension_query and not fallback_table:
            fallback_table = self.table_selector.get_table_decision(
                indicator_info=None,
                org_type=org_type,
                user_query="",
                indicator_name=indicator_name
            ).get("table_name", "")

        return {
            "table": table_decision.get("table_name", ""),
            "query_mode": table_decision.get("query_mode", ""),
            "is_dimension": is_dimension_query,
            "dimension_query_mode": table_decision.get("dimension_query_mode", ""),
            "matched_dimensions": table_decision.get("matched_dimensions", []),
            "fallback_table": fallback_table,
        }

    def _match_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """匹配指标配置"""
        indicators = params.get('indicators', [])
        org_name = params.get('org', '福建')
        period = params.get('period', '月')
        disable_main_dimension_detection = bool(params.get('disable_main_dimension_detection'))

        indicator_configs = []
        
        for indicator_name in indicators:
            # 复合指标检测：提取机构信息（如"福州售电量" → org='福州'）
            effective_org_name = org_name
            split_result = split_compound_indicator(indicator_name)
            if split_result["is_compound"]:
                effective_org_name = split_result["org_name"]
                logger.info(f"复合指标 '{indicator_name}' 提取机构: {effective_org_name}")

            # 尝试匹配指标
            match_result = self.indicator_matcher.match_indicator(indicator_name, period)

            if match_result:
                index_id, matched_name, confidence = match_result

                # 虚拟指标（如"平均气温""最高温""最低温"等）：不走常规表选择逻辑
                if index_id.startswith("VIRTUAL_"):
                    org_id = self.org_mapper.get_org_id(effective_org_name)
                    virtual_info = self.indicator_matcher.VIRTUAL_INDICATORS.get(matched_name, {})
                    virtual_type = virtual_info.get("type", "avg_temp")
                    config = {
                        "name": matched_name,
                        "original_name": indicator_name,
                        "id": index_id,
                        "table": "zhss_result_data_tqyb_mx",
                        "org_id": org_id,
                        "org_type": None,
                        "confidence": confidence,
                        "is_four_rate": False,
                        "is_virtual": True,
                        "virtual_type": virtual_type,
                    }
                    indicator_configs.append(config)
                    logger.info(f"匹配虚拟指标: {indicator_name} -> {index_id}")
                    continue

                # 获取组织信息
                org_type = self.org_mapper.get_org_type(effective_org_name)
                org_id = self.org_mapper.get_org_id(effective_org_name)

                # 确定数据表与查询模式（A1/A2/B1/B2）
                indicator_info = self.indicator_matcher.get_indicator_info(index_id)
                table_fields = self._build_table_decision_fields(
                    indicator_info=indicator_info,
                    org_type=org_type,
                    user_query='' if disable_main_dimension_detection else params.get('raw_query', ''),
                    indicator_name=matched_name,
                    enable_dimension_detection=not disable_main_dimension_detection,
                )

                config = {
                    "name": matched_name,
                    "original_name": indicator_name,
                    "id": index_id,
                    "table": table_fields["table"],
                    "org_id": org_id,
                    "org_type": org_type,
                    "confidence": confidence,
                    "is_four_rate": self.table_selector.is_four_rate_indicator(matched_name),
                    "query_mode": table_fields["query_mode"],
                    "is_dimension": table_fields["is_dimension"],
                    "dimension_query_mode": table_fields["dimension_query_mode"],
                    "matched_dimensions": table_fields["matched_dimensions"],
                    "fallback_table": table_fields["fallback_table"],
                }
                indicator_configs.append(config)
                logger.info(f"匹配指标: {indicator_name} -> {matched_name} (置信度: {confidence})")
            else:
                logger.warning(f"未能匹配指标: {indicator_name}")
        
        return indicator_configs
    
    def _generate_sql_queries(
        self,
        indicator_configs: List[Dict[str, Any]],
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """生成SQL查询"""
        sql_queries = []

        for config in indicator_configs:
            query_info = self._build_sql_query_info(config, params)
            sql_queries.append(query_info)

        return sql_queries

    # ── 天气温度指标去重 ────────────────────────────────────────────────────────

    _WEATHER_VIRTUAL_TYPES = {"avg_temp", "max_temp", "min_temp"}

    def _dedup_weather_sql_queries(
        self, sql_queries: List[Dict[str, Any]]
    ) -> tuple:
        """将同一 org/period/时间范围的多个天气温度查询合并为一条。

        三种虚拟温度指标（avg_temp / max_temp / min_temp）的 SQL 只有第二行
        val 表达式不同，其余 FROM/WHERE 完全相同。去重后仅执行一次查询，
        通过 _expand_derived_weather_results 从结果列派生其余指标的 val。

        Returns:
            (pruned_sql_queries, derived_list)
            derived_list: [{'indicator': name, 'source': canonical_name,
                            'virtual_type': 'max_temp'|'min_temp'|'avg_temp',
                            'query_info_template': query_info_dict}]
        """
        def _weather_group_key(query_info: Dict[str, Any]) -> Optional[str]:
            """提取去重 key：SQL 第三行起的不变部分（FROM/WHERE/ORDER BY）。"""
            iid = query_info.get('indicator_id', '')
            vtype = iid.replace('VIRTUAL_', '') if iid.startswith('VIRTUAL_') else ''
            if vtype not in self._WEATHER_VIRTUAL_TYPES:
                return None
            sql = query_info.get('sql', '')
            lines = sql.strip().split('\n')
            # 第 1 行 SELECT stat_date, 第 2 行 val_expr, 第 3 行起为常量部分
            if len(lines) < 3:
                return sql  # 格式不符预期，整体作 key
            return '\n'.join(lines[2:])

        # 第一遍：按 key 记录每组第一个（canonical）
        seen: Dict[str, str] = {}  # key → canonical indicator name
        pruned: List[Dict[str, Any]] = []
        derived: List[Dict[str, Any]] = []

        for q in sql_queries:
            key = _weather_group_key(q)
            if key is None:
                pruned.append(q)
                continue
            if key not in seen:
                seen[key] = q['indicator']
                pruned.append(q)
                logger.debug(f"[天气去重] 保留 canonical: {q['indicator']} (key hash={hash(key) & 0xFFFF:04x})")
            else:
                vtype = q.get('indicator_id', '').replace('VIRTUAL_', '')
                derived.append({
                    'indicator': q['indicator'],
                    'source': seen[key],
                    'virtual_type': vtype,
                    'query_info_template': q,  # 保留完整 query_info 便于构建结果
                })
                logger.info(
                    f"[天气去重] 跳过重复查询 {q['indicator']} (vtype={vtype})，"
                    f"将从 {seen[key]} 结果派生"
                )

        if derived:
            logger.info(f"[天气去重] 合并后查询数: {len(pruned)}（节省 {len(derived)} 次重复天气查询）")

        return pruned, derived

    def _expand_derived_weather_results(
        self,
        query_results: List[Dict[str, Any]],
        derived_list: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """从 canonical 天气查询结果中派生被去重的温度指标结果。

        - max_temp  → df['max_temp'] 列即为 val
        - min_temp  → df['min_temp'] 列即为 val
        - avg_temp  → (df['max_temp'] + df['min_temp']) / 2 四舍五入一位小数
        """
        # 建立 indicator_name → result 的索引
        result_map = {r['indicator']: r for r in query_results}

        expanded = list(query_results)
        for item in derived_list:
            source_name = item['source']
            vtype = item['virtual_type']
            derived_name = item['indicator']
            source_result = result_map.get(source_name)
            if source_result is None:
                logger.warning(f"[天气去重展开] 找不到 source 结果: {source_name}，跳过 {derived_name}")
                continue

            source_df: pd.DataFrame = source_result.get('data', pd.DataFrame())
            if source_df.empty or 'max_temp' not in source_df.columns or 'min_temp' not in source_df.columns:
                # 无原始数据列，克隆空结果即可
                derived_df = source_df.copy()
            else:
                derived_df = source_df.copy()
                if vtype == 'max_temp':
                    derived_df['val'] = derived_df['max_temp']
                elif vtype == 'min_temp':
                    derived_df['val'] = derived_df['min_temp']
                else:  # avg_temp
                    derived_df['val'] = ((derived_df['max_temp'] + derived_df['min_temp']) / 2).round(1)

            # 构建派生结果，复用 canonical 的状态字段
            qit = item['query_info_template']
            derived_result = {
                **source_result,
                'indicator': derived_name,
                'indicator_id': qit.get('indicator_id', source_result.get('indicator_id')),
                'data': derived_df,
                'row_count': len(derived_df),
                'sql': source_result.get('sql', ''),
                'actual_sql': source_result.get('actual_sql', ''),
                'original_sql': source_result.get('original_sql', ''),
                'role': qit.get('role', source_result.get('role', 'related')),
                'is_main': qit.get('role', 'related') == 'main',
            }
            expanded.append(derived_result)
            logger.info(f"[天气去重展开] 派生 {derived_name} (vtype={vtype}) 从 {source_name}，{len(derived_df)} 行")

        return expanded

    # ── END 天气温度指标去重 ────────────────────────────────────────────────────

    def _build_sql_query_info(self, config: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        sql = self._generate_sql(config, params)

        logger.debug(
            "[SQL生成] 指标=%s  表=%s  index_id=%s  org_id=%s  org_type=%s  角色=%s  模式=%s/%s\nSQL:\n%s",
            config['name'], config['table'], config['id'],
            config['org_id'], config.get('org_type', 'N/A'),
            config.get('role', 'main'),
            config.get('query_mode', ''),
            config.get('dimension_query_mode', ''),
            sql
        )

        query_info = {
            "indicator": config['name'],
            "indicator_id": config['id'],
            "sql": sql,
            "description": config.get('description') or f"查询{config['name']}数据",
            "role": config.get('role', 'main'),
            "query_mode": config.get('query_mode', ''),
            "is_dimension": config.get('is_dimension', False),
            "dimension_query_mode": config.get('dimension_query_mode', ''),
            "matched_dimensions": config.get('matched_dimensions', []),
            "is_city_level": config.get('is_city_level', False),
            "parent_indicator": config.get('parent_indicator', ''),
            "dimension_group_key": config.get('dimension_group_key', ''),
            "dimension_group_name": config.get('dimension_group_name', ''),
            "dimension_type": config.get('dimension_type', ''),
            "overview_scope": config.get('overview_scope', ''),
        }

        # 日历虚拟指标需要 params（时间范围）和 virtual_type 供 _generate_calendar_data 使用
        if config.get("virtual_type") in ("day_nums", "holiday_days"):
            query_info["virtual_type"] = config["virtual_type"]
            query_info["params"] = params

        # 维度查询需要携带回退信息（A2/B2）
        if config.get('is_dimension'):
            query_info["fallback_config"] = config.copy()
            query_info["params"] = params
            query_info["allow_dimension_fallback"] = True

        if config.get('allow_overview_dimension_fallback'):
            query_info["allow_overview_dimension_fallback"] = True
            query_info["overview_dimension_fallback_config"] = config.get('overview_dimension_fallback_config')
            query_info["params"] = params

        return query_info

    def _build_multi_main_overview_sql_queries(
        self,
        indicator_configs: List[Dict[str, Any]],
        params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        overview_queries = []
        for config in indicator_configs:
            if config.get('role') != 'main':
                continue
            if config.get('is_virtual') or config.get('is_weather'):
                continue

            table_fields = self._build_table_decision_fields(
                indicator_info=self.indicator_matcher.get_indicator_info(config.get('id')),
                org_type=config.get('org_type'),
                user_query='',
                indicator_name=config.get('name', ''),
                enable_dimension_detection=False,
            )
            overview_config = config.copy()
            overview_config.update({
                "table": table_fields["table"],
                "query_mode": "MULTI_MAIN_OVERVIEW_BASE_FIRST",
                "is_dimension": False,
                "dimension_query_mode": "",
                "matched_dimensions": [],
                "fallback_table": table_fields["fallback_table"],
                "overview_scope": params.get('overview_scope') or 'multi_main_metric_chapter2',
                "description": f"查询{config.get('name', '')}指标总体情况",
            })
            if config.get('is_dimension') and not params.get('enable_multi_main_overview_base_first'):
                overview_config["allow_overview_dimension_fallback"] = True
                overview_config["overview_dimension_fallback_config"] = config.copy()
            overview_queries.append(self._build_sql_query_info(overview_config, params))

        return overview_queries

    def _should_add_dim_val_is_null(self, indicator_config: Dict[str, Any]) -> bool:
        """判断当前查询是否需要追加 dim_val IS NULL。"""
        table = indicator_config.get("table", "") or ""
        role = indicator_config.get("role", "main")

        # 关联指标（role=related）不加 dim_val IS NULL
        if role != "main":
            return False

        if indicator_config.get("is_dimension"):
            return False

        if indicator_config.get("is_city_level"):
            # ds 表或 A1_BASE 路径（如 zhss_result_data_qlzb）都需要加 dim_val IS NULL
            return table == "zhss_result_data_ds" or indicator_config.get("query_mode") == "A1_BASE"

        if indicator_config.get("is_four_rate", False):
            return False

        if indicator_config.get("is_virtual") or indicator_config.get("is_weather"):
            return False

        if table == "zhss_result_data_tqyb_mx" or table == "zhss_result_data_slhyzt":
            return False

        if table.endswith("_ssx"):
            return False

        # A1_BASE 路径（one_table=1，无维度）直接查 index_table_name，需加 dim_val IS NULL
        if indicator_config.get("query_mode") == "A1_BASE":
            return True

        standard_result_tables = {
            "zhss_result_data_qs",
            "zhss_result_data_ds",
            "zhss_result_data_qx",
            "zhss_result_data_gds",
            "zhss_result_data_qlzb",
        }
        return table in standard_result_tables

    def _generate_sql(
        self,
        indicator_config: Dict[str, Any],
        params: Dict[str, Any]
    ) -> str:
        """生成单个指标的SQL查询"""
        # 日历指标（当月天数、节假日天数等）走专用路径
        virtual_type = indicator_config.get("virtual_type", "")
        if virtual_type in ["day_nums", "holiday_days"]:
            logger.info(f"[虚拟指标] 识别为日历指标: {virtual_type}")
            return self._generate_calendar_sql(indicator_config, params)

        # 温度/天气指标统一走专用路径（虚拟气温指标 + 天气表指标）
        if indicator_config.get("is_virtual") or indicator_config.get("is_weather"):
            logger.info(f"[虚拟指标] 识别为温度指标: {indicator_config.get('virtual_type', 'unknown')}")
            return self._generate_weather_sql(indicator_config, params)

        # 维度指标走专用路径（A2/B2）
        if indicator_config.get("is_dimension") and indicator_config.get("matched_dimensions"):
            return self._generate_dimension_sql(indicator_config, params)

        table = indicator_config.get("table")
        indicator_id = indicator_config.get("id")
        org_id = indicator_config.get("org_id")
        is_four_rate = indicator_config.get("is_four_rate", False)

        # 预测指标使用扩展的时间范围（包含未来预测值）
        if indicator_config.get("role") == "prediction" and indicator_config.get("extended_time_range"):
            time_range = indicator_config.get("extended_time_range")
            logger.info(f"预测指标 {indicator_config.get('name')} 使用扩展时间范围: {time_range}")
        else:
            # fallback：指标名含"预测"但 extended_time_range 未设置时，自动计算扩展范围
            if self._is_prediction_indicator_name(
                indicator_config.get("name", ""), indicator_config.get("original_name", "")
            ):
                fallback_extended = self._build_prediction_time_range(params)
                if fallback_extended:
                    time_range = fallback_extended
                    logger.info(
                        f"预测指标 {indicator_config.get('name')} 使用fallback扩展时间范围: {time_range}"
                    )
                else:
                    time_range = params.get("time_range", {})
            else:
                time_range = params.get("time_range", {})

        period = params.get("period", "月")
        is_main = params.get("is_main", 1)

        # 构建SELECT子句
        select_cols = ["index_id", "stat_date", "val", "val_tb", "val_hb", "unit"]

        # 构建WHERE条件
        where_conditions = [
            f"index_id = '{indicator_id}'",
            f"org_id = '{org_id}'",
        ]
        where_conditions.append(f"period = '{period}'")

        # 四率指标需要添加dim_val过滤
        if is_four_rate:
            dim_val = self.table_selector.get_dim_val(is_main)
            where_conditions.append(f"dim_val = '{dim_val}'")

        # 时间范围过滤
        self._append_time_conditions(where_conditions, time_range, period)

        # 普通非关联主指标查询追加 dim_val IS NULL
        if self._should_add_dim_val_is_null(indicator_config):
            where_conditions.append("dim_val IS NULL")

        # 构建完整SQL
        sql = f"""SELECT {', '.join(select_cols)}
FROM {table}
WHERE {' AND '.join(where_conditions)}
ORDER BY stat_date DESC"""

        return sql

    def _generate_weather_sql(
        self,
        indicator_config: Dict[str, Any],
        params: Dict[str, Any]
    ) -> str:
        """生成温度/天气指标的 SQL 查询（统一处理 is_virtual 与 is_weather 两种来源）。

        数据来源：zhss_result_data_tqyb_mx，含省级和地市、日粒度和月粒度数据。

        字段含义：
          dim_value_b → 最高温（℃）
          dim_value_c → 最低温（℃）

        virtual_type 决定 val 取值：
          avg_temp（默认）→ (最高温 + 最低温) / 2
          max_temp        → 最高温
          min_temp        → 最低温

        月粒度行 stat_date 格式为 'YYYY-MM-01'，需用 DATE_FORMAT 做范围比较，
        不能使用 _append_time_conditions（会转换为月末日期导致过滤错误）。
        """
        virtual_type = indicator_config.get("virtual_type", "avg_temp")
        table = indicator_config.get("table", "zhss_result_data_tqyb_mx")
        org_id = indicator_config.get("org_id")
        time_range = params.get("time_range", {})
        period = params.get("period", "月")
        start = time_range.get("start", "")
        end = time_range.get("end", "")
        
        logger.info(f"[温度指标-SQL生成] 虚拟类型={virtual_type}, org_id={org_id}, period={period}, start={start}, end={end}")

        is_province = org_id == PROVINCE_ORG_ID
        city_org_ids = [info["org_id"] for info in CITIES.values()]

        # 气温表只存日粒度数据（period='日'），月度结果需对日数据按月聚合
        if period == "月":
            # 时间范围用 DATE_FORMAT 匹配年月
            time_conds = []
            if start and len(start) == 7:
                time_conds.append(f"DATE_FORMAT(stat_date, '%Y-%m') >= '{start}'")
            if end and len(end) == 7:
                time_conds.append(f"DATE_FORMAT(stat_date, '%Y-%m') <= '{end}'")

            # 省级：IN 九地市；地市：单 org_id
            if is_province:
                city_ids_sql = ", ".join(f"'{oid}'" for oid in city_org_ids)
                org_cond = f"org_id IN ({city_ids_sql})"
            else:
                org_cond = f"org_id = '{org_id}'"

            # AVG 聚合：对月内所有日数据（跨城市/跨日）求均值
            val_expr = {
                "max_temp": "ROUND(AVG(dim_value_b), 1)",
                "min_temp": "ROUND(AVG(dim_value_c), 1)",
                "avg_temp": "ROUND(AVG((dim_value_b + dim_value_c) / 2), 1)",
            }.get(virtual_type, "ROUND(AVG((dim_value_b + dim_value_c) / 2), 1)")

            where_parts = [org_cond, "period = '日'"] + time_conds
            month_fmt = "DATE_FORMAT(stat_date, '%Y-%m')"
            sql = (
                f"SELECT {month_fmt} AS stat_date,\n"
                f"       {val_expr} AS val,\n"
                f"       ROUND(AVG(dim_value_b), 1) AS max_temp,\n"
                f"       ROUND(AVG(dim_value_c), 1) AS min_temp,\n"
                f"       NULL AS val_tb, NULL AS val_hb, '℃' AS unit\n"
                f"FROM {table}\n"
                f"WHERE {' AND '.join(where_parts)}\n"
                f"GROUP BY {month_fmt}\n"
                f"ORDER BY {month_fmt} DESC"
            )
        else:
            # 日粒度：直接查日数据，省级取九地市当日均值
            time_conds = []
            if start:
                time_conds.append(f"stat_date >= '{start}'")
            if end:
                time_conds.append(f"stat_date <= '{end}'")

            if is_province:
                city_ids_sql = ", ".join(f"'{oid}'" for oid in city_org_ids)
                org_cond = f"org_id IN ({city_ids_sql})"
                val_expr = {
                    "max_temp": "ROUND(AVG(dim_value_b), 1)",
                    "min_temp": "ROUND(AVG(dim_value_c), 1)",
                    "avg_temp": "ROUND(AVG((dim_value_b + dim_value_c) / 2), 1)",
                }.get(virtual_type, "ROUND(AVG((dim_value_b + dim_value_c) / 2), 1)")
                where_parts = [org_cond, "period = '日'"] + time_conds
                sql = (
                    f"SELECT stat_date,\n"
                    f"       {val_expr} AS val,\n"
                    f"       ROUND(AVG(dim_value_b), 1) AS max_temp,\n"
                    f"       ROUND(AVG(dim_value_c), 1) AS min_temp,\n"
                    f"       NULL AS val_tb, NULL AS val_hb, '℃' AS unit\n"
                    f"FROM {table}\n"
                    f"WHERE {' AND '.join(where_parts)}\n"
                    f"GROUP BY stat_date\n"
                    f"ORDER BY stat_date DESC"
                )
            else:
                val_expr = {
                    "max_temp": "ROUND(dim_value_b, 1)",
                    "min_temp": "ROUND(dim_value_c, 1)",
                    "avg_temp": "ROUND((dim_value_b + dim_value_c) / 2, 1)",
                }.get(virtual_type, "ROUND((dim_value_b + dim_value_c) / 2, 1)")
                where_parts = [f"org_id = '{org_id}'", "period = '日'"] + time_conds
                sql = (
                    f"SELECT stat_date,\n"
                    f"       {val_expr} AS val,\n"
                    f"       ROUND(dim_value_b, 1) AS max_temp,\n"
                    f"       ROUND(dim_value_c, 1) AS min_temp,\n"
                    f"       NULL AS val_tb, NULL AS val_hb, '℃' AS unit\n"
                    f"FROM {table}\n"
                    f"WHERE {' AND '.join(where_parts)}\n"
                    f"ORDER BY stat_date DESC"
                )

        logger.info(f"[温度指标-SQL执行] 生成的SQL:\n{sql}")
        return sql
    
    def _generate_calendar_sql(
        self,
        indicator_config: Dict[str, Any],
        params: Dict[str, Any]
    ) -> str:
        """生成日历指标的SQL查询（当月天数、节假日天数等）
        
        日历指标无需查询数据库，而是通过计算生成虚拟数据。
        
        virtual_type 决定数据来源：
          day_nums → 当月天数（1-31）
          holiday_days → 当月法定假期天数（含节假日类型标注）
        """
        virtual_type = indicator_config.get("virtual_type", "day_nums")
        time_range = params.get("time_range", {})
        start = time_range.get("start", "")
        end = time_range.get("end", "")
        
        logger.info(f"[日历指标] 生成 {virtual_type} SQL，时间范围: {start} 至 {end}")
        
        # 日历指标不需要真正的SQL查询，而是返回一个虚拟SQL
        # 在execute()中会捕获到 VIRTUAL_ 前缀，走_query_virtual_indicator()路径
        if virtual_type == "day_nums":
            # 返回一个特殊标记，表示这是日历计算
            return f"SELECT 'VIRTUAL_CALENDAR' AS type, '{virtual_type}' AS virtual_type, '{start}' AS period"
        elif virtual_type == "holiday_days":
            return f"SELECT 'VIRTUAL_CALENDAR' AS type, '{virtual_type}' AS virtual_type, '{start}' AS period"
        else:
            return f"SELECT 'VIRTUAL_CALENDAR' AS type, '{virtual_type}' AS virtual_type, '{start}' AS period"

    def _generate_dimension_sql(
        self,
        indicator_config: Dict[str, Any],
        params: Dict[str, Any]
    ) -> str:
        """生成维度指标的SQL查询（支持 base_join、ssx_join 和 direct_dimval）"""
        table = indicator_config.get("table")
        indicator_id = indicator_config.get("id")
        org_id = indicator_config.get("org_id")
        dimension_names = indicator_config.get("matched_dimensions") or []
        if not dimension_names:
            single_dimension = indicator_config.get("dimension_name", "")
            if single_dimension:
                dimension_names = [single_dimension]

        time_range = params.get("time_range", {})
        period = params.get("period", "月")

        # 判断查询模式：如果 dimension_name 是纯数字（如 4001），使用 direct_dimval 模式
        is_direct_dimval = dimension_names and all(name.isdigit() for name in dimension_names)

        if is_direct_dimval:
            # 直接使用 dim_val 过滤，不需要 JOIN（用于发电量等指标）
            where_conditions = [
                f"index_id = '{indicator_id}'",
                f"org_id = '{org_id}'",
                f"period = '{period}'"
            ]

            # 时间范围过滤
            self._append_time_conditions(where_conditions, time_range, period)

            # 维度过滤
            if len(dimension_names) == 1:
                where_conditions.append(f"dim_val = '{dimension_names[0]}'")
            else:
                dim_vals = "', '".join(dimension_names)
                where_conditions.append(f"dim_val IN ('{dim_vals}')")

            sql = f"""SELECT index_id, stat_date, val, val_tb, val_hb, unit, dim_val AS wd_name
FROM {table}
WHERE {' AND '.join(where_conditions)}
ORDER BY stat_date DESC"""
        else:
            # 使用 qlyy_dimension 表 JOIN（用于售电量等指标）
            dimension_query_mode = indicator_config.get("dimension_query_mode") or (
                "ssx_join" if (table or "").endswith("_ssx") else "base_join"
            )

            where_conditions = [
                f"a.index_id = '{indicator_id}'",
                f"a.org_id = '{org_id}'",
                f"a.period = '{period}'"
            ]

            # 时间范围过滤
            self._append_time_conditions(where_conditions, time_range, period, prefix="a.")

            # 维度过滤
            if len(dimension_names) == 1:
                where_conditions.append(f"b.wd_name = '{dimension_names[0]}'")
            elif dimension_names:
                dim_names = "', '".join(dimension_names)
                where_conditions.append(f"b.wd_name IN ('{dim_names}')")

            # one_table=1 的 base_join 需要显式过滤非空维度
            if dimension_query_mode == "base_join":
                where_conditions.append("a.dim_val IS NOT NULL")

            sql = f"""SELECT a.index_id, a.stat_date, a.val, a.val_tb, a.val_hb, a.unit, b.wd_name
FROM {table} a
INNER JOIN qlyy_dimension b ON b.wd_id = a.dim_val
WHERE {' AND '.join(where_conditions)}
ORDER BY a.stat_date DESC"""

        return sql

    def _validate_sql_safety(self, sql: str) -> tuple[bool, Optional[str]]:
        """校验 SQL 安全性，只允许 SELECT 查询

        Returns:
            (is_safe, error_message): 如果安全返回 (True, None)，否则返回 (False, error_message)
        """
        if not sql or not sql.strip():
            return False, "SQL 语句为空"

        # 转换为大写并移除多余空格，便于检查
        sql_upper = sql.strip().upper()

        # 危险操作关键字列表
        dangerous_keywords = [
            'UPDATE', 'DELETE', 'INSERT', 'DROP', 'TRUNCATE',
            'ALTER', 'CREATE', 'REPLACE', 'RENAME', 'GRANT',
            'REVOKE', 'EXECUTE', 'EXEC', 'CALL'
        ]

        # 检查是否包含危险关键字
        for keyword in dangerous_keywords:
            # 使用单词边界检查，避免误判（如 SELECT 中包含 UPDATE 字符串）
            if f' {keyword} ' in f' {sql_upper} ' or sql_upper.startswith(f'{keyword} '):
                error_msg = f"检测到危险 SQL 操作: {keyword}。系统仅允许 SELECT 查询"
                logger.error(f"SQL 安全校验失败: {error_msg}\nSQL: {sql}")
                return False, error_msg

        # 检查是否为 SELECT 语句
        if not sql_upper.startswith('SELECT'):
            error_msg = "仅允许执行 SELECT 查询语句"
            logger.error(f"SQL 安全校验失败: {error_msg}\nSQL: {sql}")
            return False, error_msg

        return True, None

    def _append_time_conditions(
        self,
        where_conditions: List[str],
        time_range: Dict[str, str],
        period: str,
        prefix: str = ""
    ):
        """向 WHERE 条件列表追加时间范围过滤"""
        if not time_range:
            return

        start = time_range.get("start")
        end = time_range.get("end")

        if start and period == "月":
            start_month = self._month_key(start)
            start_date = datetime.strptime(start_month, "%Y-%m")
            start_month_end = start_date + relativedelta(months=1) - relativedelta(days=1)
            start_sql = start_month_end.strftime("%Y-%m-%d")
            where_conditions.append(f"{prefix}stat_date >= '{start_sql}'")

        if end and period == "月":
            end_month = self._month_key(end)
            end_date = datetime.strptime(end_month, "%Y-%m")
            end_month_end = end_date + relativedelta(months=1) - relativedelta(days=1)
            end_sql = end_month_end.strftime("%Y-%m-%d")
            where_conditions.append(f"{prefix}stat_date <= '{end_sql}'")

        if period == "年":
            if start:
                start_year = int(start.split("-")[0]) if "-" in start else int(start)
                where_conditions.append(f"{prefix}stat_date >= '{start_year}-12-31'")
            if end:
                end_year = int(end.split("-")[0]) if "-" in end else int(end)
                where_conditions.append(f"{prefix}stat_date <= '{end_year}-12-31'")

        if period == "日":
            if start:
                where_conditions.append(f"{prefix}stat_date >= '{start}'")
            if end:
                where_conditions.append(f"{prefix}stat_date <= '{end}'")

    def _build_query_execution_result(
        self,
        query_info: Dict[str, Any],
        df: pd.DataFrame,
        *,
        sql: str,
        original_sql: str,
        execution_status: str,
        error: Optional[str] = None,
        missing_table: bool = False,
        fallback_attempted: bool = False,
        fallback_used: bool = False,
        fallback_sql: Optional[str] = None,
        fallback_error: Optional[str] = None,
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {
            "indicator": query_info['indicator'],
            "indicator_id": query_info['indicator_id'],
            "data": df,
            "row_count": len(df),
            "sql": sql,
            "actual_sql": sql,
            "original_sql": original_sql,
            "execution_status": execution_status,
            "missing_table": missing_table,
            "fallback_attempted": fallback_attempted,
            "fallback_used": fallback_used,
            "role": query_info.get('role', 'main'),
            "is_main": query_info.get('role', 'main') == 'main',
            "is_dimension": query_info.get('is_dimension', False),
            "is_city_level": query_info.get('is_city_level', False),
            "parent_indicator": query_info.get('parent_indicator', ''),
            "dimension_group_key": query_info.get('dimension_group_key', ''),
            "dimension_group_name": query_info.get('dimension_group_name', ''),
            "dimension_type": query_info.get('dimension_type', ''),
            "overview_scope": query_info.get('overview_scope', ''),
        }
        if missing_table and not error and not warning:
            if query_info.get('role', 'main') == 'main':
                error = "主指标数据表不存在"
            else:
                warning = "查询表不存在，已按空结果继续"
        if error:
            result["error"] = error
        if warning:
            result["warning"] = warning
        if fallback_sql:
            result["fallback_sql"] = fallback_sql
        if fallback_error:
            result["fallback_error"] = fallback_error
        return result

    def _build_executed_sql_queries(self, query_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        executed_queries = []
        for idx, result in enumerate(query_results, 1):
            sql = result.get('sql') or result.get('actual_sql') or ''
            executed_queries.append({
                "query_id": idx,
                "indicator": result.get('indicator', ''),
                "indicator_id": result.get('indicator_id'),
                "description": result.get('description') or f"查询{result.get('indicator', '')}数据",
                "sql": sql,
                "actual_sql": result.get('actual_sql') or sql,
                "original_sql": result.get('original_sql'),
                "execution_status": result.get('execution_status'),
                "status": result.get('execution_status'),
                "row_count": result.get('row_count', 0),
                "error": result.get('error'),
                "warning": result.get('warning'),
                "missing_table": result.get('missing_table', False),
                "fallback_attempted": result.get('fallback_attempted', False),
                "fallback_used": result.get('fallback_used', False),
                "fallback_sql": result.get('fallback_sql'),
                "fallback_error": result.get('fallback_error'),
                "role": result.get('role', 'main'),
                "is_dimension": result.get('is_dimension', False),
                "is_city_level": result.get('is_city_level', False),
                "overview_scope": result.get('overview_scope', ''),
            })
        return executed_queries

    def _get_execution_status(self, df: pd.DataFrame, missing_table: bool, error: Optional[str] = None) -> str:
        if missing_table:
            return "missing_table"
        if error:
            return "error"
        if df.empty:
            return "empty"
        return "success"

    def _execute_queries(self, sql_queries: List[Dict[str, Any]], progress_callback: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        """执行SQL查询，维度查询无结果时自动回退到层级表

        Args:
            sql_queries: SQL查询列表
            progress_callback: 可选的进度回调函数，用于报告查询进度
        """
        results = []
        total_queries = len(sql_queries)

        for idx, query_info in enumerate(sql_queries, 1):
            indicator_name = query_info['indicator']
            original_sql = query_info['sql']
            current_sql = original_sql
            final_sql = original_sql
            fallback_attempted = False
            fallback_used = False
            fallback_sql = None
            fallback_error = None
            execution_error = None
            execution_status_override = None
            missing_table = False

            # 报告查询进度
            if progress_callback:
                progress_callback(f"正在查询 {indicator_name} ({idx}/{total_queries})...")

            try:
                # SQL 安全校验：只允许 SELECT 查询
                is_safe, error_msg = self._validate_sql_safety(original_sql)
                if not is_safe:
                    error = f"SQL 安全校验失败: {error_msg}"
                    logger.error(f"SQL 安全校验失败 - 指标: {query_info['indicator']}, 错误: {error_msg}")
                    if progress_callback:
                        progress_callback(f"{indicator_name} SQL安全校验失败: {error_msg}")
                    results.append(self._build_query_execution_result(
                        query_info,
                        pd.DataFrame(),
                        sql=original_sql,
                        original_sql=original_sql,
                        execution_status="safety_failed",
                        error=error,
                    ))
                    continue

                # 检查是否是日历虚拟指标
                if original_sql.startswith("SELECT 'VIRTUAL_CALENDAR'"):
                    logger.info(f"[日历指标] 检测到日历虚拟指标: {query_info['indicator']}")
                    df = self._generate_calendar_data(query_info)
                else:
                    logger.info(f"[SQL] {query_info['indicator']}:\n{original_sql}")
                    rows = self.db.find_all(original_sql)
                    df = pd.DataFrame(rows)
                    missing_table = bool(getattr(self.db, 'last_query_missing_table', False))

                # 维度查询容错（A2/B2）：默认不回退，避免用父指标数据冒充维度指标
                if df.empty and query_info.get('fallback_config') and query_info.get('is_dimension') and query_info.get('allow_dimension_fallback', False):
                    fallback_config = query_info['fallback_config']
                    parent_indicator = fallback_config.get('parent_indicator', '')
                    logger.warning(
                        f"维度查询 {query_info['indicator']} (dims={fallback_config.get('matched_dimensions', [])}) 无结果，"
                        f"回退到父指标 {parent_indicator} 的层级表查询"
                    )
                    if progress_callback:
                        progress_callback(f"{indicator_name} 维度查询无结果，尝试回退查询...")

                    # 构建回退查询：使用父指标的 index_id + 按 org_type 选层级表
                    org_type = fallback_config.get('org_type', 3001)
                    parent_name = fallback_config.get('parent_indicator', '') or query_info['indicator']
                    fallback_table = fallback_config.get('fallback_table', '')

                    if not fallback_table:
                        parent_index_id = fallback_config.get('id')
                        parent_indicator_info = self.indicator_matcher.get_indicator_info(parent_index_id) if parent_index_id else None
                        fallback_table = self.table_selector.get_table_decision(
                            indicator_info=parent_indicator_info,
                            org_type=org_type,
                            user_query="",
                            indicator_name=parent_name
                        ).get("table_name", "")

                    if fallback_table:
                        original_params = query_info.get('params', {"time_range": {}, "period": "月"})
                        fallback_sql = self._generate_sql(
                            {
                                "table": fallback_table,
                                "id": fallback_config['id'],
                                "org_id": fallback_config['org_id'],
                                "org_type": org_type,
                                "is_four_rate": False,
                                "role": "main",
                            },
                            original_params
                        )
                        fallback_attempted = True
                        current_sql = fallback_sql
                        final_sql = fallback_sql
                        missing_table = False
                        # 回退查询也需要进行安全校验
                        is_fallback_safe, fallback_error = self._validate_sql_safety(fallback_sql)
                        if is_fallback_safe:
                            logger.info(f"[SQL][维度回退] {query_info['indicator']}:\n{fallback_sql}")
                            fallback_rows = self.db.find_all(fallback_sql)
                            fallback_df = pd.DataFrame(fallback_rows)
                            missing_table = bool(getattr(self.db, 'last_query_missing_table', False))
                            if not fallback_df.empty:
                                df = fallback_df
                                fallback_used = True
                                missing_table = False
                                logger.info(f"维度回退查询成功: {query_info['indicator']} -> {fallback_table}, {len(df)} 行")
                                if progress_callback:
                                    progress_callback(f"✓ {indicator_name} 回退查询成功，获得 {len(df)} 条数据")
                            else:
                                df = fallback_df
                                if progress_callback:
                                    progress_callback(f"{indicator_name} 回退查询未获得数据")
                        else:
                            execution_status_override = "safety_failed"
                            execution_error = f"回退查询 SQL 安全校验失败: {fallback_error}"
                            df = pd.DataFrame()
                            logger.error(f"回退查询 SQL 安全校验失败: {fallback_error}")
                            if progress_callback:
                                progress_callback(f"{indicator_name} 回退SQL安全校验失败: {fallback_error}")
                    else:
                        fallback_attempted = True
                        fallback_error = "未找到可用回退表"
                        if progress_callback:
                            progress_callback(f"{indicator_name} 未找到可用回退表")

                if (
                    df.empty
                    and query_info.get('allow_overview_dimension_fallback')
                    and query_info.get('overview_dimension_fallback_config')
                    and query_info.get('overview_scope') == 'multi_main_metric_chapter2'
                ):
                    fallback_config = query_info['overview_dimension_fallback_config']
                    original_params = query_info.get('params', {"time_range": {}, "period": "月"})
                    fallback_sql = self._generate_sql(fallback_config, original_params)
                    fallback_attempted = True
                    current_sql = fallback_sql
                    final_sql = fallback_sql
                    missing_table = False
                    if progress_callback:
                        progress_callback(f"{indicator_name} 总体表无数据，尝试维度表回退查询...")
                    is_fallback_safe, fallback_error = self._validate_sql_safety(fallback_sql)
                    if is_fallback_safe:
                        logger.info(f"[SQL][指标总体情况回退] {query_info['indicator']}:\n{fallback_sql}")
                        fallback_rows = self.db.find_all(fallback_sql)
                        fallback_df = pd.DataFrame(fallback_rows)
                        missing_table = bool(getattr(self.db, 'last_query_missing_table', False))
                        if not fallback_df.empty:
                            df = fallback_df
                            fallback_used = True
                            missing_table = False
                            if progress_callback:
                                progress_callback(f"✓ {indicator_name} 指标总体情况回退查询成功，获得 {len(df)} 条数据")
                        else:
                            df = fallback_df
                            if progress_callback:
                                progress_callback(f"{indicator_name} 指标总体情况回退查询未获得数据")
                    else:
                        execution_status_override = "safety_failed"
                        execution_error = f"指标总体情况回退查询 SQL 安全校验失败: {fallback_error}"
                        df = pd.DataFrame()
                        logger.error(f"指标总体情况回退查询 SQL 安全校验失败: {fallback_error}")
                        if progress_callback:
                            progress_callback(f"{indicator_name} 指标总体情况回退SQL安全校验失败: {fallback_error}")

                # Fix: 维度回退（B2→B1）后若 B1 也因 MySQL 分区缺失返回 missing_table=True，
                # 降级为 empty 状态。层级表（zhss_result_data_qs/ds/qx/gds）本身始终存在，
                # missing_table=True 此时意味着该时间段分区缺失（无数据），而非表不存在，
                # 不应将 error 推给前端（前端收到 error 非 null 会立即终止响应）。
                if fallback_attempted and not fallback_used and missing_table and df.empty:
                    logger.warning(
                        f"[维度回退] {query_info['indicator']}: B2→B1 回退后 B1 仍返回 missing_table=True，"
                        f"降级为 empty（层级表存在，但该时间段分区/数据缺失）"
                    )
                    missing_table = False

                execution_status = execution_status_override or self._get_execution_status(df, missing_table, execution_error)
                results.append(self._build_query_execution_result(
                    query_info,
                    df,
                    sql=final_sql,
                    original_sql=original_sql,
                    execution_status=execution_status,
                    error=execution_error,
                    missing_table=missing_table,
                    fallback_attempted=fallback_attempted,
                    fallback_used=fallback_used,
                    fallback_sql=fallback_sql,
                    fallback_error=fallback_error,
                ))

                # 报告查询完成
                if progress_callback:
                    progress_callback(f"✓ {indicator_name} 查询完成，获得 {len(df)} 条数据")

                logger.info(f"查询 {query_info['indicator']} (role={query_info.get('role', 'main')}): {len(df)} 行数据")
            except Exception as e:
                missing_table = bool(getattr(e, 'handled_as_empty_result', False)) or _is_missing_table_error(e)
                is_main_query = query_info.get('role', 'main') == 'main'
                should_treat_as_warning = missing_table and not is_main_query
                if should_treat_as_warning:
                    logger.warning(f"查询表不存在 {query_info['indicator']}: {e}")
                else:
                    logger.error(f"查询失败 {query_info['indicator']}: {e}")

                # 报告查询失败
                if progress_callback:
                    if should_treat_as_warning:
                        progress_callback(f"{indicator_name} 查询表不存在，已按空结果继续")
                    else:
                        progress_callback(f"{indicator_name} 查询失败: {str(e)[:50]}")

                results.append(self._build_query_execution_result(
                    query_info,
                    pd.DataFrame(),
                    sql=current_sql,
                    original_sql=original_sql,
                    execution_status="missing_table" if missing_table else "error",
                    error=None if should_treat_as_warning else str(e),
                    warning=str(e) if should_treat_as_warning else None,
                    missing_table=missing_table,
                    fallback_attempted=fallback_attempted,
                    fallback_used=fallback_used,
                    fallback_sql=fallback_sql,
                    fallback_error=fallback_error,
                ))

        return results
    
    def _generate_calendar_data(self, query_info: Dict[str, Any]) -> pd.DataFrame:
        """生成日历虚拟指标数据
        
        Args:
            query_info: 查询信息，包含 sql、indicator、params 等
            
        Returns:
            DataFrame，包含虚拟指标数据
        """
        try:
            # 从查询信息中提取虚拟类型和时间范围
            indicator_name = query_info['indicator']
            params = query_info.get('params', {})
            time_range = params.get('time_range', {})
            
            # 从query_info中获取虚拟指标类型
            virtual_type = query_info.get('virtual_type', 'day_nums')
            
            start = time_range.get('start', '')
            end = time_range.get('end', '')
            
            if not start or not end:
                logger.warning(f"[日历] 日期范围不完整: start={start}, end={end}")
                return pd.DataFrame()
            
            # 生成日期范围内的所有月份
            start_date = datetime.strptime(start, "%Y-%m")
            end_date = datetime.strptime(end, "%Y-%m")
            
            records = []
            current = start_date
            
            while current <= end_date:
                year_month = current.strftime("%Y-%m")
                
                try:
                    if virtual_type == "day_nums":
                        # 获取当月天数
                        val = CalendarHelper.get_month_days(year_month)
                        records.append({
                            'stat_date': f"{year_month}-01",
                            'val': val,
                            'val_tb': None,
                            'val_hb': None,
                            'unit': '天'
                        })
                        logger.info(f"[日历] {year_month}: 当月天数 = {val}")
                    
                    elif virtual_type == "holiday_days":
                        # 获取法定假期天数和详细信息
                        val = CalendarHelper.get_holiday_days(year_month)
                        holiday_details = CalendarHelper.get_holiday_details(year_month)
                        
                        # 构建假期详情字符串（包含假期类型标注）
                        holiday_info = "；".join([
                            f"{h['type']}({h['day_of_week']})"
                            for h in holiday_details
                        ]) if holiday_details else "无假期"
                        
                        records.append({
                            'stat_date': f"{year_month}-01",
                            'val': val,
                            'val_tb': None,
                            'val_hb': None,
                            'unit': '天',
                            'holiday_info': holiday_info
                        })
                        logger.info(f"[日历] {year_month}: 假期天数 = {val}, 假期类型: {holiday_info}")
                
                except ValueError as e:
                    logger.warning(f"[日历] 计算 {year_month} 的日期数据失败: {e}")
                
                # 移动到下一个月
                current = current + relativedelta(months=1)
            
            df = pd.DataFrame(records)
            logger.info(f"[日历] {indicator_name} 生成 {len(df)} 条记录")
            return df
        
        except Exception as e:
            logger.warning(f"[日历] 生成日历数据失败: {e}", exc_info=True)
            return pd.DataFrame()
