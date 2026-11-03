from __future__ import annotations

import io
import calendar
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisRun,
    Company,
    ExtractedFact,
    MacroIndicatorPoint,
    MacroIndustryEvent,
    MonthlyReport,
    MonitoringRule,
    RelatedEntity,
    ReportVersion,
    RiskEvent,
    RuleEvaluation,
)
from app.config import get_settings
from app.services.llm import LLMService
from app.services.monitoring_scope import PARTNER_RELATIONS, confirmed_entities, official_scope


SECTION_ORDER = [
    "risk_overview",
    "actions",
    "external",
    "operations",
    "finance",
    "governance",
    "internal_control",
    "legal",
    "brand",
    "sentiment",
    "other",
]

SECTION_LABELS = {
    "risk_overview": "风险概况",
    "actions": "行动建议",
    "external": "外部风险",
    "operations": "业务及经营风险",
    "finance": "财务风险",
    "governance": "公司治理风险",
    "internal_control": "内控风险",
    "legal": "法律风险",
    "brand": "品牌风险",
    "sentiment": "舆情风险",
    "other": "其他风险",
}

RISK_SECTIONS = SECTION_ORDER[2:]

RATING_LEVELS = ("低风险", "较低风险", "中风险", "较高风险", "高风险")

# 五档定义与判定标准沿用客户月报《风险概况评级逻辑》表，按高到低排列。
RATING_DEFINITIONS = (
    (
        "高风险",
        "重大实际损失已经发生或迫近，或企业持续经营受威胁，需立即处置。",
        "重大风险已传导至企业；影响投资本金或持续经营；现有缓释措施失效。"
        "对重大损失的认定结合既有各板块监测阈值及具体敞口。",
    ),
    (
        "较高风险",
        "单项重大或多项风险叠加，短期传导可能性高，需重点监控并制定预案。",
        "尚未形成重大实际损失，但预计短期内传导概率高；核心业务、主要合作或财务承受能力"
        "可能受显著影响；缓释方案不足或落地不确定。",
    ),
    (
        "中风险",
        "存在明确风险及传导路径，对经营可能有一定负面影响，但影响尚可控制。",
        "外部、业务或合作环节有持续预警；传导受条件和时间影响；尚未证明已形成重大损失"
        "或持续经营威胁；需常规跟踪及针对性缓释。",
    ),
    (
        "较低风险",
        "已识别零星、轻微或间接风险信号，尚未明显影响企业经营。",
        "风险多为个案或关联方间接事项；未形成明确实质损失；传导概率或影响较低，常规监测即可。",
    ),
    (
        "低风险",
        "本期已覆盖监测内容未识别异常，未发现需单独提示的风险信号。",
        "仅表示在本期已取得资料和已覆盖范围内未识别异常，不代表未来绝对无风险。"
        "必要经营、财务或内控资料缺失时，必须注明资料不足并复核，不得据此认定风险已充分排除。",
    ),
)

RATING_NOTE = (
    "沿用风险概况的五档文字评级，不将等级等同于发生概率。结合本期风险事项、与被投企业的业务联系、"
    "影响及缓释情况独立判断，不按新闻数量或各板块简单平均定级。资料不足时明确列示判断限制，"
    "不将缺少信息等同于未发现风险。"
)

RATING_SCALE_TEXT = (
    "低风险：本期监测未发现异常；\n"
    "较低风险：有零星或间接信号，影响轻微；\n"
    "中风险：风险明确、有传导路径，影响尚可控制；\n"
    "较高风险：重大或多项风险叠加、短期传导可能性高，缓释不足；\n"
    "高风险：重大损失已发生或迫近、持续经营受威胁。"
)

STYLE_GUIDE = (
    "文风模仿风险管理台账月报：结论先行，先用一小段概括本月整体特征与较上期变化，再分板块叙述；"
    "每个板块用连贯段落陈述事实与判断，需要评估影响时以“风险分析：”引出；"
    "没有发现风险时直接写“本期暂未发现相关风险”；"
    "对有数据的财务经营事项写清本期值、上期值及变化原因；对风险事项写清主体、事件、影响和待跟踪动作；"
    "面向客户的正文中严禁出现“规则”“命中N项”“监测要点”“insufficient_data”等内部评估术语；"
    "数据不足时写明尚缺何种材料、后续如何补充，不作风险推断；"
    "行动建议使用“跟踪、评估、核验、清收、完善、审慎”等动词，写成可执行的具体动作；"
    "不要用宣传性、夸张性或模板化语言。"
)

CATEGORY_SECTIONS = {
    "外部风险": "external",
    "业务及经营风险": "operations",
    "财务风险": "finance",
    "公司治理风险": "governance",
    "内控风险": "internal_control",
    "法律风险": "legal",
    "其他风险": "other",
}

FINANCE_METRIC_LABELS = {
    "revenue": "营业收入",
    "net_profit": "净利润",
    "net_assets": "净资产",
    "cash": "货币资金",
    "receivables": "应收款项",
    "operating_cashflow": "经营活动现金流量净额",
}

MACRO_INDICATOR_ORDER = (
    "gdp_yoy",
    "cpi_yoy",
    "ppi_yoy",
    "manufacturing_pmi",
    "non_manufacturing_pmi",
    "lpr_1y",
    "m2_yoy",
)


SENTIMENT_RULE_CODES = {76, 80}


def _section_for_rule(rule: MonitoringRule) -> str:
    if rule.category == "品牌舆情风险":
        return "sentiment" if rule.code in SENTIMENT_RULE_CODES else "brand"
    return CATEGORY_SECTIONS.get(rule.category, "other")


def _evaluations(db: Session, run_id: Any) -> list[tuple[RuleEvaluation, MonitoringRule]]:
    return list(
        db.execute(
            select(RuleEvaluation, MonitoringRule)
            .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
            .where(RuleEvaluation.analysis_run_id == run_id)
            .order_by(MonitoringRule.code)
        ).all()
    )


def _previous_month(period: str) -> str:
    year, month = (int(part) for part in period.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _report_as_of_date(period: str) -> date:
    configured = get_settings().demo_report_as_of_date
    if configured:
        try:
            parsed = date.fromisoformat(configured)
            if parsed.strftime("%Y-%m") == period:
                return parsed
        except ValueError:
            pass
    today = datetime.now().date()
    if today.strftime("%Y-%m") == period:
        return today
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, calendar.monthrange(year, month)[1])


def _period_rank(value: str) -> tuple[int, int]:
    text = str(value or "")
    month_match = re.search(r"(20\d{2})[-/.](1[0-2]|0?[1-9])(?!\d)", text)
    if month_match:
        return int(month_match.group(1)), int(month_match.group(2))
    quarter_match = re.search(r"(20\d{2}).*?[Qq季](?:度)?[- ]?([1-4])", text)
    if not quarter_match:
        quarter_match = re.search(r"(20\d{2})[- ]?[Qq]([1-4])", text)
    if quarter_match:
        return int(quarter_match.group(1)), int(quarter_match.group(2)) * 3
    year_match = re.search(r"(20\d{2})", text)
    return (int(year_match.group(1)), 12) if year_match else (0, 0)


def _as_utc(value: datetime) -> datetime:
    # SQLite 读回的时间不带时区，按入库时的 UTC 处理。
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _macro_context(
    db: Session, period: str, company_id: Any = None
) -> list[dict[str, Any]]:
    cutoff = _period_rank(period)
    rows = list(
        db.execute(
            select(MacroIndicatorPoint)
            .where(MacroIndicatorPoint.region_scope == "全国")
            .order_by(MacroIndicatorPoint.collected_at.desc())
            .limit(1000)
        ).scalars()
    )
    # 每个指标取不晚于报告期的最近一期；同一批入库的数据不能按入库时间挑选。
    latest: dict[str, MacroIndicatorPoint] = {}
    for row in rows:
        rank = _period_rank(row.period)
        if rank > cutoff:
            continue
        current = latest.get(row.indicator_code)
        if current is None or rank > _period_rank(current.period):
            latest[row.indicator_code] = row
    result: list[dict[str, Any]] = []
    for code in MACRO_INDICATOR_ORDER:
        row = latest.get(code)
        if not row:
            continue
        result.append(
            {
                "kind": "indicator",
                "evidence_id": f"macro-indicator-{row.id}",
                "code": row.indicator_code,
                "label": row.indicator_name,
                "period": row.period,
                "value": row.value,
                "unit": row.unit,
                "source_name": row.source_name,
                "source_url": row.source_url,
            }
        )

    as_of = _report_as_of_date(period)
    end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
    year, month = (int(part) for part in period.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    recent = db.execute(
        select(MacroIndustryEvent)
        .where(MacroIndustryEvent.published_at <= end)
        .where(MacroIndustryEvent.published_at >= start - timedelta(days=62))
        .order_by(MacroIndustryEvent.published_at.desc())
    ).scalars()
    # 本期发布的事件，以及人工整理时归入本期的事件（如月末施行的政策）全部纳入。
    events = [
        event
        for event in recent
        if _as_utc(event.published_at) >= start
        or (event.raw_payload or {}).get("report_period") == period
    ]
    if not events:
        # 本期没有整理行业事件时，沿用最近事件作为外部环境背景。
        events = list(
            db.execute(
                select(MacroIndustryEvent)
                .where(MacroIndustryEvent.published_at <= end)
                .order_by(MacroIndustryEvent.published_at.desc())
                .limit(12)
            ).scalars()
        )
    result.extend(
        {
            "kind": "event",
            "evidence_id": f"macro-event-{event.id}",
            "dimension": event.dimension,
            "scope": event.scope_key,
            "in_period": _as_utc(event.published_at) >= start
            or (event.raw_payload or {}).get("report_period") == period,
            "event_type": event.event_type,
            "label": event.title,
            "period": event.published_at.date().isoformat(),
            "summary": event.summary,
            "source_name": event.source_name,
            "source_url": event.source_url,
        }
        for event in events
    )
    if company_id:
        company_events = list(
            db.execute(
                select(RiskEvent)
                .where(RiskEvent.company_id == company_id)
                .where(RiskEvent.occurred_at <= end)
                .order_by(RiskEvent.occurred_at.desc())
                .limit(12)
            ).scalars()
        )
        result.extend(
            {
                "kind": "company_event",
                "evidence_id": f"risk-event-{event.id}",
                "period_status": (
                    PERIOD_CURRENT
                    if start.date() <= _published_on(event) <= as_of
                    else PERIOD_HISTORICAL
                ),
                "category": event.category,
                "severity": event.severity,
                "sentiment": event.sentiment,
                "label": event.title,
                "period": event.occurred_at.date().isoformat() if event.occurred_at else "",
                "summary": event.content,
                "source_name": event.source_name,
                "source_url": event.source_url,
            }
            for event in company_events
        )
    return result


def _macro_sentence(context: list[dict[str, Any]], as_of: date) -> str:
    indicators = [item for item in context if item.get("kind") == "indicator"]
    if not indicators:
        return (
            f"截至{as_of.isoformat()}，尚未取得可追溯的当期国家宏观指标，"
            "外部环境结论仅依据已入库事件，不对缺失数据作推断。"
        )
    parts = [
        f"{item['period']}{item['label']}{float(item['value']):g}{item.get('unit') or ''}"
        for item in indicators
    ]
    sources = "、".join(
        dict.fromkeys(str(item.get("source_name") or "") for item in indicators)
    )
    return f"截至{as_of.isoformat()}，{sources}公开指标显示：" + "；".join(parts) + "。"


def _monthly_metric_context(
    db: Session, report: MonthlyReport
) -> list[dict[str, Any]]:
    previous = _previous_month(report.period)
    facts = list(
        db.execute(
            select(ExtractedFact)
            .where(ExtractedFact.company_id == report.company_id)
            .where(ExtractedFact.status == "confirmed")
            .where(ExtractedFact.metric_code.in_(FINANCE_METRIC_LABELS))
            .where(ExtractedFact.period.in_([report.period, previous]))
            .order_by(ExtractedFact.created_at.asc())
        ).scalars()
    )
    latest: dict[tuple[str, str], ExtractedFact] = {
        (fact.metric_code, fact.period): fact for fact in facts
    }
    result = []
    for code, label in FINANCE_METRIC_LABELS.items():
        current = latest.get((code, report.period))
        prior = latest.get((code, previous))
        if not current or current.value_numeric is None:
            continue
        change = None
        if prior and prior.value_numeric not in (None, 0):
            change = float(current.value_numeric) / float(prior.value_numeric) - 1
        result.append(
            {
                "metric_code": code,
                "label": label,
                "period": report.period,
                "value": current.value_numeric,
                "value_text": current.value_text,
                "unit": current.unit,
                "previous_period": previous,
                "previous_value": prior.value_numeric if prior else None,
                "month_over_month": change,
                "evidence_id": f"fact-{current.id}",
                "source_locator": current.source_locator,
                "document_id": str(current.document_id),
            }
        )
    existing_codes = {item["metric_code"] for item in result}
    company = db.get(Company, report.company_id)
    profile_rows = (
        ((company.company_profile or {}).get("akshare_profile") or {}).get(
            "financial_abstract"
        )
        if company
        else []
    ) or []
    aliases = {
        "revenue": ("营业总收入", "营业收入"),
        "net_profit": ("归母净利润", "净利润"),
        "net_assets": ("归母股东权益", "股东权益", "净资产"),
        "operating_cashflow": ("经营现金净流量", "经营活动产生的现金流量净额"),
    }
    report_period_key = report.period.replace("-", "") + "99"
    for code, names in aliases.items():
        if code in existing_codes:
            continue
        row = next(
            (
                item
                for item in profile_rows
                if any(
                    name in str(item.get("指标") or item.get("项目") or "")
                    for name in names
                )
            ),
            None,
        )
        if not row:
            continue
        values = []
        for key, value in row.items():
            key_text = str(key)
            if not re.fullmatch(r"20\d{6}", key_text) or key_text > report_period_key:
                continue
            try:
                number = float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                continue
            values.append((key_text, number))
        if not values:
            continue
        latest_period, value = sorted(values)[-1]
        result.append(
            {
                "metric_code": code,
                "label": f"{FINANCE_METRIC_LABELS[code]}（最新披露期{latest_period}）",
                "period": latest_period,
                "value": value,
                "value_text": str(value),
                "unit": "元",
                "previous_period": "",
                "previous_value": None,
                "month_over_month": None,
                "evidence_id": f"akshare-{code}-{latest_period}",
                "source_locator": f"company_profile.akshare_profile.financial_abstract.{latest_period}",
            }
        )
    return result


def _metric_sentence(metrics: list[dict[str, Any]]) -> str:
    parts = []
    for metric in metrics:
        value = float(metric["value"])
        unit = metric.get("unit") or ""
        if unit == "元":
            display = f"{value / 100_000_000:,.2f}亿元"
        elif unit == "%":
            display = f"{value * 100:.2f}%"
        else:
            display = metric.get("value_text") or f"{value:,.2f}"
        change = metric.get("month_over_month")
        comparison = (
            f"，较上月{'增长' if change >= 0 else '下降'}{abs(change) * 100:.2f}%"
            if change is not None
            else "，上月同口径数据尚未确认"
        )
        parts.append(f"{metric['label']}为{display}{comparison}")
    return "；".join(parts) + "。" if parts else ""


def _code_ranges(codes: list[int]) -> str:
    ranges: list[str] = []
    for code in sorted(set(codes)):
        if ranges and code == int(ranges[-1].split("-")[-1]) + 1:
            ranges[-1] = f"{ranges[-1].split('-')[0]}-{code}"
        else:
            ranges.append(str(code))
    return "、".join(ranges)


def _split_hits(
    items: list[tuple[RuleEvaluation, MonitoringRule]],
    hit_periods: dict[str, str] | None,
) -> tuple[list[tuple[RuleEvaluation, MonitoringRule]], list[tuple[RuleEvaluation, MonitoringRule]]]:
    hits = [(evaluation, rule) for evaluation, rule in items if evaluation.status == "hit"]
    if not hit_periods:
        return hits, []
    current = [item for item in hits if hit_periods.get(str(item[0].id)) != "historical"]
    historical = [item for item in hits if hit_periods.get(str(item[0].id)) == "historical"]
    return current, historical


def _points(items: list[tuple[RuleEvaluation, MonitoringRule]]) -> str:
    points = "；".join(rule.monitoring_point.rstrip("。") for _, rule in items[:3])
    return points + (f"等{len(items)}项" if len(items) > 3 else "")


def _event_titles(items: list[tuple[RuleEvaluation, MonitoringRule]]) -> str:
    # 历史事项按实际事件标题列示，避免把监测要点名称写成已发生的事实。
    titles = list(dict.fromkeys(
        str(item.get("title") or "").strip().rstrip("。")
        for evaluation, _ in items
        for item in (evaluation.evidence or [])
        if str(item.get("id") or "").startswith("event-") and item.get("title")
    ))
    if not titles:
        return _points(items)
    shown = "；".join(titles[:3])
    return shown + (f"等{len(titles)}项" if len(titles) > 3 else "")


def _section_rating(
    items: list[tuple[RuleEvaluation, MonitoringRule]],
    hit_periods: dict[str, str] | None = None,
) -> dict[str, Any]:
    # 只按本期发布的事项定级；本期之前已披露、本期未见新增的事项作为背景，最高列示较低风险。
    hits, historical = _split_hits(items, hit_periods)
    gaps = [(evaluation, rule) for evaluation, rule in items if evaluation.status == "insufficient_data"]
    severities = {evaluation.severity for evaluation, _ in hits}
    important = [
        evaluation for evaluation, _ in hits
        if evaluation.severity in {"important", "high", "critical"}
    ]
    if "critical" in severities:
        level = "高风险"
    elif "high" in severities or len(important) >= 2:
        level = "较高风险"
    elif important:
        level = "中风险"
    elif hits or historical:
        level = "较低风险"
    else:
        level = "低风险"

    basis = []
    codes = [rule.code for _, rule in items]
    if codes:
        basis.append(f"对应监测范围第{_code_ranges(codes)}项。")
    if hits:
        basis.append(f"本期识别需关注事项：{_points(hits)}。")
    elif not historical:
        basis.append("已覆盖信息中暂未识别需单独提示的异常。")
    if historical:
        basis.append(
            f"另有{_event_titles(historical)}为本期之前已披露事项，本期未见新增披露，"
            "作为背景持续跟踪，不作为本期新增风险的定级依据。"
        )
    if gaps:
        basis.append(
            f"另有{len(gaps)}项所需资料未取得，现列示等级不能据此认定风险已充分排除，需补充资料后复核。"
        )

    if hits:
        summary = str(hits[0][0].rationale or hits[0][1].monitoring_point).strip()
    elif historical:
        summary = "本期未见新增披露，此前已披露事项持续跟踪。"
    elif gaps:
        summary = "所需资料尚未取得，现列示等级需补充资料后复核。"
    else:
        summary = "本期暂未发现相关风险。"
    return {
        "level": level,
        "data_pending": bool(gaps),
        "summary": summary[:120],
        "basis": "".join(basis),
        "source": "system",
        "history_only": bool(historical) and not hits,
    }


def _deterministic_ratings(
    rows: list[tuple[RuleEvaluation, MonitoringRule]],
    hit_periods: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[tuple[RuleEvaluation, MonitoringRule]]] = defaultdict(list)
    for evaluation, rule in rows:
        grouped[_section_for_rule(rule)].append((evaluation, rule))
    return {
        section: _section_rating(grouped.get(section, []), hit_periods)
        for section in RISK_SECTIONS
    }


def hit_periods(
    db: Session,
    rows: list[tuple[RuleEvaluation, MonitoringRule]],
    period: str,
    as_of_date: date,
) -> dict[str, str]:
    """命中项按事件发布日期分为本期（current）和历史（historical）。

    规则评估按截至月末的累计口径命中；月报只把本期发布的事项作为本期风险。
    以指标、文档或人工事实为证据的命中属于当期资料，视为本期。
    """

    year, month = (int(part) for part in period.split("-"))
    start = date(year, month, 1)
    end = min(date(year, month, calendar.monthrange(year, month)[1]), as_of_date)
    event_ids: dict[str, list[uuid.UUID]] = {}
    for evaluation, _ in rows:
        if evaluation.status != "hit":
            continue
        items = evaluation.evidence or []
        if not items or any(not str(item.get("id") or "").startswith("event-") for item in items):
            continue
        ids = []
        for item in items:
            match = EVIDENCE_UUID.search(str(item.get("id")))
            if match:
                ids.append(uuid.UUID(match.group(0)))
        event_ids[str(evaluation.id)] = ids
    wanted = {value for values in event_ids.values() for value in values}
    published = {
        event.id: _published_on(event)
        for event in (
            db.execute(select(RiskEvent).where(RiskEvent.id.in_(wanted))).scalars()
            if wanted
            else []
        )
    }
    result = {}
    for evaluation, _ in rows:
        if evaluation.status != "hit":
            continue
        key = str(evaluation.id)
        if key not in event_ids:
            result[key] = "current"
            continue
        dates = [published[value] for value in event_ids[key] if value in published]
        result[key] = "current" if not dates or any(start <= day <= end for day in dates) else "historical"
    return result


def _rating_boundary(
    period: str,
    as_of_date: date,
    ratings: dict[str, dict[str, Any]],
    entity_count: int = 0,
) -> str:
    year, month = (int(part) for part in period.split("-"))
    closed = as_of_date.day >= calendar.monthrange(as_of_date.year, as_of_date.month)[1]
    scope = f"期间：{year}年{month}月{'全月' if closed else '（月度进行中）'}；公开资料核对截至{as_of_date.isoformat()}。"
    coverage = (
        f"本次评级覆盖被投企业本体及已确认的{entity_count}个合作方、关联方，仅依据本期已取得资料。"
        if entity_count
        else "本次评级仅覆盖本期已取得资料和已纳入监测的主体。"
    )
    pending = [SECTION_LABELS[key] for key in RISK_SECTIONS if (ratings.get(key) or {}).get("data_pending")]
    limits = (
        f"{'、'.join(pending)}资料不足，相关等级均需补充资料后复核，不代表已完成实质验证。"
        if pending
        else ""
    )
    return (
        scope
        + coverage
        + limits
        + f"司法及舆情按{month}月信息发布日期纳入，历史事项{month}月再次披露的可纳入并去重；"
        "事件日、开庭日及监测日不替代发布日期。"
        f"市场部分次月发布的{month}月统计单独标注，待核线索不作为已确认本期事实或定量损失依据。"
    )


def report_ratings(report: MonthlyReport) -> dict[str, dict[str, Any]]:
    return dict((report.evidence_snapshot or {}).get("ratings") or {})


def rating_display(rating: dict[str, Any]) -> str:
    level = str(rating.get("level") or "")
    return f"{level}（资料待补）" if level and rating.get("data_pending") else level


ENTITY_GROUPS = ("本体", "合作方", "关联方")
LEGAL_EVENT_CATEGORIES = {"legal", "监管处罚", "法律风险", "合规风险"}
LEGAL_EVENT_TOKENS = ("诉讼", "仲裁", "被执行", "失信", "处罚", "开庭", "裁判", "立案", "纠纷", "罚款", "罚没")
SENTIMENT_EVENT_CATEGORIES = {"brand", "品牌舆情", "舆情"}
SENTIMENT_EVENT_TOKENS = ("舆情", "投诉", "热搜", "负面", "曝光")
BLOCK_HEADING = re.compile(r"^【([^】]+)】\s*$")
NO_DATA_TEXT = {
    "operations": "本期暂无相关数据支持。",
    "finance": "本期暂无相关数据支持。",
    "internal_control": "本期暂无相关信息支持。",
}


def _event_kinds(event: RiskEvent) -> list[str]:
    text = f"{event.category} {event.title}"
    kinds = []
    if event.category in LEGAL_EVENT_CATEGORIES or any(token in text for token in LEGAL_EVENT_TOKENS):
        kinds.append("legal")
    if (
        event.category in SENTIMENT_EVENT_CATEGORIES
        or event.sentiment == "negative"
        or any(token in text for token in SENTIMENT_EVENT_TOKENS)
    ):
        kinds.append("sentiment")
    return kinds


def _published_on(event: RiskEvent) -> date:
    # 司法及舆情按信息发布日期纳入；事件日、开庭日不替代发布日期。
    payload = event.extra_payload or {}
    for key in ("published_at", "publish_date"):
        try:
            return date.fromisoformat(str(payload.get(key) or "")[:10])
        except ValueError:
            continue
    value = event.occurred_at or event.created_at
    return value.date() if value else date.min


def monitored_entities(
    db: Session, company: Company, period: str, as_of_date: date
) -> list[dict[str, Any]]:
    """本体及已确认合作方、关联方在本期按发布日期纳入的法律、舆情事项。"""

    scope = official_scope(company.name) or {}
    entities = confirmed_entities(db, company)
    names = [entity.name for entity in entities]
    companies = (
        {item.name: item for item in db.execute(select(Company).where(Company.name.in_(names))).scalars()}
        if names
        else {}
    )
    members: list[tuple[dict[str, Any], Any]] = [
        (
            {
                "group": "本体",
                "name": company.name,
                "relation": "监测主体本体",
                "sentiment_group": scope.get("self_sentiment_group") or "本体",
            },
            company.id,
        )
    ]
    for entity in entities:
        metadata = entity.metadata_payload or {}
        label = RELATION_LABELS.get(entity.relation_type, entity.relation_type)
        members.append(
            (
                {
                    "group": "合作方" if entity.relation_type in PARTNER_RELATIONS else "关联方",
                    "name": entity.name,
                    "relation": metadata.get("relation_note") or label,
                    "sentiment_group": metadata.get("sentiment_group") or "合作方及关联方",
                },
                companies[entity.name].id if entity.name in companies else None,
            )
        )

    year, month = (int(part) for part in period.split("-"))
    start = date(year, month, 1)
    end = min(date(year, month, calendar.monthrange(year, month)[1]), as_of_date)
    result = []
    for member, company_id in members:
        events: list[dict[str, Any]] = []
        seen: set[str] = set()
        rows = (
            db.execute(
                select(RiskEvent)
                .where(RiskEvent.company_id == company_id)
                .order_by(RiskEvent.occurred_at.desc().nullslast(), RiskEvent.created_at.desc())
                .limit(200)
            ).scalars()
            if company_id
            else []
        )
        for event in rows:
            published = _published_on(event)
            kinds = _event_kinds(event)
            # 历史事项本期再次披露的按标题去重，只保留最近一次披露。
            key = re.sub(r"\s+", "", event.title)
            if not kinds or not start <= published <= end or key in seen:
                continue
            seen.add(key)
            events.append(
                {
                    "evidence_id": f"risk-event-{event.id}",
                    "kinds": kinds,
                    "period": published.isoformat(),
                    "label": event.title,
                    "summary": str(event.content or "")[:300],
                    "severity": event.severity,
                    "sentiment": event.sentiment,
                    "source_name": event.source_name,
                    "source_url": event.source_url,
                }
            )
        member["events"] = sorted(events, key=lambda item: item["period"])
        result.append(member)
    return result


def _kind_events(member: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in member.get("events", []) if kind in item["kinds"]]


def _event_lines(events: list[dict[str, Any]], with_entity: str = "") -> list[str]:
    lines = []
    for index, item in enumerate(events, start=1):
        summary = str(item.get("summary") or "").strip()
        source = f"（来源：{item['source_name']}）" if item.get("source_name") else ""
        prefix = f"{with_entity}：" if with_entity else ""
        label = item["label"].rstrip("。")
        # 正文已以标题开头时不再重复标题。
        text = summary if summary.startswith(label) else f"{label}。{summary}"
        lines.append(f"{index}. {prefix}{item['period']}，{text[:240]}{source}")
    return lines


def _legal_section(
    company: Company, members: list[dict[str, Any]], rating: dict[str, Any]
) -> str:
    blocks = []
    flagged = []
    for group in ENTITY_GROUPS:
        # 组内有本期事项的主体排在前面，其余按监测实体清单顺序。
        group_members = sorted(
            (item for item in members if item["group"] == group),
            key=lambda item: not _kind_events(item, "legal"),
        )
        for member in group_members:
            events = _kind_events(member, "legal")
            if events:
                flagged.append(member["name"])
                impact = (
                    "相关事项直接涉及本公司，需核实案件进展、涉案金额及对经营的实际影响。"
                    if group == "本体"
                    else f"需结合案件进展及与{company.name}的业务联系持续核实，暂不直接等同于{company.name}承担同等责任。"
                )
                body = [
                    f"总结：监测期内，{member['name']}新增{len(events)}条法律相关信息，需关注责任认定和潜在风险传导。",
                    *_event_lines(events),
                    f"综合判断：{impact}",
                ]
            elif group == "本体":
                body = [f"监测期内，{member['name']}自身未新增重大诉讼、执行及失信事项，未监测到相关法律风险。"]
            else:
                body = ["本期暂未发现相关风险。"]
            blocks.append(f"【{group}｜{member['name']}】\n" + "\n".join(body))
    judgment = f"综合评定为{rating_display(rating) or '未评级'}。"
    if flagged:
        judgment += f"本期法律相关信息主要涉及{'、'.join(flagged)}。"
        if any(name != company.name for name in flagged):
            judgment += f"合作方、关联方事项不直接等同于{company.name}承担同等责任，需持续跟踪传导路径。"
    else:
        judgment += "本期已纳入监测的主体均未新增法律相关信息。"
    blocks.append("【法律风险综合判断】\n" + judgment)
    return "\n\n".join(blocks)


def _sentiment_groups(company: Company, members: list[dict[str, Any]]) -> list[dict[str, str]]:
    scope = official_scope(company.name) or {}
    groups = list(scope.get("sentiment_groups") or [])
    labels = {item["label"] for item in groups}
    for member in members:
        if member["sentiment_group"] not in labels:
            labels.add(member["sentiment_group"])
            groups.append({"label": member["sentiment_group"], "focus": ""})
    return groups


def _sentiment_section(
    company: Company, members: list[dict[str, Any]], rating: dict[str, Any]
) -> str:
    blocks = []
    flagged = []
    for group in _sentiment_groups(company, members):
        label = group["label"]
        group_members = [item for item in members if item["sentiment_group"] == label]
        if not group_members:
            continue
        names = "、".join(item["name"] for item in group_members)
        focus = str(group.get("focus") or "")
        hit_members = [item for item in group_members if _kind_events(item, "sentiment")]
        if hit_members:
            flagged.append(label)
            lines = []
            for item in hit_members:
                lines.extend(_event_lines(_kind_events(item, "sentiment"), with_entity=item["name"]))
            count = sum(len(_kind_events(item, "sentiment")) for item in hit_members)
            body = [
                f"总结：监测期内{label}涉及{count}条负面或敏感信息，主要集中于"
                f"{'、'.join(item['name'] for item in hit_members)}，需关注向{company.name}的传导。",
                *([focus] if focus else []),
                *lines,
                "综合判断：司法线索不自动作为舆情事实，实际传播、投诉及业务影响仍需核实。",
            ]
        else:
            body = [f"总结：对{names}的公开信息进行监测，{focus}本期暂未监测到相关风险信息。"]
        blocks.append(f"【{label}】\n" + "\n".join(body))
    judgment = f"综合评定为{rating_display(rating) or '未评级'}。"
    judgment += (
        f"本期负面或敏感信息集中于{'、'.join(flagged)}，"
        "但尚需核实是否形成集中投诉、大范围负面传播或重大经营损失。"
        if flagged
        else "本期已纳入监测的主体均未监测到负面舆情。"
    )
    blocks.append("【舆情风险综合判断】\n" + judgment)
    return "\n\n".join(blocks)


def split_entity_blocks(text: str) -> list[tuple[str, str]]:
    """把“【分组｜主体】”标题行拆成导出用的分行内容；没有标题行时返回空列表。"""

    blocks: list[tuple[str, list[str]]] = []
    for line in str(text or "").split("\n"):
        match = BLOCK_HEADING.match(line.strip())
        if match:
            blocks.append((match.group(1), []))
        elif blocks:
            blocks[-1][1].append(line)
        elif line.strip():
            blocks.append(("", [line]))
    if not any(heading for heading, _ in blocks):
        return []
    return [(heading, "\n".join(lines).strip()) for heading, lines in blocks]


def _block_headings(text: str) -> list[str]:
    return [heading for heading, _ in split_entity_blocks(text)]


def _apply_entity_signals(
    ratings: dict[str, dict[str, Any]], members: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    # 合作方、关联方本期出现法律或舆情事项时，至少按“间接信号”列为较低风险。
    for key, kind in (("legal", "legal"), ("sentiment", "sentiment")):
        names = [item["name"] for item in members if _kind_events(item, kind)]
        rating = ratings.get(key)
        if not names or not rating or rating["level"] != "低风险":
            continue
        ratings[key] = {
            **rating,
            "level": "较低风险",
            "summary": f"{'、'.join(names[:3])}本期出现相关事项，影响尚待核实。"[:120],
            "basis": rating["basis"]
            + f"本期{'、'.join(names)}出现按发布日期纳入的相关事项，符合间接信号、直接影响尚未证实的较低风险特征。",
        }
    return ratings


def _history_paragraph(items: list[tuple[RuleEvaluation, MonitoringRule]]) -> str:
    return (
        f"此前已披露、本期未见新增的事项（持续跟踪，不作为本期新增风险）：{_event_titles(items)}。"
    )


def _deterministic_sections(
    company: Company,
    period: str,
    rows: list[tuple[RuleEvaluation, MonitoringRule]],
    monthly_metrics: list[dict[str, Any]],
    macro_context: list[dict[str, Any]],
    as_of_date: date,
    hit_periods: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], dict[str, list[str]]]:
    grouped: dict[str, list[tuple[RuleEvaluation, MonitoringRule]]] = defaultdict(list)
    evidence_by_section: dict[str, list[str]] = defaultdict(list)
    for evaluation, rule in rows:
        section = _section_for_rule(rule)
        grouped[section].append((evaluation, rule))
        evidence_by_section[section].extend(
            str(item.get("id"))
            for item in (evaluation.evidence or [])
            if item.get("id")
        )

    hits, historical = _split_hits(rows, hit_periods)
    gaps = [
        (evaluation, rule)
        for evaluation, rule in rows
        if evaluation.status == "insufficient_data"
    ]
    important = [item for item in hits if item[0].severity in {"important", "high", "critical"}]
    summary = (
        f"{company.name}{period}投后监测（截至{as_of_date.isoformat()}）已完成{len(rows)}项监测要点核验，"
        f"本期发现{len(hits)}项需关注事项（其中重要事项{len(important)}项）"
        + (f"，另有{len(historical)}项为此前已披露、本期未见新增的事项，持续跟踪" if historical else "")
        + f"；另有{len(gaps)}项因资料不足，待补充材料后进一步核验。"
    )
    sections: dict[str, str] = {}
    sections["risk_overview"] = summary
    if hits:
        actions = []
        for index, (evaluation, rule) in enumerate(hits[:6], start=1):
            actions.append(
                f"{index}. 针对“{rule.monitoring_point}”核验最新进展，"
                f"明确责任人与完成时点，落实风险缓释措施。"
            )
        sections["actions"] = " ".join(actions)
    elif historical:
        sections["actions"] = (
            f"1. 持续跟踪{_event_titles(historical)}的后续进展，关注是否出现新的披露或处置结果。"
            + (" 2. 补充本期财务、经营及治理资料，完成相关事项的复核。" if gaps else "")
        )
    elif gaps:
        sections["actions"] = "1. 补充本期财务、经营及治理资料，完成相关事项的复核。"
    else:
        sections["actions"] = "持续按月更新公开信息和公司经营材料。"

    for section in SECTION_ORDER[2:]:
        items = grouped.get(section, [])
        section_hits, section_history = _split_hits(items, hit_periods)
        section_gaps = [
            (evaluation, rule)
            for evaluation, rule in items
            if evaluation.status == "insufficient_data"
        ]
        if section_hits:
            paragraphs = [
                f"本期该板块发现{len(section_hits)}项需关注事项。"
            ]
            for evaluation, rule in section_hits:
                paragraphs.append(
                    f"{rule.monitoring_point}：{evaluation.rationale}"
                )
            if section_history:
                paragraphs.append(_history_paragraph(section_history))
            if section_gaps:
                paragraphs.append(
                    f"另有{len(section_gaps)}项监测事项因资料不足暂缓结论，需继续补充原始材料。"
                )
            sections[section] = "\n\n".join(paragraphs)
        elif section_history:
            sections[section] = "本期未见新增披露。\n\n" + _history_paragraph(section_history)
        elif section_gaps:
            sections[section] = NO_DATA_TEXT.get(section) or (
                f"本期该板块所需监测材料尚未齐备，共有{len(section_gaps)}项监测事项暂缓结论，"
                "不能据此认定不存在风险，待补充原始材料后进一步核验。"
            )
        else:
            sections[section] = "本期暂未发现相关风险。"
    metric_summary = _metric_sentence(monthly_metrics)
    if metric_summary:
        sections["finance"] = (
            metric_summary
            if sections["finance"] == NO_DATA_TEXT["finance"]
            else metric_summary + "\n\n" + sections["finance"]
        )
    macro_summary = _macro_sentence(macro_context, as_of_date)
    sections["external"] = macro_summary + "\n\n" + sections["external"]
    evidence_by_section["external"].extend(
        str(item["evidence_id"])
        for item in macro_context
        if item.get("evidence_id")
    )
    return summary, sections, {
        key: list(dict.fromkeys(values)) for key, values in evidence_by_section.items()
    }


def _previous_report(db: Session, report: MonthlyReport) -> MonthlyReport | None:
    return db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.company_id == report.company_id)
        .where(MonthlyReport.status == "approved")
        .where(MonthlyReport.period < report.period)
        .order_by(MonthlyReport.period.desc(), MonthlyReport.version.desc())
        .limit(1)
    ).scalar_one_or_none()


PERIOD_CURRENT = "本期"
PERIOD_HISTORICAL = "历史（本期之前已披露，本期未见新增）"

EVIDENCE_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)


def _evidence_key(value: Any) -> str:
    # 规则证据写作 event-<id>，提示中的事件写作 risk-event-<id>；按记录编号比对，忽略前缀写法。
    text = str(value)
    match = EVIDENCE_UUID.search(text)
    return match.group(0).replace("-", "").lower() if match else text


def _llm_sections(
    db: Session,
    *,
    report: MonthlyReport,
    company: Company,
    rows: list[tuple[RuleEvaluation, MonitoringRule]],
    base_summary: str,
    base_sections: dict[str, str],
    evidence_by_section: dict[str, list[str]],
    monthly_metrics: list[dict[str, Any]],
    macro_context: list[dict[str, Any]],
    as_of_date: date,
    base_ratings: dict[str, dict[str, Any]],
    members: list[dict[str, Any]],
    hit_periods: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], dict[str, dict[str, Any]], dict[str, Any], str]:
    fallback = (base_summary, base_sections, base_ratings, {}, "deterministic")
    service = LLMService()
    if not service.is_configured:
        return fallback
    previous = _previous_report(db, report)
    hits = [
        {
            "rule_code": rule.code,
            "section": _section_for_rule(rule),
            "period_status": (
                PERIOD_HISTORICAL
                if (hit_periods or {}).get(str(evaluation.id)) == "historical"
                else PERIOD_CURRENT
            ),
            "monitoring_point": rule.monitoring_point,
            "assessment_standard": rule.assessment_standard,
            "severity": evaluation.severity,
            "rationale": evaluation.rationale,
            "evidence_ids": [
                item.get("id") for item in (evaluation.evidence or []) if item.get("id")
            ],
            "facts": evaluation.facts or [],
        }
        for evaluation, rule in rows
        if evaluation.status == "hit"
    ]
    gap_counts: dict[str, int] = defaultdict(int)
    for evaluation, rule in rows:
        if evaluation.status == "insufficient_data":
            gap_counts[_section_for_rule(rule)] += 1
    previous_style = {
        key: str((previous.sections or {}).get(key) or "")[:1200]
        for key in SECTION_ORDER
        if previous and (previous.sections or {}).get(key)
    }
    valid_evidence = {
        value for values in evidence_by_section.values() for value in values
    }
    # 提示中提供给模型的证据（宏观指标、行业与公司事件、各主体事项）均可被引用。
    valid_evidence.update(
        str(item["evidence_id"])
        for item in [
            *monthly_metrics,
            *macro_context,
            *(event for member in members for event in member.get("events", [])),
        ]
        if item.get("evidence_id")
    )
    valid_keys = {_evidence_key(value) for value in valid_evidence}

    def fallback_for(reason: str):
        # 退回确定性草稿时记录原因，便于在报告的模型用量里排查。
        return (base_summary, base_sections, base_ratings, {"fallback_reason": reason[:300]}, "deterministic")

    request = dict(
            purpose=f"monthly-report:{company.id}:{report.period}",
            system_prompt=(
                "你是投后风险管理月报撰写助手，最终读者是投资机构管理层，输出将直接进入正式风险管理台账。"
                "文风模仿风险管理台账月报：结论先行，先用一小段概括本月整体特征与较上期变化；"
                "各板块用连贯段落陈述事实与判断，需要评估影响时以“风险分析：”引出；"
                "没有发现风险时直接写“本期暂未发现相关风险”。"
                "严禁在正文中出现“规则”“命中N项”“监测要点”“规则编号”“insufficient_data”等内部评估术语，"
                "也不要罗列评估数量，应把评估结论转化为对公司经营、财务或合规状况的具体影响描述。"
                "external（外部风险）板块按“宏观经济趋势：”“行业监管政策：”“市场行业格局：”“头部机构动态：”“风险分析：”五段组织："
                "宏观经济趋势引用 official_macro_context 中 kind 为 indicator 的最新指标并写明期间；"
                "行业监管政策写 dimension 为行业监管态势（或 policy_regulatory）的本期事件；"
                "市场行业格局写 dimension 为市场行业格局的统计数据，按跨境客流、目的地分化、消费结构、汇率变化等子项列明数值、同比和发布日期，"
                "假期口径或次月发布的数据要注明口径；没有数据的子项不写；"
                "头部机构动态按机构逐一概括 dimension 为头部机构动态的事件，本月无重大动态的机构写明“本月未见重大公开动态”；"
                "风险分析写上述因素对被投企业的传导路径和影响程度。"
                "legal 与 sentiment 板块按主体分段：必须原样保留 base_sections 中每一行“【…】”标题及其顺序，"
                "不得增删或改写标题；每段以“总结：”开头概括结论，有事项时按“涉诉特征/风险分类/后续管控措施”"
                "或“传导判断/重点跟踪方向/业务缓释措施”分层展开，并写明与被投企业的业务联系；"
                "最后一个综合判断段以“综合评定为X。”开头，X 与 ratings 中对应板块等级一致。"
                "只能使用 monitored_entities 中各主体的本期事项，不得把合作方、关联方事项写成被投企业自身事项。"
                "triggered_rules 与 company_risk_events 中 period_status 为“历史”的事项是本期之前已披露、本期未见新增的事项："
                "只能作为背景写成“此前已披露、本期未见新增，持续跟踪”，不得写成本月或本期发生，不得放入摘要的本月重点；"
                "仅由历史事项支撑的板块评级不得高于较低风险。"
                + STYLE_GUIDE
                + "只能使用输入事实与评估结论，不得创造数字、案件或来源；数据不足必须明确说明，不得推断。"
                "ratings 对 external、operations、finance、governance、internal_control、legal、brand、sentiment、other "
                "九个板块逐一评级，每项包含 level（只能是低风险、较低风险、中风险、较高风险、高风险之一）、"
                "summary（风险概况简评，不超过60字）和 basis（具体依据及评级原因，写明对应事项、与公司的业务联系、"
                "影响及缓释情况和资料限制）。评级标准："
                + "；".join(f"{level}：{definition}{criteria}" for level, definition, criteria in RATING_DEFINITIONS)
                + RATING_NOTE
                + "base_ratings 是按评估结论给出的参考等级，可依据事实调整，但资料待补的板块必须在 basis 中写明待补资料。"
                "返回JSON对象：{summary:string,sections:{risk_overview,actions,external,operations,finance,"
                "governance,internal_control,legal,brand,sentiment,other},"
                "ratings:{external:{level,summary,basis},...},evidence_ids_by_section:{...}}。"
                "正文中不要声称本报告已获审批。"
            ),
            user_payload={
                "company": company.name,
                "period": report.period,
                "report_as_of_date": as_of_date.isoformat(),
                "base_summary": base_summary,
                "base_sections": base_sections,
                "triggered_rules": hits,
                "insufficient_data_counts": dict(gap_counts),
                "confirmed_monthly_metrics": monthly_metrics,
                "official_macro_context": [
                    item for item in macro_context if item.get("kind") != "company_event"
                ],
                "company_risk_events": [
                    item for item in macro_context if item.get("kind") == "company_event"
                ],
                "previous_approved_style_examples": previous_style,
                "monitored_entities": members,
                "base_ratings": {
                    key: {
                        "level": value["level"],
                        "data_pending": value["data_pending"],
                        "history_only": value.get("history_only", False),
                        "basis": value["basis"],
                    }
                    for key, value in base_ratings.items()
                },
            },
            max_output_tokens=8000,
            # 月报输出较长，放宽超时并在失败时重试一次。
            timeout=180,
        )
    result, error = None, ""
    for _ in range(2):
        try:
            result = service.complete_json(db, **request)
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    if not result:
        return fallback_for(error or "模型未返回结果")
    payload = result.payload
    proposed = payload.get("sections")
    cited = payload.get("evidence_ids_by_section") or {}
    if not isinstance(proposed, dict) or any(
        not isinstance(proposed.get(key), str) for key in SECTION_ORDER
    ):
        return fallback_for("模型返回的板块不完整")
    for values in cited.values() if isinstance(cited, dict) else []:
        if not isinstance(values, list):
            return fallback_for("模型返回的证据编号格式不正确")
        unknown = [str(value) for value in values if _evidence_key(value) not in valid_keys]
        if unknown:
            return fallback_for(f"模型引用了未提供的证据编号：{'、'.join(unknown[:3])}")
    summary = str(payload.get("summary") or base_summary)
    sections = {key: str(proposed[key])[:20_000] for key in SECTION_ORDER}
    for key in ("legal", "sentiment"):
        # 按主体分段的标题行用于导出分行，模型改动了标题时退回确定性文本。
        if _block_headings(sections[key]) != _block_headings(base_sections[key]):
            sections[key] = base_sections[key]
    proposed_ratings = payload.get("ratings") if isinstance(payload.get("ratings"), dict) else {}
    ratings: dict[str, dict[str, Any]] = {}
    for key, base in base_ratings.items():
        item = proposed_ratings.get(key)
        if (
            not isinstance(item, dict)
            or item.get("level") not in RATING_LEVELS
            or not isinstance(item.get("basis"), str)
            or not item["basis"].strip()
        ):
            ratings[key] = base
            continue
        level = item["level"]
        if base.get("history_only") and RATING_LEVELS.index(level) > RATING_LEVELS.index("较低风险"):
            # 只有历史事项的板块，模型初评同样不超过较低风险。
            level = "较低风险"
        # 资料待补标记只来自评估结果，模型不能把缺资料的板块改成已验证。
        ratings[key] = {
            "level": level,
            "data_pending": base["data_pending"],
            "summary": str(item.get("summary") or base["summary"])[:120],
            "basis": item["basis"][:4000],
            "source": "llm",
            "history_only": base.get("history_only", False),
        }
    return summary, sections, ratings, result.token_usage, result.model_name


def generate_monthly_report(db: Session, report_id: Any) -> MonthlyReport:
    report = db.get(MonthlyReport, report_id)
    if not report:
        raise ValueError("月报不存在。")
    company = db.get(Company, report.company_id)
    if not company:
        raise ValueError("公司不存在。")
    run = db.get(AnalysisRun, report.analysis_run_id) if report.analysis_run_id else None
    if not run or run.status != "completed":
        raise ValueError("请选择已完成的实时分析任务生成月报。")
    rows = _evaluations(db, run.id)
    if not rows:
        raise ValueError("分析任务尚无规则评估结果。")
    monthly_metrics = _monthly_metric_context(db, report)
    macro_context = _macro_context(db, report.period, company_id=report.company_id)
    as_of_date = _report_as_of_date(report.period)
    periods = hit_periods(db, rows, report.period, as_of_date)
    summary, sections, evidence = _deterministic_sections(
        company,
        report.period,
        rows,
        monthly_metrics,
        macro_context,
        as_of_date,
        periods,
    )
    members = monitored_entities(db, company, report.period, as_of_date)
    base_ratings = _apply_entity_signals(_deterministic_ratings(rows, periods), members)
    if any(item.get("kind") == "event" and item.get("in_period") for item in macro_context):
        # 外部风险有本期发布的行业、监管事件时，不按“仅历史事项”限制模型定级。
        base_ratings["external"] = {**base_ratings["external"], "history_only": False}
    sections["legal"] = _legal_section(company, members, base_ratings["legal"])
    sections["sentiment"] = _sentiment_section(company, members, base_ratings["sentiment"])
    for key in ("legal", "sentiment"):
        evidence.setdefault(key, [])
        evidence[key].extend(
            item["evidence_id"]
            for member in members
            for item in _kind_events(member, key)
            if item["evidence_id"] not in evidence[key]
        )
    evidence.setdefault("finance", [])
    evidence["finance"].extend(
        str(metric["evidence_id"])
        for metric in monthly_metrics
        if metric.get("evidence_id")
    )
    summary, sections, ratings, token_usage, model_name = _llm_sections(
        db,
        report=report,
        company=company,
        rows=rows,
        base_summary=summary,
        base_sections=sections,
        evidence_by_section=evidence,
        monthly_metrics=monthly_metrics,
        macro_context=macro_context,
        as_of_date=as_of_date,
        base_ratings=base_ratings,
        members=members,
        hit_periods=periods,
    )
    # 模型未改写或改写被退回时，综合判断里的等级跟随最终评级。
    if sections["legal"] == _legal_section(company, members, base_ratings["legal"]):
        sections["legal"] = _legal_section(company, members, ratings["legal"])
    if sections["sentiment"] == _sentiment_section(company, members, base_ratings["sentiment"]):
        sections["sentiment"] = _sentiment_section(company, members, ratings["sentiment"])
    report.summary = summary
    report.sections = sections
    report.evidence_snapshot = {
        "ratings": ratings,
        "rating_boundary": _rating_boundary(
            report.period, as_of_date, ratings, entity_count=len(members) - 1
        ),
        "by_section": evidence,
        "monitored_entities": members,
        "monthly_metrics": monthly_metrics,
        "macro_context": macro_context,
        "as_of_date": as_of_date.isoformat(),
        "period_status": "month_to_date" if as_of_date.day < calendar.monthrange(as_of_date.year, as_of_date.month)[1] else "closed_month",
        "rule_evaluation_ids": [str(evaluation.id) for evaluation, _ in rows],
        "hit_periods": periods,
    }
    report.status = "draft"
    report.model_name = model_name
    report.token_usage = token_usage
    report.generated_at = datetime.now(timezone.utc)
    db.add(report)
    db.flush()
    db.add(
        ReportVersion(
            report_id=report.id,
            revision=1,
            snapshot={
                "title": report.title,
                "summary": report.summary,
                "sections": report.sections,
                "evidence_snapshot": report.evidence_snapshot,
                "status": report.status,
            },
            edited_by=report.created_by,
        )
    )
    db.commit()
    db.refresh(report)
    return report


def append_report_revision(db: Session, report: MonthlyReport, actor: str) -> int:
    revision = (
        db.execute(
            select(func.max(ReportVersion.revision)).where(
                ReportVersion.report_id == report.id
            )
        ).scalar_one()
        or 0
    ) + 1
    db.add(
        ReportVersion(
            report_id=report.id,
            revision=revision,
            snapshot={
                "title": report.title,
                "summary": report.summary,
                "sections": report.sections,
                "evidence_snapshot": report.evidence_snapshot,
                "status": report.status,
            },
            edited_by=actor,
        )
    )
    return revision


LEVEL_FILLS = {
    "高风险": ("C0392B", "FFFFFF"),
    "较高风险": ("E67E22", "FFFFFF"),
    "中风险": ("F1C232", "000000"),
    "较低风险": ("9BC53D", "000000"),
    "低风险": ("2E7D32", "FFFFFF"),
}

STATUS_LABELS = {"draft": "草稿", "in_review": "审阅中", "approved": "已审批"}

RELATION_LABELS = {
    "shareholder": "股东方",
    "subsidiary": "子公司",
    "partner": "合作方",
    "customer": "重要客户",
    "guarantor": "担保机构",
    "penetrating_shareholder": "穿透股东方",
    "other": "其他关联方",
}

SCOPE_FOOTNOTE = (
    "注：标注为“强制”的要点原则上必须监测（确有合理理由无法监测的除外）；"
    "标注为“自选”的要点可结合参股投资情况自行选择是否监测。"
)


def _row_height(text: Any, width: float, minimum: float = 20) -> float:
    # 中文字符约占两个列宽单位，按换行和列宽估算行数。
    lines = sum(
        max(1, -(-len(line) * 2 // max(int(width), 1)))
        for line in str(text or "").split("\n")
    )
    return min(409, max(minimum, lines * 15 + 6))


class _SheetWriter:
    def __init__(self, sheet: Any) -> None:
        from openpyxl.styles import Border, Side

        self.sheet = sheet
        thin = Side(style="thin", color="A6A6A6")
        self.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        sheet.sheet_view.showGridLines = False

    def put(
        self,
        row: int,
        col: int,
        value: Any,
        *,
        end_col: int | None = None,
        end_row: int | None = None,
        bold: bool = False,
        size: int = 10,
        fill: str = "",
        color: str = "000000",
        align: str = "left",
    ) -> None:
        from openpyxl.styles import Alignment, Font, PatternFill

        last_row = end_row or row
        last_col = end_col or col
        if last_row != row or last_col != col:
            self.sheet.merge_cells(
                start_row=row, start_column=col, end_row=last_row, end_column=last_col
            )
        for r in range(row, last_row + 1):
            for c in range(col, last_col + 1):
                cell = self.sheet.cell(r, c)
                cell.border = self.border
                if fill:
                    cell.fill = PatternFill("solid", fgColor=fill)
        cell = self.sheet.cell(row, col, value)
        cell.font = Font(name="Arial", size=size, bold=bold, color=color)
        cell.alignment = Alignment(
            horizontal=align, vertical="center" if align == "center" else "top", wrap_text=True
        )

    def header(self, row: int, values: list[str]) -> None:
        for col, value in enumerate(values, start=1):
            self.put(row, col, value, bold=True, fill="17365D", color="FFFFFF", align="center")

    def widths(self, values: list[float]) -> None:
        from openpyxl.utils import get_column_letter

        for index, width in enumerate(values, start=1):
            self.sheet.column_dimensions[get_column_letter(index)].width = width


def _write_report_sheet(
    sheet: Any, report: MonthlyReport, company: Company | None
) -> None:
    writer = _SheetWriter(sheet)
    snapshot = report.evidence_snapshot or {}
    ratings = report_ratings(report)
    sections = report.sections or {}
    columns = len(RISK_SECTIONS) + 1
    writer.widths([14] + [17] * len(RISK_SECTIONS))
    body_width = 17 * len(RISK_SECTIONS)

    writer.put(1, 1, report.title, end_col=columns, bold=True, size=16, fill="17365D", color="FFFFFF", align="center")
    sheet.row_dimensions[1].height = 32
    writer.put(
        2,
        1,
        f"被投企业：{company.name if company else ''}　监测期间：{report.period}　"
        f"公开资料核对截至：{snapshot.get('as_of_date') or '未标注'}　"
        f"版本：v{report.version}　状态：{STATUS_LABELS.get(report.status, report.status)}",
        end_col=columns,
        align="center",
    )

    writer.put(3, 1, "风险概况", end_row=5, bold=True, fill="D9EAD3", align="center")
    summary_height = 20.0
    for offset, key in enumerate(RISK_SECTIONS):
        col = offset + 2
        rating = ratings.get(key) or {}
        level = str(rating.get("level") or "")
        fill, color = LEVEL_FILLS.get(level, ("EEEEEE", "000000"))
        writer.put(3, col, SECTION_LABELS[key], bold=True, fill="D9EAD3", align="center")
        writer.put(4, col, f"风险等级：{rating_display(rating) or '未评级'}", bold=True, fill=fill, color=color, align="center")
        writer.put(5, col, rating.get("summary") or "")
        summary_height = max(summary_height, _row_height(rating.get("summary"), 17))
    sheet.row_dimensions[4].height = 30
    sheet.row_dimensions[5].height = summary_height

    row = 6
    for label, text in (
        ("评分标准", RATING_SCALE_TEXT),
        ("监测摘要", report.summary),
        ("行动建议", sections.get("actions")),
        *((SECTION_LABELS[key], sections.get(key)) for key in RISK_SECTIONS),
    ):
        blocks = split_entity_blocks(str(text or ""))
        if not blocks:
            writer.put(row, 1, label, bold=True, fill="D9EAD3", align="center")
            writer.put(row, 2, str(text or ""), end_col=columns)
            sheet.row_dimensions[row].height = _row_height(text, body_width, 34)
            row += 1
            continue
        # 法律、舆情按主体分行：B 列为“分组/主体”，C 列起为该主体的正文。
        writer.put(row, 1, label, end_row=row + len(blocks) - 1, bold=True, fill="D9EAD3", align="center")
        for heading, body in blocks:
            writer.put(row, 2, heading.replace("｜", "\n"), bold=True, align="center")
            writer.put(row, 3, body, end_col=columns)
            sheet.row_dimensions[row].height = _row_height(body, body_width - 17, 34)
            row += 1
    sheet.freeze_panes = "B3"


def _write_scope_sheet(
    sheet: Any,
    db: Session,
    rows: list[tuple[RuleEvaluation, MonitoringRule]],
    company: Company | None,
) -> None:
    writer = _SheetWriter(sheet)
    writer.widths([14, 7, 50, 72, 10, 22])
    rules: list[MonitoringRule] = []
    if rows:
        rules = sorted(
            db.execute(
                select(MonitoringRule).where(
                    MonitoringRule.rule_set_id == rows[0][1].rule_set_id
                )
            ).scalars(),
            # 后补的品牌规则编号排在“其他风险”之后，表内仍按板块顺序列示。
            key=lambda rule: (rule.category == "其他风险", rule.code),
        )
    selected = {rule.id for evaluation, rule in rows if evaluation.status != "not_applicable"}
    company_name = company.name if company else "被投企业"

    writer.put(1, 1, f"{company_name}股权投资风险监测项目-监测范围及指标", end_col=6, bold=True, size=14, fill="17365D", color="FFFFFF", align="center")
    sheet.row_dimensions[1].height = 28
    writer.put(2, 1, f"监测条目共{len(rules)}项，本期纳入监测{len(selected)}项", end_col=6, align="center")
    writer.header(3, ["风险板块", "序号", "风险监测要点", "评估标准", "是否强制", company_name])

    row = 4
    start = row
    for index, rule in enumerate(rules):
        writer.put(row, 2, rule.code, align="center")
        writer.put(row, 3, rule.monitoring_point)
        writer.put(row, 4, rule.assessment_standard)
        writer.put(row, 5, rule.requirement_type, align="center")
        writer.put(row, 6, "✓" if rule.id in selected else "", bold=True, align="center")
        sheet.row_dimensions[row].height = _row_height(rule.assessment_standard, 72, 30)
        is_last = index == len(rules) - 1 or rules[index + 1].category != rule.category
        if is_last:
            writer.put(start, 1, rule.category, end_row=row, bold=True, align="center")
            start = row + 1
        row += 1
    writer.put(row, 1, SCOPE_FOOTNOTE, end_col=6)
    sheet.row_dimensions[row].height = 30
    sheet.freeze_panes = "A4"


def _write_rating_logic_sheet(sheet: Any, report: MonthlyReport) -> None:
    writer = _SheetWriter(sheet)
    writer.widths([16, 42, 80])
    ratings = report_ratings(report)

    writer.put(1, 1, "风险概况评级逻辑", end_col=3, bold=True, size=14, fill="17365D", color="FFFFFF", align="center")
    sheet.row_dimensions[1].height = 28
    writer.header(2, ["风险等级", "评级定义", "评级判定标准"])
    row = 3
    for level, definition, criteria in RATING_DEFINITIONS:
        fill, color = LEVEL_FILLS[level]
        writer.put(row, 1, level, bold=True, fill=fill, color=color, align="center")
        writer.put(row, 2, definition)
        writer.put(row, 3, criteria)
        sheet.row_dimensions[row].height = _row_height(criteria, 80, 36)
        row += 1

    row += 1
    writer.put(row, 1, "评级说明", bold=True, fill="D9EAD3", align="center")
    writer.put(row, 2, RATING_NOTE, end_col=3)
    sheet.row_dimensions[row].height = _row_height(RATING_NOTE, 122, 36)

    row += 2
    writer.put(row, 1, "本期评级依据（对应月报内容）", end_col=3, bold=True, fill="D9EAD3")
    row += 1
    writer.header(row, ["风险类别", "本期评级", "具体依据及评级原因"])
    row += 1
    for key in RISK_SECTIONS:
        rating = ratings.get(key) or {}
        level = str(rating.get("level") or "")
        fill, color = LEVEL_FILLS.get(level, ("", "000000"))
        writer.put(row, 1, SECTION_LABELS[key], bold=True, align="center")
        writer.put(row, 2, rating_display(rating) or "未评级", bold=True, fill=fill, color=color, align="center")
        writer.put(row, 3, rating.get("basis") or "")
        sheet.row_dimensions[row].height = _row_height(rating.get("basis"), 80, 36)
        row += 1

    row += 1
    boundary = str((report.evidence_snapshot or {}).get("rating_boundary") or "")
    writer.put(row, 1, "评级边界", bold=True, fill="D9EAD3", align="center")
    writer.put(row, 2, boundary, end_col=3)
    sheet.row_dimensions[row].height = _row_height(boundary, 122, 36)


def _write_entity_sheet(sheet: Any, db: Session, company: Company | None) -> None:
    writer = _SheetWriter(sheet)
    writer.widths([8, 48, 52])
    company_name = company.name if company else ""
    entities = confirmed_entities(db, company) if company else []

    def relation_text(entity: RelatedEntity) -> str:
        note = str((entity.metadata_payload or {}).get("relation_note") or "")
        if note:
            return note
        label = RELATION_LABELS.get(entity.relation_type, entity.relation_type)
        return (
            f"{company_name}{label}"
            if entity.relation_type in {"shareholder", "subsidiary", "penetrating_shareholder"}
            else label
        )

    writer.put(1, 1, "监测实体清单", end_col=3, bold=True, size=14, fill="17365D", color="FFFFFF", align="center")
    sheet.row_dimensions[1].height = 28
    writer.header(2, ["序号", "主体名称", "关联关系"])
    groups = (
        ("被投企业", [(company_name, "监测主体本体")] if company else []),
        ("合作方", [(item.name, relation_text(item)) for item in entities if item.relation_type in PARTNER_RELATIONS]),
        ("关联方", [(item.name, relation_text(item)) for item in entities if item.relation_type not in PARTNER_RELATIONS]),
    )
    row = 3
    for title, members in groups:
        writer.put(row, 1, f"{title}：", end_col=3, bold=True, fill="D9EAD3", align="center")
        row += 1
        if not members:
            writer.put(row, 1, "暂未纳入已确认主体", end_col=3, color="7F7F7F", align="center")
            row += 1
        for index, (name, relation) in enumerate(members, start=1):
            writer.put(row, 1, index, align="center")
            writer.put(row, 2, name)
            writer.put(row, 3, relation)
            row += 1


def export_monthly_report_xlsx(
    db: Session, report: MonthlyReport
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    company = db.get(Company, report.company_id)
    rows = _evaluations(db, report.analysis_run_id)
    workbook = Workbook()
    report_sheet = workbook.active
    report_sheet.title = "风险监测报告"
    _write_report_sheet(report_sheet, report, company)
    _write_scope_sheet(workbook.create_sheet("监测范围及评估标准"), db, rows, company)
    _write_rating_logic_sheet(workbook.create_sheet("风险概况评级逻辑"), report)
    _write_entity_sheet(workbook.create_sheet("监测实体"), db, company)

    evaluation_sheet = workbook.create_sheet("规则评估")
    evaluation_sheet.append(
        ["规则编号", "风险板块", "监测要点", "评估结果", "重要性", "判断理由", "证据编号"]
    )
    for cell in evaluation_sheet[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for evaluation, rule in rows:
        evaluation_sheet.append(
            [
                rule.code,
                rule.category,
                rule.monitoring_point,
                evaluation.status,
                evaluation.severity,
                evaluation.rationale,
                "、".join(
                    str(item.get("id") or "") for item in (evaluation.evidence or [])
                ),
            ]
        )
    widths = [12, 20, 58, 18, 14, 60, 38]
    for index, width in enumerate(widths, start=1):
        evaluation_sheet.column_dimensions[chr(64 + index)].width = width
    for row_cells in evaluation_sheet.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    evaluation_sheet.freeze_panes = "A2"

    evidence_sheet = workbook.create_sheet("证据索引")
    evidence_sheet.append(["报告板块", "证据编号", "指标/说明", "期间", "数值", "来源位置"])
    for cell in evidence_sheet[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="17365D")
    snapshot = report.evidence_snapshot or {}
    evidence_details = {
        str(item.get("evidence_id")): item
        for item in [
            *snapshot.get("monthly_metrics", []),
            *snapshot.get("macro_context", []),
            *(event for member in snapshot.get("monitored_entities", []) for event in member.get("events", [])),
        ]
        if item.get("evidence_id")
    }
    for section, evidence_ids in (snapshot.get("by_section") or {}).items():
        for evidence_id in evidence_ids:
            detail = evidence_details.get(str(evidence_id), {})
            value = detail.get("value_text", detail.get("value", detail.get("summary", "")))
            if detail.get("unit") and value not in (None, ""):
                value = f"{value}{detail['unit']}"
            source_locator = detail.get("source_locator", "")
            if not source_locator and detail.get("source_url"):
                source_locator = f"{detail.get('source_name') or ''} {detail['source_url']}".strip()
            evidence_sheet.append(
                [
                    SECTION_LABELS.get(section, section),
                    evidence_id,
                    detail.get("label", "规则评估证据，详见规则评估Sheet"),
                    detail.get("period", ""),
                    value,
                    source_locator,
                ]
            )
    for index, width in enumerate([20, 48, 40, 16, 22, 58], start=1):
        evidence_sheet.column_dimensions[chr(64 + index)].width = width
    evidence_sheet.freeze_panes = "A2"

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
