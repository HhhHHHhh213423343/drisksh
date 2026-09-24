from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from math import sqrt
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    AnalysisRun,
    Company,
    CompanyRuleAssignment,
    ExtractedFact,
    MonitoringRule,
    RelatedEntity,
    RiskEvent,
    RuleEvaluation,
    SourceDocument,
)
from app.services.llm import LLMService
from app.services.rule_seed import ensure_company_rule_assignments


VALID_STATUSES = {"hit", "not_hit", "insufficient_data", "not_applicable"}

EVENT_KEYWORDS: dict[int, tuple[str, ...]] = {
    1: ("货币政策", "财政政策", "税收政策", "利率", "LPR"),
    2: ("监管政策", "行业监管", "新规", "办法", "整改"),
    3: ("技术更新", "市场需求", "竞争格局", "价格战"),
    4: ("供应链", "合作终止", "交易对手", "信用风险"),
    6: ("外汇管制", "资产冻结", "主权风险", "境外"),
    7: ("自然灾害", "不可抗力", "停产", "业务中断"),
    12: ("主营业务变更", "业务转型", "行业变更"),
    13: ("主营业务停滞", "市场份额下降", "持续经营"),
    15: ("合作终止", "项目延期", "系统建设", "产品开发"),
    20: ("核心客户", "客户流失", "客户变动"),
    31: ("行业平均", "同业对标", "可比公司"),
    37: ("资产负债率", "流动比率", "速动比率", "偿债"),
    38: ("应收账款", "应收款项", "应收保理款"),
    41: ("大额借款", "高息借款", "融资成本"),
    42: ("高额举债", "高息举债", "货币资金"),
    45: ("资不抵债", "破产", "清算", "重整"),
    46: ("会计政策变更", "会计估计变更", "会计差错"),
    47: ("非标准审计意见", "保留意见", "无法表示意见", "否定意见"),
    49: ("董事变更", "高管变更", "法定代表人变更", "组织机构调整"),
    53: ("停产", "半停产", "持续经营"),
    55: ("数据泄露", "信息泄露", "生产安全", "网络安全事件"),
    56: ("股东纠纷", "管理层分歧", "控制权争议"),
    57: ("实际控制人变更", "控制权变更", "股权结构变更"),
    58: ("决议无效", "决议撤销", "董事会无法召开", "治理僵局"),
    64: ("约谈", "通报批评", "公开谴责"),
    70: ("股权冻结", "司法冻结", "强制执行"),
    71: ("行政处罚", "罚款", "资质吊销", "责令停业"),
    72: ("司法调查", "立案调查", "刑事调查"),
    73: ("诉讼", "仲裁", "开庭", "裁判文书"),
    74: ("失信被执行人", "经营异常", "限制消费", "被执行人"),
    75: ("连带保证", "对外担保", "保证责任"),
    76: ("负面舆情", "投诉", "热搜", "消费者权益", "媒体报道"),
    77: ("出借名号", "挂名经营", "品牌授权"),
    78: ("未经授权使用", "超授权范围", "商标侵权"),
}

CATEGORY_FALLBACK_KEYWORDS = {
    "外部风险": ("政策", "行业", "宏观", "市场", "供应链"),
    "业务及经营风险": ("业务", "客户", "经营", "收入", "合作"),
    "财务风险": ("财务", "利润", "现金流", "负债", "应收"),
    "公司治理风险": ("股东", "高管", "董事", "治理", "控制人"),
    "内控风险": ("内控", "整改", "个人账户", "岗位分离"),
    "法律风险": ("诉讼", "处罚", "执行", "调查", "冻结"),
    "品牌舆情风险": ("舆情", "投诉", "品牌", "负面", "商标"),
    "其他风险": (),
}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _period_key(value: str) -> tuple[int, int, str]:
    match = re.match(r"^(20\d{2})(?:-(\d{1,2}))?", value or "")
    if not match:
        return (0, 0, value or "")
    return (int(match.group(1)), int(match.group(2) or 0), value)


def _event_within_requested_period(event: RiskEvent, requested_period: str) -> bool:
    # 投后监测是截至申报月末的累计口径（本月及此前所有已知相关事件都仍然有效），
    # 不是"仅当月新增事件"——否则上月及更早的处罚、诉讼等事件会在下一次分析时被
    # 静默丢弃，导致同一家公司的结论在未换月的情况下也会出现大幅波动。
    if not requested_period:
        return True
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", requested_period)
    if not match:
        return True
    year, month = int(match.group(1)), int(match.group(2))
    end = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )
    occurred = event.occurred_at or event.created_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    return occurred < end


def _confirmed_facts(db: Session, company_id: Any, requested_period: str) -> list[ExtractedFact]:
    facts = list(
        db.execute(
            select(ExtractedFact)
            .where(ExtractedFact.company_id == company_id)
            .where(ExtractedFact.status == "confirmed")
            .order_by(ExtractedFact.created_at.asc())
        ).scalars()
    )
    if not requested_period:
        return facts
    return [
        fact
        for fact in facts
        if not fact.period or _period_key(fact.period)[:2] <= _period_key(requested_period)[:2]
    ]


def _latest_metrics(
    facts: list[ExtractedFact], requested_period: str = ""
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[ExtractedFact]] = defaultdict(list)
    for fact in facts:
        if fact.value_numeric is not None:
            grouped[fact.metric_code].append(fact)
    values: dict[str, float] = {}
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for code, metric_facts in grouped.items():
        metric_facts.sort(key=lambda item: (_period_key(item.period), item.created_at))
        current_facts = (
            [item for item in metric_facts if item.period in {"", requested_period}]
            if requested_period
            else metric_facts
        )
        if not current_facts:
            continue
        latest = current_facts[-1]
        values[code] = float(latest.value_numeric)
        evidence[code].append(
            {
                "id": f"fact-{latest.id}",
                "label": latest.label,
                "value": latest.value_numeric,
                "value_text": latest.value_text,
                "unit": latest.unit,
                "period": latest.period,
                "source_locator": latest.source_locator,
                "document_id": str(latest.document_id),
            }
        )

        latest_period = _period_key(latest.period)
        if latest_period[0] and latest_period[1]:
            prior = next(
                (
                    item
                    for item in reversed(metric_facts[:-1])
                    if _period_key(item.period)[:2]
                    == (latest_period[0] - 1, latest_period[1])
                ),
                None,
            )
            if prior and prior.value_numeric not in (None, 0):
                values[f"{code}_yoy"] = float(latest.value_numeric) / float(prior.value_numeric) - 1
                evidence[f"{code}_yoy"].extend(
                    [
                        evidence[code][0],
                        {
                            "id": f"fact-{prior.id}",
                            "label": prior.label,
                            "value": prior.value_numeric,
                            "value_text": prior.value_text,
                            "unit": prior.unit,
                            "period": prior.period,
                            "source_locator": prior.source_locator,
                            "document_id": str(prior.document_id),
                        },
                    ]
                )
    if values.get("total_assets") not in (None, 0) and "cash" in values:
        values.setdefault("cash_to_assets", values["cash"] / values["total_assets"])
        evidence["cash_to_assets"] = [
            *evidence.get("cash", []),
            *evidence.get("total_assets", []),
        ]
    return values, evidence


def _profile_metrics(
    company: Company,
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    profile = (company.company_profile or {}).get("akshare_profile") or {}
    rows = profile.get("financial_abstract") or []
    result: dict[str, float] = {}
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    aliases = {
        "revenue": ("营业总收入", "营业收入"),
        "net_profit": ("归母净利润", "净利润"),
        "net_assets": ("归母股东权益", "股东权益", "净资产"),
        "operating_cashflow": ("经营现金净流量", "经营活动产生的现金流量净额"),
    }
    for code, names in aliases.items():
        row = next(
            (
                item
                for item in rows
                if any(name in str(item.get("指标") or item.get("项目") or "") for name in names)
            ),
            None,
        )
        if not row:
            continue
        periods = sorted(
            (
                str(key),
                number,
            )
            for key, value in row.items()
            if re.fullmatch(r"20\d{6}", str(key))
            and (number := _as_number(value)) is not None
        )
        if not periods:
            continue
        latest_period, latest = periods[-1]
        result.setdefault(code, latest)
        latest_evidence = {
            "id": f"akshare-{code}-{latest_period}",
            "label": str(row.get("指标") or row.get("项目") or code),
            "value": latest,
            "period": latest_period,
            "source_name": "AkShare公开财务数据",
            "source_locator": f"company_profile.akshare_profile.financial_abstract.{latest_period}",
        }
        evidence[code].append(latest_evidence)
        prior_key = f"{int(latest_period[:4]) - 1}{latest_period[4:]}"
        prior = _as_number(row.get(prior_key))
        if prior not in (None, 0):
            result.setdefault(f"{code}_yoy", latest / prior - 1)
            evidence[f"{code}_yoy"].extend(
                [
                    latest_evidence,
                    {
                        "id": f"akshare-{code}-{prior_key}",
                        "label": str(row.get("指标") or row.get("项目") or code),
                        "value": prior,
                        "period": prior_key,
                        "source_name": "AkShare公开财务数据",
                        "source_locator": f"company_profile.akshare_profile.financial_abstract.{prior_key}",
                    },
                ]
            )
    return result, evidence


def _compare(actual: float, op: str, expected: float) -> bool:
    return {
        "lt": actual < expected,
        "lte": actual <= expected,
        "gt": actual > expected,
        "gte": actual >= expected,
        "eq": actual == expected,
    }.get(op, False)


def _condition_result(
    condition: dict[str, Any],
    metrics: dict[str, float],
    metric_evidence: dict[str, list[dict[str, Any]]],
) -> tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not condition:
        return "insufficient_data", "该规则没有可直接计算的结构化条件。", [], []
    groups = [group for group in ("all", "any") if condition.get(group)]
    if not groups:
        return "insufficient_data", "规则条件尚未结构化。", [], []
    group = groups[0]
    checks = []
    missing = []
    evidence: list[dict[str, Any]] = []
    for item in condition[group]:
        metric = str(item.get("metric") or "")
        if metric not in metrics:
            missing.append(metric)
            continue
        actual = metrics[metric]
        expected = float(item["value"])
        hit = _compare(actual, str(item.get("op") or ""), expected)
        checks.append(
            {
                "metric": metric,
                "actual": actual,
                "operator": item.get("op"),
                "expected": expected,
                "matched": hit,
            }
        )
        evidence.extend(metric_evidence.get(metric, []))
    if missing:
        return (
            "insufficient_data",
            "缺少计算所需指标：" + "、".join(missing),
            checks,
            _unique_evidence(evidence),
        )
    matched = all(item["matched"] for item in checks) if group == "all" else any(
        item["matched"] for item in checks
    )
    status = "hit" if matched else "not_hit"
    rationale = "规则阈值已触发。" if matched else "数据完整，未达到规则阈值。"
    return status, rationale, checks, _unique_evidence(evidence)


def _unique_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("id") or json.dumps(item, ensure_ascii=False, sort_keys=True))
        unique[key] = item
    return list(unique.values())


def _local_vector_similarity(left: str, right: str) -> float:
    def vector(value: str) -> Counter[str]:
        compact = re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())
        return Counter(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))

    left_vector, right_vector = vector(left), vector(right)
    if not left_vector or not right_vector:
        return 0.0
    numerator = sum(value * right_vector.get(key, 0) for key, value in left_vector.items())
    denominator = sqrt(sum(value * value for value in left_vector.values())) * sqrt(
        sum(value * value for value in right_vector.values())
    )
    return numerator / denominator if denominator else 0.0


def _event_candidates(rule: MonitoringRule, events: list[tuple[RiskEvent, str]]) -> list[dict[str, Any]]:
    keywords = EVENT_KEYWORDS.get(rule.code) or CATEGORY_FALLBACK_KEYWORDS.get(rule.category, ())
    candidates = []
    rule_text = f"{rule.monitoring_point}\n{rule.assessment_standard}"
    for event, entity_name in events:
        text = f"{event.title}\n{event.content}"
        matched = [keyword for keyword in keywords if keyword and keyword in text]
        similarity = _local_vector_similarity(rule_text, text[:2000])
        if not matched and similarity < 0.12:
            continue
        candidates.append(
            {
                "id": f"event-{event.id}",
                "title": event.title,
                "content": event.content[:900],
                "source_name": event.source_name,
                "source_url": event.source_url,
                "occurred_at": (
                    event.occurred_at or event.created_at
                ).isoformat(),
                "matched_keywords": matched,
                "entity_name": entity_name,
                "relevance_score": round(similarity, 4),
                "match_mode": "keyword" if matched else "vector",
            }
        )
    candidates.sort(
        key=lambda item: (len(item["matched_keywords"]), item["relevance_score"]),
        reverse=True,
    )
    return candidates[: get_settings().llm_max_evidence_items]


def _document_candidates(
    rule: MonitoringRule, documents: list[SourceDocument]
) -> list[dict[str, Any]]:
    keywords = EVENT_KEYWORDS.get(rule.code) or CATEGORY_FALLBACK_KEYWORDS.get(rule.category, ())
    candidates: list[dict[str, Any]] = []
    rule_text = f"{rule.monitoring_point}\n{rule.assessment_standard}"
    for document in documents:
        text = document.extracted_text or ""
        positions = [(text.find(keyword), keyword) for keyword in keywords if keyword and keyword in text]
        positions = [item for item in positions if item[0] >= 0]
        similarity = _local_vector_similarity(rule_text, text[:4000])
        if not positions and similarity < 0.12:
            continue
        position, keyword = min(positions) if positions else (0, "向量相似")
        excerpt = text[max(0, position - 250) : position + 650]
        candidates.append(
            {
                "id": f"document-{document.id}",
                "title": document.original_filename,
                "excerpt": excerpt,
                "matched_keywords": [keyword],
                "relevance_score": round(similarity, 4),
                "match_mode": "keyword" if positions else "vector",
            }
        )
    candidates.sort(key=lambda item: item["relevance_score"], reverse=True)
    return candidates[: get_settings().llm_max_evidence_items]


def _manual_status(
    rule: MonitoringRule, facts: list[ExtractedFact]
) -> tuple[str, str, dict[str, Any]] | None:
    code = f"rule_{rule.code}_status"
    fact = next((item for item in reversed(facts) if item.metric_code == code), None)
    if not fact:
        return None
    status = fact.value_text.strip().lower()
    if status not in VALID_STATUSES:
        return None
    return (
        status,
        f"采用已确认的人工判断：{status}。",
        {
            "id": f"fact-{fact.id}",
            "label": fact.label,
            "value_text": fact.value_text,
            "period": fact.period,
            "source_locator": fact.source_locator,
            "document_id": str(fact.document_id),
        },
    )


def _apply_llm_results(
    db: Session,
    rules: list[MonitoringRule],
    evaluations: dict[int, dict[str, Any]],
    evidence_by_rule: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    service = LLMService()
    if not service.is_configured:
        return {}
    usage_total: dict[str, int] = defaultdict(int)
    model_name = ""
    for category in sorted({rule.category for rule in rules}):
        category_rules = [
            rule
            for rule in rules
            if rule.category == category
            and evaluations[rule.code]["status"] == "insufficient_data"
            and evidence_by_rule.get(rule.code)
        ]
        if not category_rules:
            continue
        evidence_pool = _unique_evidence(
            [item for rule in category_rules for item in evidence_by_rule[rule.code]]
        )[: get_settings().llm_max_evidence_items]
        evidence_ids = {str(item["id"]) for item in evidence_pool}
        payload = {
            "category": category,
            "rules": [
                {
                    "rule_code": rule.code,
                    "monitoring_point": rule.monitoring_point,
                    "assessment_standard": rule.assessment_standard,
                }
                for rule in category_rules
            ],
            "evidence": evidence_pool,
        }
        try:
            result = service.complete_json(
                db,
                purpose=f"rule-evaluation:{category}",
                system_prompt=(
                    "你是投后风险规则复核器。只能依据给定证据判断，不能计算或修改数值。"
                    "返回 JSON：{evaluations:[{rule_code,status,severity,rationale,evidence_ids}]}。"
                    "status 仅可为 hit、not_hit、insufficient_data、not_applicable。"
                    "没有足够证据时必须返回 insufficient_data，不能把未取得资料写成未命中。"
                ),
                user_payload=payload,
                max_output_tokens=2500,
            )
        except Exception:
            continue
        if not result:
            continue
        model_name = result.model_name
        for key, value in result.token_usage.items():
            if isinstance(value, int):
                usage_total[key] += value
        allowed_codes = {rule.code for rule in category_rules}
        for item in result.payload.get("evaluations", []):
            if not isinstance(item, dict):
                continue
            try:
                code = int(item.get("rule_code"))
            except (TypeError, ValueError):
                continue
            status = str(item.get("status") or "")
            cited = [str(value) for value in item.get("evidence_ids", [])]
            if code not in allowed_codes or status not in VALID_STATUSES:
                continue
            if status == "hit" and not cited:
                continue
            if any(value not in evidence_ids for value in cited):
                continue
            evaluations[code].update(
                {
                    "status": status,
                    "severity": str(item.get("severity") or "normal")[:16],
                    "rationale": str(item.get("rationale") or "")[:4000],
                    "evidence": [
                        evidence for evidence in evidence_pool if evidence["id"] in cited
                    ],
                    "llm_output": item,
                }
            )
    return {"model_name": model_name, "token_usage": dict(usage_total)}


def evaluate_rules(db: Session, analysis_run_id: Any) -> dict[str, Any]:
    run = db.get(AnalysisRun, analysis_run_id)
    if not run:
        raise ValueError("分析任务不存在。")
    company = db.get(Company, run.company_id)
    if not company:
        raise ValueError("公司不存在。")
    rule_set = ensure_company_rule_assignments(db, company.id)
    assignments = list(
        db.execute(
            select(CompanyRuleAssignment, MonitoringRule)
            .join(MonitoringRule, MonitoringRule.id == CompanyRuleAssignment.rule_id)
            .where(CompanyRuleAssignment.company_id == company.id)
            .where(CompanyRuleAssignment.enabled.is_(True))
            .where(MonitoringRule.rule_set_id == rule_set.id)
            .order_by(MonitoringRule.code)
        ).all()
    )
    rules = [row[1] for row in assignments]
    facts = _confirmed_facts(db, company.id, run.requested_period)
    metrics, metric_evidence = _latest_metrics(facts, run.requested_period)
    profile_metrics, profile_evidence = _profile_metrics(company)
    for key, value in profile_metrics.items():
        metrics.setdefault(key, value)
        if key not in metric_evidence:
            metric_evidence[key] = profile_evidence.get(key, [])
    own_events = list(
        db.execute(
            select(RiskEvent)
            .where(RiskEvent.company_id == company.id)
            .order_by(RiskEvent.occurred_at.desc().nullslast(), RiskEvent.created_at.desc())
            .limit(300)
        ).scalars()
    )
    events: list[tuple[RiskEvent, str]] = [
        (event, company.name)
        for event in own_events
        if _event_within_requested_period(event, run.requested_period)
    ]
    related_names = list(
        db.execute(
            select(RelatedEntity.name)
            .where(RelatedEntity.company_id == company.id)
            .where(RelatedEntity.status == "confirmed")
        ).scalars()
    )
    if related_names:
        related_companies = list(
            db.execute(select(Company).where(Company.name.in_(related_names))).scalars()
        )
        for related_company in related_companies:
            related_events = db.execute(
                select(RiskEvent)
                .where(RiskEvent.company_id == related_company.id)
                .order_by(RiskEvent.occurred_at.desc().nullslast(), RiskEvent.created_at.desc())
                .limit(100)
            ).scalars()
            events.extend(
                (event, related_company.name)
                for event in related_events
                if _event_within_requested_period(event, run.requested_period)
            )
    documents = list(
        db.execute(
            select(SourceDocument)
            .where(SourceDocument.company_id == company.id)
            .where(SourceDocument.status == "parsed")
            .order_by(SourceDocument.created_at.desc())
            .limit(100)
        ).scalars()
    )

    evaluations: dict[int, dict[str, Any]] = {}
    evidence_by_rule: dict[int, list[dict[str, Any]]] = {}
    for rule in rules:
        manual = _manual_status(rule, facts)
        if manual:
            status, rationale, manual_evidence = manual
            checks: list[dict[str, Any]] = []
            evidence: list[dict[str, Any]] = [manual_evidence]
        elif rule.code == 6 and (company.company_profile or {}).get(
            "has_overseas_operations"
        ) is False:
            status, rationale, checks, evidence = (
                "not_applicable",
                "公司档案已确认不存在境外经营或境外资产。",
                [],
                [],
            )
        elif rule.condition:
            status, rationale, checks, evidence = _condition_result(
                rule.condition or {}, metrics, metric_evidence
            )
        else:
            status, rationale, checks, evidence = (
                "insufficient_data",
                "需要事件证据或内部材料完成判断。",
                [],
                [],
            )
        candidates = [
            *_event_candidates(rule, events),
            *_document_candidates(rule, documents),
        ]
        evidence_by_rule[rule.code] = _unique_evidence(candidates)
        keyword_candidates = [
            item for item in candidates if item.get("match_mode") == "keyword"
        ]
        if status == "insufficient_data" and keyword_candidates and rule.evaluation_type in {"event", "hybrid"}:
            status = "hit"
            rationale = "检索到与规则直接相关的事件证据，需人工复核影响程度。"
            evidence = keyword_candidates
        evaluations[rule.code] = {
            "status": status,
            "severity": (
                "important"
                if status == "hit" and (rule.requirement_type == "强制" or "重大" in rule.assessment_standard)
                else "general"
            ),
            "rationale": rationale,
            "facts": checks,
            "evidence": _unique_evidence(evidence),
            "llm_output": {},
        }

    llm_summary = _apply_llm_results(
        db, rules, evaluations, evidence_by_rule
    )
    for rule in rules:
        payload = evaluations[rule.code]
        existing = db.execute(
            select(RuleEvaluation)
            .where(RuleEvaluation.analysis_run_id == run.id)
            .where(RuleEvaluation.rule_id == rule.id)
        ).scalar_one_or_none()
        target = existing or RuleEvaluation(
            analysis_run_id=run.id,
            rule_id=rule.id,
            status=payload["status"],
        )
        target.status = payload["status"]
        target.severity = payload["severity"]
        target.rationale = payload["rationale"]
        target.evidence = payload["evidence"]
        target.facts = payload["facts"]
        target.llm_output = payload["llm_output"]
        db.add(target)

    counts: dict[str, int] = defaultdict(int)
    for payload in evaluations.values():
        counts[payload["status"]] += 1
    run.summary = {
        **(run.summary or {}),
        "rule_set_version": rule_set.version,
        "rule_count": len(rules),
        "rule_status_counts": dict(counts),
        "llm": llm_summary,
    }
    db.add(run)
    db.commit()
    return {
        "rule_set_version": rule_set.version,
        "rule_count": len(rules),
        "status_counts": dict(counts),
        "llm": llm_summary,
    }
