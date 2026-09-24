from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.services.source_registry import detect_industry_plugin, source_plan_for


CATEGORY_LABELS = {
    "macro": "宏观环境及行业分析",
    "operations": "企业业务运营分析",
    "finance": "企业财务状况分析",
    "legal": "法律诉讼风险分析",
    "brand": "品牌舆情分析",
}

REPORT_TYPES = {
    "macro": "macro_environment_report",
    "operations": "business_operations_report",
    "finance": "financial_health_report",
    "legal": "legal_risk_report",
    "brand": "brand_sentiment_report",
}

SEVERITY_HIGH = {"important", "high", "critical", "重要", "高", "严重"}
SENTIMENT_NEGATIVE = {"negative", "负面", "消极"}
SENTIMENT_POSITIVE = {"positive", "正面", "积极"}

DEFAULT_NEXT_ACTIONS = {
    "macro": [
        "补充行业市场规模、竞争格局和技术路线的权威数据，并与宏观指标建立影响路径。",
        "按地区、业务线和报告期跟踪政策变化，避免用全国指标直接替代企业暴露。",
    ],
    "operations": [
        "按行业模板接入经营指标；内部指标与公开披露必须分层展示。",
        "接入同口径同行数据并记录口径、报告期和来源，形成可审计对标。",
    ],
    "finance": [
        "优先使用公司公告、年报和监管披露，聚合数据仅作为交叉校验。",
        "年度与季度累计口径分开比较，并对关键结论保留页码和原文证据。",
    ],
    "legal": [
        "补充案件编号、阶段、金额、决定机关和原文链接，区分未检出与未接入。",
        "将监管处罚、诉讼和经营影响映射到可执行的整改责任与期限。",
    ],
    "brand": [
        "接入合规的新闻、投诉和社交数据，并保留媒体类型、发布时间和传播指标。",
        "将媒体/KOL权重与样本量分开呈现，避免少量样本被解释为整体舆情。",
    ],
}

INDUSTRY_METRIC_LABELS = {
    "pipeline_count": "研发管线数量",
    "approval_count": "获批项目数量",
    "recall_count": "召回事件",
    "medical_insurance_exposure": "医保及集采暴露",
    "contracted_sales": "合同销售额",
    "land_reserve": "土地储备",
    "completion": "项目竣工交付",
    "cash_collection": "销售回款率",
    "debt_maturity": "债务到期压力",
    "monthly_active_users": "月活跃用户",
    "paying_users": "付费用户",
    "service_availability": "服务可用率",
    "data_security_events": "数据安全事件",
    "output": "产量/发电量",
    "utilization_hours": "设备利用小时",
    "installed_capacity": "装机容量",
    "energy_price": "能源价格",
    "emissions": "排放指标",
}


@dataclass
class CompanySnapshot:
    id: Any
    name: str
    industry: str
    region: str
    official_website: str
    profile: dict[str, Any]


@dataclass
class EventSnapshot:
    id: Any
    category: str
    severity: str
    title: str
    content: str
    source_url: str
    source_name: str
    occurred_at: datetime | None
    created_at: datetime | None
    sentiment: str
    extra: dict[str, Any]


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _rows(db: Session, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        result = db.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]
    except SQLAlchemyError:
        return []


def _one(db: Session, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = _rows(db, sql, params)
    return rows[0] if rows else {}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.replace(",", "").replace("%", "").strip()
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _company(
    db: Session,
    *,
    company_id: Any | None,
    company_name: str | None,
) -> CompanySnapshot:
    if company_id is not None:
        company_key = str(company_id).replace("-", "")
        row = _one(
            db,
            "SELECT * FROM companies WHERE REPLACE(CAST(id AS TEXT), '-', '') = :company_key LIMIT 1",
            {"company_key": company_key},
        )
    else:
        row = _one(db, "SELECT * FROM companies WHERE name = :company_name LIMIT 1", {"company_name": company_name or ""})
    if not row:
        raise ValueError("未找到目标公司，请先创建 companies 记录。")
    return CompanySnapshot(
        id=row.get("id"),
        name=str(row.get("name") or company_name or "目标公司"),
        industry=str(row.get("industry") or ""),
        region=str(row.get("region") or ""),
        official_website=str(row.get("official_website") or ""),
        profile=_json(row.get("company_profile"), {}),
    )


# 投后监测规则用中文分类（monitoring_rules.category），本模块历史上用英文维度键（legal/operations/...）
# 直接按 risk_events.category 做等值匹配永远查不到数据。改为复用最近一次已完成分析的规则命中结果
# （rule_evaluations.evidence），保证与「投后监测」页面展示的证据保持一致。
CATEGORY_RULE_MAP: dict[str, tuple[str, ...]] = {
    "operations": ("业务及经营风险",),
    "finance": ("财务风险",),
    "legal": ("法律风险", "公司治理风险", "内控风险", "其他风险"),
    "brand": ("品牌舆情风险",),
}


def _events(db: Session, company_id: Any, category: str, limit: int) -> list[EventSnapshot]:
    rule_categories = CATEGORY_RULE_MAP.get(category)
    if not rule_categories:
        return []
    placeholders = ", ".join(f"'{value}'" for value in rule_categories)
    rows = _rows(
        db,
        f"""
        SELECT re.evidence AS evidence, re.severity AS severity, re.created_at AS created_at
        FROM rule_evaluations re
        JOIN monitoring_rules mr ON mr.id = re.rule_id
        WHERE re.analysis_run_id = (
            SELECT id FROM analysis_runs
            WHERE company_id = :company_id AND status = 'completed'
            ORDER BY created_at DESC
            LIMIT 1
        )
        AND re.status = 'hit'
        AND mr.category IN ({placeholders})
        ORDER BY re.created_at DESC
        """,
        {"company_id": company_id},
    )
    seen: dict[str, EventSnapshot] = {}
    for row in rows:
        severity = str(row.get("severity") or "一般")
        fallback_dt = _dt(row.get("created_at"))
        for item in _json(row.get("evidence"), []) or []:
            if not isinstance(item, dict):
                continue
            event_id = str(item.get("id") or item.get("title") or "")
            if not event_id or event_id in seen:
                continue
            occurred = _dt(item.get("occurred_at")) or fallback_dt
            seen[event_id] = EventSnapshot(
                id=event_id,
                category=category,
                severity=severity,
                title=str(item.get("title") or ""),
                content=str(item.get("content") or ""),
                source_url=str(item.get("source_url") or ""),
                source_name=str(item.get("source_name") or "未标注来源"),
                occurred_at=occurred,
                created_at=occurred,
                sentiment="neutral",
                extra={},
            )
    events = sorted(
        seen.values(),
        key=lambda event: event.occurred_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return events[:limit]


def _industry_template(company: CompanySnapshot) -> str:
    akshare = company.profile.get("akshare_profile") or {}
    individual = akshare.get("individual_info") or {}
    text_value = " ".join(
        str(value)
        for value in (
            company.industry,
            company.name,
            individual.get("行业"),
            individual.get("industry"),
        )
        if value
    )
    if any(keyword in text_value for keyword in ("银行", "货币金融", "商业银行")):
        return "banking"
    if any(keyword in text_value for keyword in ("保险", "证券", "基金", "金融")):
        return "financial_services"
    return detect_industry_plugin(
        company.industry,
        company.name,
        company.profile,
    ).code


def _period_label(period: str) -> str:
    if len(period) == 8 and period.isdigit():
        quarter = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(period[4:6])
        if quarter:
            return f"{period[:4]}{quarter}"
    return period


def _metric_row(rows: list[dict[str, Any]], *names: str) -> dict[str, Any]:
    for row in rows:
        metric_name = str(row.get("指标") or row.get("项目") or row.get("metric_name") or "")
        if any(name == metric_name or name in metric_name for name in names):
            return row
    return {}


def _period_values(row: dict[str, Any], limit: int = 8) -> list[tuple[str, float]]:
    values = []
    for key, value in row.items():
        period = "".join(character for character in str(key) if character.isdigit())
        number = _number(value)
        if len(period) == 8 and number is not None:
            values.append((period, number))
    return sorted(values, key=lambda item: item[0])[-limit:]


def _growth(row: dict[str, Any]) -> float | None:
    values = _period_values(row, 100)
    if not values:
        return None
    period, latest = values[-1]
    previous = _number(row.get(f"{int(period[:4]) - 1}{period[4:]}"))
    if previous in (None, 0):
        return None
    return round((latest / previous - 1) * 100, 1)


def _latest_indicator(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: str(row.get("日期") or row.get("报告期") or ""), default={})


def _indicator_value(
    abstract: list[dict[str, Any]],
    indicator: dict[str, Any],
    *names: str,
) -> float | None:
    for name in names:
        for key, value in indicator.items():
            if name in str(key) and (number := _number(value)) is not None:
                return number
    row = _metric_row(abstract, *names)
    values = _period_values(row, 1)
    return values[-1][1] if values else None


def _metric(
    key: str,
    label: str,
    value: Any,
    *,
    unit: str = "",
    description: str = "",
    tone: str = "neutral",
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": "待接入" if value is None else str(value),
        "unit": unit,
        "description": description,
        "tone": tone,
        "source_ids": source_ids or [],
    }


def _event_source(event: EventSnapshot, index: int) -> dict[str, Any]:
    return {
        "id": f"event-{index + 1}",
        "title": event.title,
        "source_name": event.source_name,
        "source_url": event.source_url,
        "source_tag": event.extra.get("source_authority_label") or "结构化事件",
        "source_code": event.extra.get("source_code") or "risk_event",
        "page_hint": str(event.extra.get("page_hint") or event.extra.get("pdf_page") or ""),
        "published_at": event.occurred_at or event.created_at,
        "severity": event.severity,
        "sentiment": event.sentiment,
    }


def _timeline(events: list[EventSnapshot]) -> list[dict[str, Any]]:
    return [
        {
            "date": (event.occurred_at or event.created_at or datetime.now(timezone.utc)).date().isoformat(),
            "title": event.title,
            "detail": event.content,
            "severity": event.severity,
            "sentiment": event.sentiment,
            "source_id": f"event-{index + 1}",
        }
        for index, event in enumerate(events)
    ]


def _profile_context(company: CompanySnapshot) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], datetime | None]:
    profile = company.profile.get("akshare_profile") or {}
    abstract = profile.get("financial_abstract") or []
    indicators = profile.get("financial_indicators") or []
    return profile, abstract, indicators, _dt(profile.get("updated_at"))


def _finance(company: CompanySnapshot, events: list[EventSnapshot], template: str) -> dict[str, Any]:
    profile, abstract, indicators, profile_at = _profile_context(company)
    indicator = _latest_indicator(indicators)
    revenue_row = _metric_row(abstract, "营业总收入", "营业收入")
    profit_row = _metric_row(abstract, "归母净利润", "净利润")
    revenue_values = _period_values(revenue_row)
    profit_values = _period_values(profit_row)
    latest_revenue = revenue_values[-1][1] if revenue_values else None
    latest_profit = profit_values[-1][1] if profit_values else None
    latest_period = _period_label(revenue_values[-1][0]) if revenue_values else "最新报告期"
    financial_source = ["akshare-financial"] if abstract or indicators else []
    sources = [_event_source(event, index) for index, event in enumerate(events)]
    if financial_source:
        sources.insert(0, {
            "id": "akshare-financial",
            "title": f"{company.name} 财务摘要与指标",
            "source_name": "AkShare（公开数据聚合，需与公司公告交叉校验）",
            "source_url": "https://www.cninfo.com.cn/new/index",
            "source_tag": "聚合财务数据",
            "source_code": "akshare_finance",
            "page_hint": "financial_abstract / financial_indicators",
            "published_at": profile_at,
        })

    metrics = [
        _metric("revenue", f"{latest_period}营收", f"{latest_revenue / 1e8:,.2f}" if latest_revenue is not None else None, unit="亿元", source_ids=financial_source),
        _metric("net_profit", f"{latest_period}归母净利润", f"{latest_profit / 1e8:,.2f}" if latest_profit is not None else None, unit="亿元", source_ids=financial_source),
        _metric("revenue_growth", "营收同比增长", f"{_growth(revenue_row):.1f}" if _growth(revenue_row) is not None else None, unit="%", description="仅比较上年同一报告期", source_ids=financial_source),
        _metric("profit_growth", "净利润同比增长", f"{_growth(profit_row):.1f}" if _growth(profit_row) is not None else None, unit="%", source_ids=financial_source),
    ]

    if template == "banking":
        banking_values = {
            "net_interest_margin": _indicator_value(abstract, indicator, "净息差"),
            "npl_ratio": _indicator_value(abstract, indicator, "不良贷款率", "不良率"),
            "provision_coverage": _indicator_value(abstract, indicator, "拨备覆盖率"),
            "cet1_ratio": _indicator_value(abstract, indicator, "核心一级资本充足率"),
            "capital_adequacy": _indicator_value(abstract, indicator, "资本充足率"),
            "cost_income_ratio": _indicator_value(abstract, indicator, "成本收入比"),
        }
        metrics.extend([
            _metric("net_interest_margin", "净息差", _fmt(banking_values["net_interest_margin"]), unit="%", source_ids=financial_source),
            _metric("npl_ratio", "不良贷款率", _fmt(banking_values["npl_ratio"]), unit="%", tone="warning", source_ids=financial_source),
            _metric("provision_coverage", "拨备覆盖率", _fmt(banking_values["provision_coverage"]), unit="%", source_ids=financial_source),
            _metric("cet1_ratio", "核心一级资本充足率", _fmt(banking_values["cet1_ratio"]), unit="%", source_ids=financial_source),
            _metric("capital_adequacy", "资本充足率", _fmt(banking_values["capital_adequacy"]), unit="%", source_ids=financial_source),
            _metric("cost_income_ratio", "成本收入比", _fmt(banking_values["cost_income_ratio"]), unit="%", source_ids=financial_source),
        ])
        summary = (
            f"{company.name} {latest_period}已取得营收与利润数据；银行业财务判断需进一步核验净息差、资产质量和资本充足率。"
            if latest_revenue is not None else f"{company.name} 尚未取得可计算的银行业财务序列。"
        )
    else:
        cash_ratio = _indicator_value(abstract, indicator, "经营现金净流量与净利润的比率")
        debt_ratio = _indicator_value(abstract, indicator, "资产负债率")
        metrics.extend([
            _metric("cash_flow_elasticity", "现金流弹性", _fmt(cash_ratio), unit="倍", description="经营现金净流量与净利润比率", source_ids=financial_source),
            _metric("debt_ratio", "资产负债率", _fmt(debt_ratio), unit="%", source_ids=financial_source),
        ])
        summary = (
            f"{company.name} {latest_period}累计营收{latest_revenue / 1e8:,.2f}亿元、归母净利润{latest_profit / 1e8:,.2f}亿元。"
            if latest_revenue is not None and latest_profit is not None else f"{company.name} 尚未取得可计算的营收与利润序列。"
        )

    profit_by_period = dict(profit_values)
    table_items = []
    for period, revenue in revenue_values[-6:]:
        profit = profit_by_period.get(period)
        table_items.append({
            "period": _period_label(period),
            "revenue": round(revenue / 1e8, 2),
            "net_profit": round(profit / 1e8, 2) if profit is not None else None,
            "net_margin": round(profit / revenue * 100, 2) if profit is not None and revenue else None,
        })
    scenario_items = []
    if latest_revenue is not None and latest_profit is not None and latest_revenue:
        margin = latest_profit / latest_revenue
        for label, rate in (("压力", -0.10), ("基准", 0.0), ("增长", 0.10)):
            scenario_revenue = latest_revenue * (1 + rate)
            scenario_items.append({
                "scenario": label,
                "revenue": round(scenario_revenue / 1e8, 2),
                "net_profit": round(scenario_revenue * margin / 1e8, 2),
                "assumption": f"营收较当前报告期{rate:+.0%}，净利率保持{margin:.2%}",
            })
    return {
        "summary": summary,
        "metrics": metrics,
        "series": [
            {"key": "revenue", "label": "营业总收入", "unit": "亿元", "points": [{"period": _period_label(p), "value": round(v / 1e8, 2)} for p, v in revenue_values], "source_ids": financial_source},
            {"key": "net_profit", "label": "归母净利润", "unit": "亿元", "points": [{"period": _period_label(p), "value": round(v / 1e8, 2)} for p, v in profit_values], "source_ids": financial_source},
        ],
        "sections": [
            {"key": "period_analysis", "title": "年度 / 季度财务分析", "kind": "table", "summary": "报告期累计口径；同比仅比较上年同一报告期。", "items": table_items, "source_ids": financial_source},
            {"key": "scenario_simulation", "title": "财务情景模拟", "kind": "scenario", "summary": "简化敏感性分析，不构成盈利预测。", "items": scenario_items, "source_ids": financial_source},
            {"key": "announcement_timeline", "title": "财务公告时间线", "kind": "timeline", "summary": "展示已入库财务公告与事件。", "items": _timeline(events), "source_ids": [f"event-{i + 1}" for i in range(len(events))]},
        ],
        "sources": sources,
        "profile_at": profile_at,
    }


def _operations(company: CompanySnapshot, events: list[EventSnapshot], template: str) -> dict[str, Any]:
    profile, abstract, indicators, profile_at = _profile_context(company)
    indicator = _latest_indicator(indicators)
    financial_source = ["akshare-financial"] if abstract or indicators else []
    sources = [_event_source(event, index) for index, event in enumerate(events)]
    if template == "banking":
        loans_row = _metric_row(abstract, "客户贷款及垫款", "贷款总额")
        deposits_row = _metric_row(abstract, "客户存款", "存款总额")
        values = {
            "loan_growth": _growth(loans_row),
            "deposit_growth": _growth(deposits_row),
            "net_interest_margin": _indicator_value(abstract, indicator, "净息差"),
            "npl_ratio": _indicator_value(abstract, indicator, "不良贷款率", "不良率"),
            "cost_income_ratio": _indicator_value(abstract, indicator, "成本收入比"),
        }
        metrics = [
            _metric("operating_events", "经营与服务事件", len(events) if events else None, unit="条", description="未取得证据不等于没有事件", source_ids=[f"event-{i + 1}" for i in range(len(events))]),
            _metric("loan_growth", "客户贷款同比增长", _fmt(values["loan_growth"]), unit="%", source_ids=financial_source),
            _metric("deposit_growth", "客户存款同比增长", _fmt(values["deposit_growth"]), unit="%", source_ids=financial_source),
            _metric("net_interest_margin", "净息差", _fmt(values["net_interest_margin"]), unit="%", source_ids=financial_source),
            _metric("npl_ratio", "不良贷款率", _fmt(values["npl_ratio"]), unit="%", tone="warning", source_ids=financial_source),
            _metric("cost_income_ratio", "成本收入比", _fmt(values["cost_income_ratio"]), unit="%", source_ids=financial_source),
            _metric("complaint_resolution", "客户投诉闭环率", None, unit="%", description="需接入投诉与工单系统"),
            _metric("system_availability", "关键系统可用率", None, unit="%", description="需接入信息科技运营数据"),
        ]
        summary = f"{company.name}已切换银行业运营模板；公开披露用于经营趋势，投诉闭环和系统可用率需内部数据。"
        peer_items = [
            {"label": "客户贷款同比增长", "company": values["loan_growth"], "peer": None},
            {"label": "净息差", "company": values["net_interest_margin"], "peer": None},
            {"label": "不良贷款率", "company": values["npl_ratio"], "peer": None},
            {"label": "成本收入比", "company": values["cost_income_ratio"], "peer": None},
        ]
        sections = [
            {"key": "bank_operations", "title": "银行经营效率", "kind": "table", "summary": "使用银行业适用指标，不再套用库存、排产和订单履约口径。", "items": peer_items, "source_ids": financial_source},
            {"key": "operating_timeline", "title": "经营与服务事件", "kind": "timeline", "summary": "经营公告、服务中断、重大项目和客户影响事件。", "items": _timeline(events), "source_ids": [f"event-{i + 1}" for i in range(len(events))]},
            {"key": "peer_benchmark", "title": "同业对标准备度", "kind": "comparison", "summary": "同行列为空表示尚无同报告期、同口径数据，不生成推测排名。", "items": peer_items, "source_ids": financial_source},
        ]
    elif template in {
        "financial_services",
        "pharma",
        "real_estate",
        "internet",
        "energy",
    }:
        plugin = detect_industry_plugin(
            company.industry,
            company.name,
            company.profile,
        )
        revenue_row = _metric_row(abstract, "营业总收入", "营业收入")
        revenue_growth = _growth(revenue_row)
        source_ids = [f"event-{i + 1}" for i in range(len(events))]
        metrics = [
            _metric(
                "operating_events",
                "经营与服务事件",
                len(events) if events else None,
                unit="条",
                description="未取得证据不等于没有事件",
                source_ids=source_ids,
            ),
            _metric(
                "revenue_growth",
                "营业收入同比增长",
                _fmt(revenue_growth),
                unit="%",
                source_ids=financial_source,
            ),
        ]
        for metric_key in plugin.metric_keys:
            if metric_key in {
                "revenue_growth",
                "operating_events",
                "service_availability",
            }:
                continue
            metrics.append(
                _metric(
                    metric_key,
                    INDUSTRY_METRIC_LABELS.get(metric_key, metric_key),
                    None,
                    description="需接入行业主管部门、公司披露或授权内部数据",
                )
            )
        metrics.extend([
            _metric(
                "complaint_resolution",
                "客户投诉闭环率",
                None,
                unit="%",
                description="需接入投诉及客服系统",
            ),
            _metric(
                "service_availability",
                "关键服务可用率",
                None,
                unit="%",
                description="需接入企业内部运营数据",
            ),
        ])
        summary = (
            f"{company.name}已使用{plugin.name if plugin.code != 'general' else '金融服务业'}"
            "运营模板；公开披露与内部运营指标分层展示。"
        )
        sections = [
            {
                "key": "industry_operations",
                "title": "行业经营指标",
                "kind": "table",
                "summary": "仅展示适用于当前行业的指标；缺失项保持待接入。",
                "items": [
                    {
                        "label": metric["label"],
                        "value": metric["value"],
                        "unit": metric["unit"],
                    }
                    for metric in metrics
                ],
                "source_ids": financial_source,
            },
            {
                "key": "operating_timeline",
                "title": "经营与服务事件",
                "kind": "timeline",
                "summary": "公司披露、主管部门和权威来源中的经营事件。",
                "items": _timeline(events),
                "source_ids": source_ids,
            },
        ]
    else:
        inventory_days = _indicator_value(abstract, indicator, "存货周转天数")
        receivable_days = _indicator_value(abstract, indicator, "应收账款周转天数")
        inventory_turnover = _indicator_value(abstract, indicator, "存货周转率")
        metrics = [
            _metric("operating_events", "运营节点", len(events) if events else None, unit="条", description="未取得证据不等于没有事件", source_ids=[f"event-{i + 1}" for i in range(len(events))]),
            _metric("inventory_days", "存货周转天数", _fmt(inventory_days), unit="天", source_ids=financial_source),
            _metric("inventory_turnover", "存货周转率", _fmt(inventory_turnover), unit="次", source_ids=financial_source),
            _metric("receivable_days", "应收账款周转天数", _fmt(receivable_days), unit="天", source_ids=financial_source),
            _metric("order_fulfillment", "订单履约率", None, unit="%", description="需接入订单与交付系统"),
            _metric("ticket_closure", "风险工单闭环率", None, unit="%", description="需接入风险工单系统"),
        ]
        summary = f"{company.name}当前入库{len(events)}条运营节点；内部经营漏斗未接入前不展示推测值。"
        sections = [
            {"key": "operations_funnel", "title": "经营转化漏斗", "kind": "funnel", "summary": "未接入订单系统前仅展示准备状态。", "items": [{"label": label, "value": "待接入"} for label in ("线索 / 订单", "排产", "交付", "回款")], "source_ids": []},
            {"key": "factory_milestones", "title": "工厂与运营节点", "kind": "timeline", "summary": "按已入库运营事件展示。", "items": _timeline(events), "source_ids": [f"event-{i + 1}" for i in range(len(events))]},
        ]
    return {"summary": summary, "metrics": metrics, "series": [], "sections": sections, "sources": sources, "profile_at": profile_at}


def _event_analysis(company: CompanySnapshot, category: str, events: list[EventSnapshot]) -> dict[str, Any]:
    total = len(events)
    high = sum(event.severity in SEVERITY_HIGH for event in events)
    negative = sum(event.sentiment in SENTIMENT_NEGATIVE for event in events)
    negative_share = round(negative / total * 100, 1) if total else None
    sources = [_event_source(event, index) for index, event in enumerate(events)]
    source_ids = [f"event-{index + 1}" for index in range(total)]
    severity_counts = Counter("高优先级" if event.severity in SEVERITY_HIGH else "一般" for event in events)
    sentiment_counts = Counter(
        "负面" if event.sentiment in SENTIMENT_NEGATIVE else "正面" if event.sentiment in SENTIMENT_POSITIVE else "中性"
        for event in events
    )
    if category == "legal":
        metrics = [
            _metric("case_count", "法律/监管事件", total if total else None, unit="条", description="未取得证据不等于不存在风险", source_ids=source_ids),
            _metric("high_priority", "高优先级", high if total else None, unit="条", description="仅基于当前入库样本", tone="warning" if high else "neutral", source_ids=source_ids),
            _metric("negative_share", "负面事件占比", _fmt(negative_share), unit="%", source_ids=source_ids),
            _metric("exposure_amount", "已披露涉案金额", None, unit="万元", description="仅汇总明确披露金额"),
        ]
        sections = [
            {"key": "legal_timeline", "title": "法律与监管事件时间轴", "kind": "timeline", "summary": "诉讼、调查、处罚、问询和合规事件。", "items": _timeline(events), "source_ids": source_ids},
            {"key": "risk_summary", "title": "风险分级汇总", "kind": "distribution", "summary": "按已入库事件严重度统计。", "items": [{"label": label, "value": value} for label, value in severity_counts.items()], "source_ids": source_ids},
            {"key": "risk_transmission", "title": "风险传导与联动建议", "kind": "recommendation", "summary": "从监管/法律事件映射经营、财务和品牌影响。", "items": [{"title": event.title, "path": "法律/监管 → 业务流程 → 财务或客户影响 → 品牌", "action": "核验决定文号、阶段、金额、责任部门和整改期限", "source_id": f"event-{index + 1}"} for index, event in enumerate(events[:5])], "source_ids": source_ids[:5]},
        ]
        summary = f"{company.name}法律与监管维度已入库{total}条事件；未检出不能等同于不存在风险。"
    else:
        source_counts = Counter(event.source_name or "未知来源" for event in events)
        metrics = [
            _metric("mention_count", "舆情样本", total if total else None, unit="条", description="未取得样本不等于没有舆情", source_ids=source_ids),
            _metric("negative_share", "负面占比", _fmt(negative_share), unit="%", tone="warning" if negative else "neutral", source_ids=source_ids),
            _metric("source_count", "覆盖来源", len(source_counts) if total else None, unit="个", source_ids=source_ids),
            _metric("weighted_influence", "加权影响力", None, description="需接入媒体/KOL权重与传播量"),
        ]
        denominator = total or 1
        distribution = [
            {"label": label, "value": round(sentiment_counts.get(label, 0) / denominator * 100, 1)}
            for label in ("正面", "中性", "负面")
        ]
        sections = [
            {"key": "sentiment_distribution", "title": "品牌声量分布", "kind": "distribution", "summary": "仅代表当前入库样本，不代表全网舆情。", "items": distribution, "source_ids": source_ids},
            {"key": "source_distribution", "title": "财经媒体 / 社交平台 / 行业自媒体", "kind": "distribution", "summary": "按当前来源统计；没有传播量时不伪造影响力。", "items": [{"label": label, "value": value} for label, value in source_counts.most_common()], "source_ids": source_ids},
            {"key": "topic_heat", "title": "实时议题与事件时间线", "kind": "timeline", "summary": "当前以事件时间线代替无来源的虚假热度。", "items": _timeline(events), "source_ids": source_ids},
        ]
        summary = f"{company.name}品牌舆情维度已入库{total}条样本；情绪占比仅代表可追溯样本。"
    return {"summary": summary, "metrics": metrics, "series": [], "sections": sections, "sources": sources, "profile_at": None}


def _macro(db: Session, company: CompanySnapshot) -> dict[str, Any]:
    indicator_rows = _rows(db, "SELECT * FROM macro_indicator_points ORDER BY collected_at DESC LIMIT 600")
    event_rows = _rows(db, "SELECT * FROM macro_industry_events ORDER BY published_at DESC LIMIT 120")
    latest_by_code: dict[str, dict[str, Any]] = {}
    series_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in indicator_rows:
        code = str(row.get("indicator_code") or "")
        if code and code not in latest_by_code:
            latest_by_code[code] = row
        if code:
            series_by_code[code].append(row)
    preferred = ["gdp_yoy", "manufacturing_pmi", "m2_yoy", "lpr_1y", "cpi_yoy", "ppi_yoy"]
    metrics = []
    for code in preferred:
        row = latest_by_code.get(code)
        metrics.append(_metric(
            code,
            str(row.get("indicator_name")) if row else code,
            _fmt(_number(row.get("value"))) if row else None,
            unit=str(row.get("unit") or "") if row else "",
            source_ids=[f"macro-{code}"] if row else [],
        ))
    series = []
    for code in preferred[:4]:
        rows = sorted(series_by_code.get(code, []), key=lambda row: str(row.get("period") or ""))[-12:]
        if rows:
            series.append({
                "key": code,
                "label": str(rows[-1].get("indicator_name") or code),
                "unit": str(rows[-1].get("unit") or ""),
                "points": [{"period": str(row.get("period") or ""), "value": float(row.get("value") or 0)} for row in rows],
                "source_ids": [f"macro-{code}"],
            })
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, row in latest_by_code.items():
        source_url = str(row.get("source_url") or "")
        key = source_url or f"{row.get('source_name')}-{code}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "id": f"macro-{code}",
            "title": f"{row.get('indicator_name')}（{row.get('period')}）",
            "source_name": str(row.get("source_name") or "官方宏观数据"),
            "source_url": source_url,
            "source_tag": "官方宏观指标",
            "source_code": "pbc_macro" if code in {"lpr_1y", "m2_yoy"} else "nbs_macro",
            "published_at": _dt(row.get("collected_at")),
        })
    industry_events = []
    for index, row in enumerate(event_rows[:30]):
        source_url = str(row.get("source_url") or "")
        sources.append({
            "id": f"macro-event-{index + 1}",
            "title": str(row.get("title") or ""),
            "source_name": str(row.get("source_name") or "官方政策来源"),
            "source_url": source_url,
            "source_tag": "政策/行业事件",
            "source_code": (
                "ndrc_policy"
                if "发展和改革" in str(row.get("source_name") or "")
                else str(row.get("dimension") or "macro_event")
            ),
            "published_at": _dt(row.get("published_at")),
        })
        industry_events.append({
            "date": str(row.get("published_at") or "")[:10],
            "title": str(row.get("title") or ""),
            "detail": str(row.get("summary") or ""),
            "direction": str(row.get("impact_direction") or "neutral"),
            "severity": str(row.get("severity") or "normal"),
            "source_id": f"macro-event-{index + 1}",
        })
    sections = [
        {"key": "macro_radar", "title": "宏观信号雷达", "kind": "radar", "summary": "使用国家统计局、人民银行等可追溯指标。", "items": [{"label": metric["label"], "value": metric["value"]} for metric in metrics if metric["value"] != "待接入"], "source_ids": [source["id"] for source in sources]},
        {"key": "policy_timeline", "title": "政策与行业事件时间线", "kind": "timeline", "summary": "官方政策、经济和行业节点。", "items": industry_events, "source_ids": [item["source_id"] for item in industry_events]},
    ]
    summary = f"{company.name}宏观与行业分析已覆盖{len(latest_by_code)}类指标和{len(event_rows)}条政策/行业事件。"
    collected_times = [
        timestamp
        for row in indicator_rows
        if (timestamp := _dt(row.get("collected_at"))) is not None
    ]
    latest_at = max(collected_times, default=None)
    return {"summary": summary, "metrics": metrics, "series": series, "sections": sections, "sources": sources, "profile_at": latest_at}


def _fmt(value: float | None, digits: int = 2) -> str | None:
    return f"{value:.{digits}f}" if value is not None else None


def _last_ingestion_state(db: Session, company_id: Any) -> tuple[set[str], dict[str, str], datetime | None]:
    columns = {column["name"] for column in inspect(db.get_bind()).get_columns("ingestion_runs")}
    if "company_id" in columns:
        company_key = str(company_id).replace("-", "")
        runs = _rows(
            db,
            """
            SELECT * FROM ingestion_runs
            WHERE REPLACE(CAST(company_id AS TEXT), '-', '') = :company_key
            ORDER BY started_at DESC LIMIT 20
            """,
            {"company_key": company_key},
        )
    else:
        runs = _rows(db, "SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT 20")
    enabled: set[str] = set()
    failures: dict[str, str] = {}
    last_checked = None
    for run in runs:
        last_checked = last_checked or _dt(run.get("finished_at")) or _dt(run.get("started_at"))
        summary = _json(run.get("summary"), {})
        enabled.update(summary.get("enabled_sources") or [])
        for failure in _json(run.get("failures"), []):
            code = str(failure.get("source_code") or "")
            if code and code not in failures:
                failures[code] = str(failure.get("error") or "数据源请求失败")[:240]
    return enabled, failures, last_checked


def _retrieval_checks(
    db: Session,
    company_id: Any,
    category: str,
) -> tuple[dict[str, dict[str, Any]], datetime | None]:
    if not inspect(db.get_bind()).has_table("source_retrieval_runs"):
        return {}, None
    rows = _rows(
        db,
        """
        SELECT source_code, status, checked_at, discovered_count, ingested_count, error
        FROM source_retrieval_runs
        WHERE REPLACE(CAST(company_id AS TEXT), '-', '') = :company_key
          AND category = :category
        ORDER BY checked_at DESC
        """,
        {"company_key": str(company_id).replace("-", ""), "category": category},
    )
    latest: dict[str, dict[str, Any]] = {}
    checked_at = None
    for row in rows:
        code = str(row.get("source_code") or "")
        if not code or code in latest:
            continue
        latest[code] = row
        timestamp = _dt(row.get("checked_at"))
        if timestamp and (checked_at is None or timestamp > checked_at):
            checked_at = timestamp
    return latest, checked_at


EXPECTED_SOURCES = {
    "macro": [
        ("nbs_macro", "国家统计局", "official"),
        ("pbc_macro", "中国人民银行", "official"),
        ("ndrc_policy", "国家发展和改革委员会", "official"),
    ],
    "finance": [
        ("cninfo_announcements", "巨潮资讯公告", "official"),
        ("exchange_disclosure", "证券交易所披露", "official"),
        ("company_official_site", "公司官网/投资者关系", "official"),
        ("akshare_finance", "AkShare财务聚合", "aggregator"),
    ],
    "operations": [
        ("cninfo_announcements", "巨潮资讯经营公告", "official"),
        ("company_official_site", "公司官网/投资者关系", "official"),
        ("authoritative_web_search", "权威新闻与官方站点检索", "licensed"),
        ("internal_operations", "订单/服务/工单内部系统", "internal"),
    ],
    "legal": [
        ("cninfo_announcements", "巨潮资讯诉讼与处罚公告", "official"),
        ("exchange_disclosure", "证券交易所问询与纪律处分", "official"),
        ("nfra_penalties", "国家金融监督管理总局行政处罚", "official"),
        ("pbc_official", "中国人民银行行政处罚与监管信息", "official"),
        ("csrc_enforcement", "中国证监会监管与执法信息", "official"),
        ("authoritative_web_search", "监管机构官方站点检索", "licensed"),
        ("enterprise_credit", "国家企业信用信息公示系统", "official"),
        ("judicial_disclosure", "合规授权司法公开数据", "official"),
    ],
    "brand": [
        ("company_official_site", "公司官网", "official"),
        ("authoritative_web_search", "权威新闻检索", "licensed"),
        ("licensed_social", "持牌社交与投诉数据", "licensed"),
    ],
}


def _source_coverage(
    db: Session,
    company: CompanySnapshot,
    category: str,
    sources: list[dict[str, Any]],
    profile_available: bool,
) -> list[dict[str, Any]]:
    enabled, failures, last_checked = _last_ingestion_state(db, company.id)
    retrieval_checks, retrieval_checked_at = _retrieval_checks(db, company.id, category)
    if retrieval_checked_at and (last_checked is None or retrieval_checked_at > last_checked):
        last_checked = retrieval_checked_at
    counts: Counter[str] = Counter()
    names: Counter[str] = Counter()
    for source in sources:
        code = str(source.get("source_code") or "")
        if code:
            counts[code] += 1
        names[str(source.get("source_name") or "")] += 1
    result = []
    expected_sources = list(EXPECTED_SOURCES[category])
    if category != "macro":
        _, planned_sources = source_plan_for(
            company.industry,
            company.name,
            company.profile,
        )
        expected_sources = [
            (source.code, source.name, source.authority)
            for source in planned_sources
            if category in source.categories
        ]
        if category == "finance":
            expected_sources.append(
                ("akshare_finance", "AkShare财务聚合", "aggregator")
            )
        if category == "operations":
            expected_sources.append(
                ("internal_operations", "企业内部经营系统", "internal")
            )
    deduplicated = {
        code: (code, label, authority)
        for code, label, authority in expected_sources
    }
    for code, label, authority in deduplicated.values():
        points = counts.get(code, 0)
        if not points:
            points = sum(count for name, count in names.items() if label[:4] in name or name[:4] in label)
        if code == "akshare_finance" and profile_available:
            points = max(points, 1)
        if points:
            status = "success"
            message = f"已入库{points}条可追溯证据"
        elif code in retrieval_checks:
            check = retrieval_checks[code]
            raw_status = str(check.get("status") or "failed")
            status = raw_status if raw_status in {"success", "no_hit", "failed", "not_connected"} else "failed"
            discovered = int(check.get("discovered_count") or 0)
            ingested = int(check.get("ingested_count") or 0)
            message = (
                str(check.get("error") or "")
                if status in {"failed", "not_connected"}
                else f"最近一次检索发现{discovered}条、入库{ingested}条"
            )
        elif code in failures:
            status = "failed"
            message = failures[code]
        elif code in enabled:
            status = "no_hit"
            message = "最近一次检索成功执行，但当前维度未命中证据"
        elif code == "authoritative_web_search" and not os.getenv("SERPER_API_KEY", "").strip():
            status = "not_connected"
            message = "未配置新闻检索凭据；仅允许白名单官方与权威域名"
        elif authority == "internal":
            status = "not_connected"
            message = "属于企业内部经营数据，需要授权接入"
        else:
            status = "not_connected"
            message = "连接器尚未执行或未配置"
        result.append({
            "code": code,
            "source": label,
            "status": status,
            "authority": authority,
            "points": points,
            "message": message,
            "categories": [category],
            "last_checked_at": last_checked,
        })
    return result


def _metric_coverage(
    metrics: list[dict[str, Any]],
    *,
    category: str,
    template: str,
    sources: list[dict[str, Any]],
    profile_at: datetime | None,
) -> list[dict[str, Any]]:
    source_by_id = {str(source.get("id")): source for source in sources}
    result = []
    for metric in metrics:
        value = metric.get("value")
        source_ids = [str(item) for item in metric.get("source_ids") or []]
        applicable = True
        if template == "banking" and metric.get("key") in {"inventory_days", "inventory_turnover", "receivable_days", "debt_ratio", "cash_flow_elasticity"}:
            applicable = False
        if not applicable:
            status = "not_applicable"
            reason = "当前行业模板不适用该指标"
        elif value not in (None, "", "待接入") and source_ids:
            status = "available"
            reason = "已取得可追溯字段"
        elif value not in (None, "", "待接入"):
            status = "partial"
            reason = "已有数值但尚未绑定到具体证据"
        else:
            status = "not_connected"
            reason = metric.get("description") or "所需字段尚未接入"
        as_of = profile_at
        for source_id in source_ids:
            source_at = _dt(source_by_id.get(source_id, {}).get("published_at"))
            if source_at and (as_of is None or source_at > as_of):
                as_of = source_at
        result.append({
            "key": str(metric.get("key") or ""),
            "label": str(metric.get("label") or ""),
            "status": status,
            "weight": 1,
            "reason": reason,
            "source_ids": source_ids,
            "as_of": as_of,
            "applicable": applicable,
        })
    return result


def _quality(
    coverage: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    applicable = [item for item in coverage if item["status"] != "not_applicable"]
    denominator = sum(float(item.get("weight") or 1) for item in applicable) or 1
    now = datetime.now(timezone.utc)
    source_by_id = {str(source.get("id")): source for source in sources}
    completeness_points = freshness_points = authority_points = corroboration_points = 0.0
    for item in applicable:
        weight = float(item.get("weight") or 1)
        status = item["status"]
        completeness_points += weight * ({"available": 1, "partial": 0.5, "stale": 0.6}.get(status, 0))
        as_of = _dt(item.get("as_of"))
        if status in {"available", "partial", "stale"}:
            if as_of is None:
                freshness_points += weight * 0.4
            else:
                age_days = max(0, (now - as_of).days)
                freshness_points += weight * (1 if age_days <= 120 else 0.6 if age_days <= 365 else 0.2)
        authorities = []
        for source_id in item.get("source_ids") or []:
            source = source_by_id.get(str(source_id), {})
            name = str(source.get("source_name") or "")
            tag = str(source.get("source_tag") or "")
            authorities.append(0.8 if "AkShare" in name or "聚合" in tag else 1.0)
        if authorities:
            authority_points += weight * max(authorities)
            corroboration_points += weight * (1 if len(authorities) >= 2 else 0.5)
    components = {
        "completeness": round(completeness_points / denominator * 100),
        "freshness": round(freshness_points / denominator * 100),
        "authority": round(authority_points / denominator * 100),
        "corroboration": round(corroboration_points / denominator * 100),
    }
    overall = round(
        components["completeness"] * 0.40
        + components["freshness"] * 0.20
        + components["authority"] * 0.25
        + components["corroboration"] * 0.15
    )
    status = "complete" if overall >= 80 else "partial" if overall >= 35 else "insufficient"
    missing = [item for item in applicable if item["status"] not in {"available", "partial", "stale"}]
    warnings = [f"{item['label']}：{item['reason']}" for item in missing[:5]]
    boundary = (
        f"{len(applicable) - len(missing)}/{len(applicable)}项适用指标已有数据；"
        "覆盖率按完整度40%、时效性20%、权威可追溯性25%、交叉验证15%计算。"
    )
    updated = max((_dt(item.get("as_of")) for item in applicable if _dt(item.get("as_of"))), default=None)
    return {
        "status": status,
        "coverage_percent": overall,
        "evidence_count": len(sources),
        "warnings": warnings,
        "updated_at": updated,
        "boundary_summary": boundary,
        "components": components,
        "metric_coverage": coverage,
    }, warnings


def build_agent_preview_v2(
    db: Session,
    *,
    company_id: Any | None = None,
    company_name: str | None = None,
    category: str,
    limit: int = 12,
) -> dict[str, Any]:
    if category not in CATEGORY_LABELS:
        raise ValueError("不支持的分析维度。")
    company = _company(db, company_id=company_id, company_name=company_name)
    template = _industry_template(company)
    events = _events(db, company.id, category, limit)
    if category == "macro":
        analysis = _macro(db, company)
    elif category == "finance":
        analysis = _finance(company, events, template)
    elif category == "operations":
        analysis = _operations(company, events, template)
    else:
        analysis = _event_analysis(company, category, events)

    source_coverage = _source_coverage(
        db,
        company,
        category,
        analysis["sources"],
        bool((company.profile.get("akshare_profile") or {}).get("status") == "available"),
    )
    metric_coverage = _metric_coverage(
        analysis["metrics"],
        category=category,
        template=template,
        sources=analysis["sources"],
        profile_at=analysis.get("profile_at"),
    )
    data_quality, _ = _quality(metric_coverage, analysis["sources"])
    if not events and category in {"legal", "brand"}:
        data_quality["warnings"].append("当前数据库没有该维度事件；这表示未取得证据，不表示现实中不存在风险。")
    failed = [source for source in source_coverage if source["status"] == "failed"]
    if failed:
        data_quality["warnings"].append(f"{len(failed)}个数据源最近抓取失败，已从覆盖率中按缺失处理。")
    key_points = [f"{event.title}：{event.content}" for event in events[:3]]
    if not key_points:
        key_points = [data_quality["boundary_summary"], "缺失指标保持待接入状态，不填充演示结论。"]
    stage = "industry_template_first" if template != "general" else "evidence_contract_first"
    return {
        "company_name": company.name,
        "category": category,
        "report_type": REPORT_TYPES[category],
        "retrieval_stage": stage,
        "industry_template": template,
        "summary": analysis["summary"],
        "key_points": key_points,
        "next_actions": DEFAULT_NEXT_ACTIONS[category],
        "sources": [{key: value for key, value in source.items() if key != "id"} for source in analysis["sources"]],
        "metrics": analysis["metrics"],
        "series": [series for series in analysis["series"] if series.get("points")],
        "sections": analysis["sections"],
        "data_quality": data_quality,
        "source_coverage": source_coverage,
        "generated_at": datetime.now(timezone.utc),
    }
