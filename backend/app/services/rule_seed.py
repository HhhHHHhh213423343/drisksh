from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CompanyRuleAssignment, MonitoringRule, RuleSet


RULE_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "rules_2026.json"


RULE_CONDITIONS: dict[int, dict[str, Any]] = {
    1: {"any": [{"metric": "estimated_net_profit_impact", "op": "lte", "value": -0.10}]},
    6: {"any": [{"metric": "overseas_asset_impairment_expected", "op": "gte", "value": 0.20}]},
    7: {"any": [{"metric": "system_outage_days", "op": "gte", "value": 7}, {"metric": "disaster_loss_to_equity", "op": "gte", "value": 0.05}]},
    9: {"any": [{"metric": "top5_customer_ratio", "op": "gte", "value": 0.50}, {"metric": "top1_customer_ratio", "op": "gte", "value": 0.30}]},
    11: {"any": [{"metric": "capital_operation_amount_to_equity", "op": "gte", "value": 0.10}, {"metric": "capital_operation_loss_to_equity", "op": "gte", "value": 0.05}]},
    13: {"any": [{"metric": "core_revenue_yoy", "op": "lte", "value": -0.30}]},
    16: {"any": [{"metric": "cash_to_assets", "op": "gte", "value": 0.40}]},
    17: {"any": [{"metric": "expense_growth_minus_revenue_growth", "op": "gte", "value": 0.20}]},
    21: {"any": [{"metric": "related_party_transaction_ratio", "op": "gte", "value": 0.10}]},
    28: {"any": [{"metric": "valuation_yoy", "op": "lte", "value": -0.10}]},
    29: {"all": [{"metric": "related_party_fund_occupation_to_equity", "op": "gte", "value": 0.05}, {"metric": "related_party_fund_occupation_days", "op": "gt", "value": 90}]},
    32: {"any": [{"metric": "revenue_yoy", "op": "lte", "value": -0.10}]},
    33: {"any": [{"metric": "net_profit_yoy", "op": "lte", "value": -0.10}, {"metric": "net_profit", "op": "lt", "value": 0}]},
    34: {"any": [{"metric": "core_business_metric_yoy", "op": "lte", "value": -0.10}, {"metric": "budget_completion", "op": "lt", "value": 0.80}]},
    35: {"any": [{"metric": "net_assets", "op": "lt", "value": 0}, {"metric": "net_assets_yoy", "op": "lte", "value": -0.20}]},
    36: {"any": [{"metric": "roe_change_pp", "op": "lte", "value": -5}]},
    39: {"any": [{"metric": "overdue_90d_ratio_change_pp", "op": "gte", "value": 2}]},
    43: {"any": [{"metric": "operating_cashflow", "op": "lt", "value": 0}, {"metric": "operating_cashflow_yoy", "op": "lte", "value": -0.10}, {"metric": "cash_months_to_maturity", "op": "lt", "value": 3}]},
    44: {"any": [{"metric": "guarantees_to_equity", "op": "gte", "value": 0.20}]},
    46: {"any": [{"metric": "accounting_change_profit_impact_abs", "op": "gte", "value": 0.10}]},
    50: {"any": [{"metric": "key_role_vacancy_months", "op": "gte", "value": 3}]},
    51: {"any": [{"metric": "executive_turnover_rate", "op": "gte", "value": 0.40}]},
    52: {"any": [{"metric": "core_team_attrition_rate", "op": "gte", "value": 0.20}, {"metric": "employee_attrition_rate", "op": "gte", "value": 0.15}]},
    57: {"any": [{"metric": "top_shareholder_change_pp_abs", "op": "gte", "value": 10}]},
    62: {"any": [{"metric": "pledged_share_ratio", "op": "gte", "value": 0.50}]},
    70: {"any": [{"metric": "frozen_share_ratio", "op": "gte", "value": 0.30}]},
    71: {"any": [{"metric": "penalty_to_net_profit", "op": "gte", "value": 0.10}]},
    73: {"any": [{"metric": "litigation_amount_to_equity", "op": "gte", "value": 0.10}]},
    75: {"any": [{"metric": "legal_guarantees_to_equity", "op": "gte", "value": 0.20}]},
}


CATEGORY_KEYS = {
    "外部风险": "external",
    "业务及经营风险": "operations",
    "财务风险": "finance",
    "公司治理风险": "governance",
    "内控风险": "internal_control",
    "法律风险": "legal",
    "品牌舆情风险": "brand_sentiment",
    "其他风险": "other",
}

HYBRID_RULE_CODES = {1, 6, 7, 11, 13, 46, 71, 73, 75}


def _metric_requirements(condition: dict[str, Any]) -> list[str]:
    requirements: list[str] = []
    for group in ("all", "any"):
        for item in condition.get(group, []):
            metric = str(item.get("metric") or "").strip()
            if metric and metric not in requirements:
                requirements.append(metric)
    return requirements


def ensure_default_rule_set(db: Session) -> RuleSet:
    payload_bytes = RULE_DATA_PATH.read_bytes()
    payload = json.loads(payload_bytes.decode("utf-8"))
    version = str(payload["version"])
    rule_set = db.execute(
        select(RuleSet).where(RuleSet.version == version)
    ).scalar_one_or_none()
    if not rule_set:
        rule_set = RuleSet(
            name=payload["name"],
            version=version,
            source_hash=hashlib.sha256(payload_bytes).hexdigest(),
            status="active",
            source_name=payload.get("source_name", ""),
        )
        db.add(rule_set)
        db.flush()
    else:
        rule_set.name = payload["name"]
        rule_set.source_hash = hashlib.sha256(payload_bytes).hexdigest()
        rule_set.source_name = payload.get("source_name", "")
        db.add(rule_set)

    for item in payload["rules"]:
        code = int(item["code"])
        condition = RULE_CONDITIONS.get(code, {})
        evaluation_type = item["evaluation_type"]
        if code in HYBRID_RULE_CODES:
            evaluation_type = "hybrid"
        if condition and evaluation_type not in {"quantitative", "hybrid"}:
            evaluation_type = "hybrid"
        rule = db.execute(
            select(MonitoringRule)
            .where(MonitoringRule.rule_set_id == rule_set.id)
            .where(MonitoringRule.code == code)
        ).scalar_one_or_none()
        if not rule:
            rule = MonitoringRule(
                rule_set_id=rule_set.id,
                code=code,
            )
        rule.category = item["category"]
        rule.monitoring_point = item["monitoring_point"]
        rule.assessment_standard = item["assessment_standard"]
        rule.requirement_type = item["requirement_type"]
        rule.evaluation_type = evaluation_type
        rule.enabled = True
        rule.condition = condition
        rule.data_requirements = {
            "metrics": _metric_requirements(condition),
            "original_targets": item.get("original_targets", {}),
        }
        rule.applicability = {"company_scope": "all"}
        db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.execute(
            select(RuleSet).where(RuleSet.version == version)
        ).scalar_one_or_none()
        if concurrent:
            return ensure_default_rule_set(db)
        raise
    db.refresh(rule_set)
    return rule_set


def ensure_company_rule_assignments(db: Session, company_id) -> RuleSet:
    rule_set = ensure_default_rule_set(db)
    rules = list(
        db.execute(
            select(MonitoringRule)
            .where(MonitoringRule.rule_set_id == rule_set.id)
            .order_by(MonitoringRule.code)
        ).scalars()
    )
    existing_rule_ids = set(
        db.execute(
            select(CompanyRuleAssignment.rule_id).where(
                CompanyRuleAssignment.company_id == company_id
            )
        ).scalars()
    )
    for rule in rules:
        if rule.id not in existing_rule_ids:
            db.add(
                CompanyRuleAssignment(
                    company_id=company_id,
                    rule_id=rule.id,
                    enabled=True,
                    override={},
                )
            )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return rule_set
