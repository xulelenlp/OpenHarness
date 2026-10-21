"""业务分析场景模板（集中定义，供大纲生成与技能提示使用）。

场景不是硬编码的 Agent 路由：它只在用户明确要做某类业务分析时，
给 ``db_query(outline)`` 提供指标/维度/必做分析/报告段落的骨架。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalysisScenario:
    key: str
    name: str
    description: str
    keywords: tuple[str, ...] = ()
    scene_names: tuple[str, ...] = ()
    indicator_keywords: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    required_analyses: tuple[str, ...] = ()
    external_sources: tuple[str, ...] = ()
    report_sections: tuple[str, ...] = ()
    default_org: str = "福建"
    default_period: str = "月"

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "keywords": list(self.keywords),
            "scene_names": list(self.scene_names),
            "indicator_keywords": list(self.indicator_keywords),
            "dimensions": list(self.dimensions),
            "required_analyses": list(self.required_analyses),
            "external_sources": list(self.external_sources),
            "report_sections": list(self.report_sections),
            "default_org": self.default_org,
            "default_period": self.default_period,
        }


SCENARIOS: dict[str, AnalysisScenario] = {
    "business": AnalysisScenario(
        key="business",
        name="经营综合分析",
        description="围绕营业收入、利润、售电量、电价、电费回收等核心经营指标做月度/专题分析。",
        keywords=("经营", "经营情况", "经营分析", "综合", "月报", "经营质效"),
        scene_names=("数说经营", "经营分析"),
        indicator_keywords=("营业收入", "利润总额", "售电量", "代理购电价格", "平均到户用电价格", "电费回收率", "预收电费"),
        dimensions=("时间", "地市", "产业"),
        required_analyses=("目标完成度", "同比环比", "结构变化", "地市对比", "异常识别"),
        external_sources=("政策", "企业事件", "能源价格"),
        report_sections=("摘要", "总体经营情况", "售电量与电价", "电费回收", "问题诊断", "成因分析", "建议"),
    ),
    "sales": AnalysisScenario(
        key="sales",
        name="售电量与市场分析",
        description="分析售电量趋势、区域分化、产业/行业结构、气温负荷与政策事件影响。",
        keywords=("售电量", "售电", "电量", "市场", "用电量", "产业", "行业"),
        scene_names=("数说经营", "数说服务"),
        indicator_keywords=("售电量", "全社会用电量", "分产业售电量", "业扩净增容量", "最高用电负荷", "发电量"),
        dimensions=("时间", "地市", "产业", "行业", "客户类型", "气温"),
        required_analyses=("趋势", "同比环比", "九地市对比", "产业/行业贡献度", "目标完成", "气温/负荷相关性", "政策/企业事件"),
        external_sources=("新能源政策", "电价政策", "地方重大企业", "气温"),
        report_sections=("摘要", "总体售电情况", "时间趋势", "区域对比", "产业/行业结构", "问题诊断", "成因分析", "趋势与建议"),
    ),
    "recovery": AnalysisScenario(
        key="recovery",
        name="电费回收与欠费风险",
        description="分析电费回收率、应收余额、欠费结构、大额欠费客户与预收电费风险。",
        keywords=("电费", "回收", "回收率", "欠费", "应收", "预收", "大额欠费"),
        scene_names=("数说经营", "数说服务"),
        indicator_keywords=("电费回收率", "应收电费余额", "电费欠费", "大额欠费客户", "预收电费", "当月电费到账金额"),
        dimensions=("时间", "地市", "客户类型", "欠费账龄"),
        required_analyses=("回收率趋势", "目标缺口", "欠费结构", "风险客户清单", "预收/回收联动", "政策影响"),
        external_sources=("电价政策", "地方企业事件", "经济环境"),
        report_sections=("摘要", "回收总体情况", "欠费结构", "风险客户", "成因分析", "催收建议", "数据质量"),
    ),
    "service": AnalysisScenario(
        key="service",
        name="供电服务与客户体验",
        description="分析投诉/抱怨、工单、抢修、业扩、低电压/频繁停电等服务指标。",
        keywords=("服务", "投诉", "抱怨", "工单", "抢修", "业扩", "低电压", "停电", "客户体验"),
        scene_names=("数说服务", "数说电网"),
        indicator_keywords=("百万客户投诉", "意见工单数量", "全渠道客户投诉", "重复诉求数量", "故障抢修平均时长", "高压业扩办电时长", "低电压客户数", "频繁停电客户数"),
        dimensions=("时间", "地市", "业务类型", "客户类型"),
        required_analyses=("热点", "重复诉求", "区域/环节对比", "时长/数量趋势", "根因分析", "整改建议"),
        external_sources=("服务政策", "地方事件", "天气"),
        report_sections=("摘要", "服务总体情况", "投诉/工单热点", "抢修/业扩效率", "问题诊断", "根因与建议"),
    ),
    "grid": AnalysisScenario(
        key="grid",
        name="电网运行与可靠性",
        description="分析供电可靠率、停电、线损、电压质量、重过载等运行指标。",
        keywords=("电网", "运行", "可靠性", "线损", "停电", "电压", "重过载", "过载", "配变"),
        scene_names=("数说电网", "配电运维"),
        indicator_keywords=("供电可靠率", "用户平均停电时长", "用户平均停电次数", "综合线损率", "综合电压合格率", "重载配变数", "过载配变数", "低电压配变数"),
        dimensions=("时间", "地市", "设备/线路", "电压等级"),
        required_analyses=("可靠性趋势", "停电归因", "线损异常", "设备风险", "区域对比"),
        external_sources=("天气", "电网政策", "地方事件"),
        report_sections=("摘要", "运行总体情况", "可靠性", "线损/电压", "设备风险", "问题诊断", "建议"),
    ),
    "asset": AnalysisScenario(
        key="asset",
        name="资产投资与转资",
        description="分析资产总额、固定资产投资、预算完成、转资完成与资金进度。",
        keywords=("资产", "投资", "转资", "预算", "固定资产", "资金"),
        scene_names=("数说资产",),
        indicator_keywords=("资产总额", "固定资产投资", "资本性项目预算完成率", "资本性项目转资完成率", "资金支出金额", "资金进账金额"),
        dimensions=("时间", "单位", "项目类型"),
        required_analyses=("进度", "预算偏差", "结构", "风险", "建议"),
        external_sources=("投资政策", "地方重大项目"),
        report_sections=("摘要", "资产总体情况", "投资进度", "转资/预算", "风险与建议"),
    ),
    "new_energy": AnalysisScenario(
        key="new_energy",
        name="新能源与政策影响",
        description="分析新能源装机/发电/利用率、电价市场化、绿电交易与政策影响。",
        keywords=("新能源", "光伏", "风电", "绿电", "电价", "市场化", "政策"),
        scene_names=("数说经营", "能源价格专题"),
        indicator_keywords=("新能源发电量", "全口径发电装机容量", "代理购电价格", "市场化直接交易购电价格", "发电量"),
        dimensions=("时间", "地市", "能源类型"),
        required_analyses=("政策时间线", "影响路径", "数据验证", "情景分析"),
        external_sources=("新能源政策", "电价政策", "地方企业"),
        report_sections=("摘要", "政策背景", "新能源发展", "电价/市场", "影响分析", "建议"),
    ),
    "region": AnalysisScenario(
        key="region",
        name="区域/企业专题",
        description="围绕某地市、园区或重大企业事件，分析电量、业扩、欠费与产业影响。",
        keywords=("区域", "地市", "园区", "企业", "专题", "事件"),
        scene_names=("数说经营", "数说服务"),
        indicator_keywords=("售电量", "业扩净增容量", "电费欠费", "大额欠费客户", "分产业售电量"),
        dimensions=("时间", "企业", "行业", "区域"),
        required_analyses=("事件时间线", "贡献度", "敏感性", "对比"),
        external_sources=("地方重大企业", "地方政策", "新闻事件"),
        report_sections=("摘要", "区域/企业概况", "事件时间线", "数据影响", "结论与建议"),
    ),
    "forecast": AnalysisScenario(
        key="forecast",
        name="预测与目标管理",
        description="分析预测指标、目标完成、趋势外推与情景。",
        keywords=("预测", "预估", "目标", "完成率", "趋势", "展望"),
        scene_names=("数说经营",),
        indicator_keywords=("售电量预测", "预估售电量", "营业收入", "利润总额", "售电量"),
        dimensions=("时间", "地市"),
        required_analyses=("预测偏差", "目标缺口", "情景", "建议"),
        external_sources=("政策", "经济环境", "天气"),
        report_sections=("摘要", "目标完成", "预测与偏差", "情景分析", "建议"),
    ),
    "custom": AnalysisScenario(
        key="custom",
        name="自定义数据 + 业务库",
        description="结合用户上传的 Excel/Word/CSV 与业务库指标做口径对齐和差异分析。",
        keywords=("上传", "附件", "表格", "自定义", "对照", "差异"),
        scene_names=(),
        indicator_keywords=(),
        dimensions=("用户指定",),
        required_analyses=("口径对齐", "数据清洗", "差异分析", "业务解释"),
        external_sources=("用户附件", "业务库", "政策"),
        report_sections=("摘要", "数据与口径", "差异分析", "业务解释", "建议", "附录"),
    ),
}


def get_scenario(key: str) -> AnalysisScenario:
    return SCENARIOS.get(str(key or "").strip(), SCENARIOS["business"])


def match_scenario(text: str) -> AnalysisScenario:
    """按关键词粗匹配；匹配不到返回 business（不阻断）。"""
    value = str(text or "")
    best: tuple[int, AnalysisScenario] | None = None
    for scenario in SCENARIOS.values():
        score = sum(1 for word in scenario.keywords if word and word in value)
        if score and (best is None or score > best[0]):
            best = (score, scenario)
    return best[1] if best else SCENARIOS["business"]


__all__ = ["SCENARIOS", "AnalysisScenario", "get_scenario", "match_scenario"]
