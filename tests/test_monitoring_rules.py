from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes import monitoring as monitoring_routes
from app.config import get_settings
from app.db.base import Base
from app.models import (
    AnalysisRun,
    Company,
    ExtractedFact,
    MacroIndicatorPoint,
    MacroIndustryEvent,
    MonthlyReport,
    MonitoringRule,
    RelatedEntity,
    RiskEvent,
    RuleEvaluation,
    SourceDocument,
)
from app.schemas.monitoring import MonthlyReportUpdate, SectionRatingUpdate
from app.services.document_ingestion import METRIC_ALIASES, parse_path, safe_filename, validate_extension
from app.services.monthly_reports import (
    RATING_LEVELS,
    RISK_SECTIONS,
    SECTION_ORDER,
    _code_ranges,
    _deterministic_sections,
    _llm_sections,
    _macro_context,
    _section_for_rule,
    _apply_entity_signals,
    _section_rating,
    export_monthly_report_xlsx,
    generate_monthly_report,
    hit_periods,
    split_entity_blocks,
)
from app.services.monitoring_scope import ensure_official_related_entities
from app.services.rule_engine import evaluate_rules
from app.services.rule_seed import RULE_CONDITIONS, ensure_company_rule_assignments, ensure_default_rule_set


def database() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_rule_seed_contains_all_79_rules() -> None:
    db = database()
    rule_set = ensure_default_rule_set(db)
    assert rule_set.version == "2026.1"
    assert db.scalar(select(func.count(MonitoringRule.id))) == 82
    assert db.scalar(select(MonitoringRule).where(MonitoringRule.code == 32)).condition
    ctrip_rules = list(
        db.execute(select(MonitoringRule).where(MonitoringRule.code.in_([80, 81, 82]))).scalars()
    )
    assert all("携程" in rule.monitoring_point for rule in ctrip_rules)
    assert all(rule.applicability == {"company_name_keywords": ["携程"]} for rule in ctrip_rules)
    required_metrics = {
        item["metric"]
        for condition in RULE_CONDITIONS.values()
        for group in ("all", "any")
        for item in condition.get(group, [])
    }
    assert required_metrics <= set(METRIC_ALIASES)


def test_spreadsheet_parser_extracts_period_facts(tmp_path: Path) -> None:
    path = tmp_path / "finance.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["指标", "2025-06", "2026-06"])
    sheet.append(["营业收入", 100, 80])
    sheet.append(["净利润", 10, -2])
    workbook.save(path)

    result = parse_path(path, ".xlsx")
    facts = {(fact.metric_code, fact.period, fact.value_numeric) for fact in result.facts}
    assert ("revenue", "2025-06", 100.0) in facts
    assert ("revenue", "2026-06", 80.0) in facts
    assert ("net_profit", "2026-06", -2.0) in facts


def test_upload_filename_and_macro_file_are_rejected() -> None:
    assert safe_filename("../../财务报告.xlsx") == "财务报告.xlsx"
    try:
        validate_extension("含宏文件.xlsm")
    except ValueError as exc:
        assert "仅支持" in str(exc)
    else:
        raise AssertionError("宏文件不应进入解析流程")


def test_rule_engine_distinguishes_hit_and_missing_data(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    db = database()
    company = Company(name="测试商业保理有限公司")
    db.add(company)
    db.flush()
    ensure_company_rule_assignments(db, company.id)
    document = SourceDocument(
        company_id=company.id,
        original_filename="finance.xlsx",
        extension=".xlsx",
        storage_path="/tmp/finance.xlsx",
        sha256="a" * 64,
        size_bytes=10,
        status="parsed",
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            ExtractedFact(
                document_id=document.id,
                company_id=company.id,
                metric_code="revenue",
                label="营业收入",
                value_numeric=100,
                value_text="100",
                period="2025-06",
                confidence=1,
                status="confirmed",
            ),
            ExtractedFact(
                document_id=document.id,
                company_id=company.id,
                metric_code="revenue",
                label="营业收入",
                value_numeric=80,
                value_text="80",
                period="2026-06",
                confidence=1,
                status="confirmed",
            ),
        ]
    )
    run = AnalysisRun(
        company_id=company.id,
        status="running",
        requested_period="2026-06",
        current_stage="evaluate_rules",
    )
    db.add(run)
    db.commit()

    result = evaluate_rules(db, run.id)
    assert result["rule_count"] == 79
    rule_32 = db.execute(
        select(RuleEvaluation)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == run.id)
        .where(MonitoringRule.code == 32)
    ).scalar_one()
    rule_33 = db.execute(
        select(RuleEvaluation)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == run.id)
        .where(MonitoringRule.code == 33)
    ).scalar_one()
    assert rule_32.status == "hit"
    assert rule_33.status == "insufficient_data"


def test_rule_engine_limits_events_to_requested_month(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    db = database()
    company = Company(name="期间测试公司")
    db.add(company)
    db.flush()
    ensure_company_rule_assignments(db, company.id)
    db.add_all(
        [
            RiskEvent(
                company_id=company.id,
                category="legal",
                severity="important",
                title="7月新增诉讼",
                content="公司发生重大诉讼，已发布开庭公告。",
                source_url="https://example.com/july",
                source_name="测试来源",
                occurred_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
            ),
            RiskEvent(
                company_id=company.id,
                category="legal",
                severity="important",
                title="8月新增诉讼",
                content="公司发生重大诉讼，已发布开庭公告。",
                source_url="https://example.com/august",
                source_name="测试来源",
                occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            ),
        ]
    )
    run = AnalysisRun(
        company_id=company.id,
        status="running",
        requested_period="2026-07",
        current_stage="evaluate_rules",
    )
    db.add(run)
    db.commit()
    evaluate_rules(db, run.id)
    evaluation = db.execute(
        select(RuleEvaluation)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == run.id)
        .where(MonitoringRule.code == 73)
    ).scalar_one()
    assert evaluation.status == "hit"
    assert [item["title"] for item in evaluation.evidence] == ["7月新增诉讼"]


def test_monthly_report_generates_versions_and_xlsx(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "demo_report_as_of_date", "2026-07-09", raising=False)
    db = database()
    company = Company(name="月报测试公司")
    db.add(company)
    db.flush()
    ensure_company_rule_assignments(db, company.id)
    document = SourceDocument(
        company_id=company.id,
        original_filename="monthly.xlsx",
        extension=".xlsx",
        storage_path="/tmp/monthly.xlsx",
        sha256="b" * 64,
        size_bytes=10,
        status="parsed",
    )
    db.add(document)
    db.flush()
    db.add_all(
        [
            ExtractedFact(
                document_id=document.id,
                company_id=company.id,
                metric_code="revenue",
                label="营业收入",
                value_numeric=100,
                value_text="100",
                period="2026-06",
                confidence=1,
                status="confirmed",
            ),
            ExtractedFact(
                document_id=document.id,
                company_id=company.id,
                metric_code="revenue",
                label="营业收入",
                value_numeric=120,
                value_text="120",
                period="2026-07",
                confidence=1,
                status="confirmed",
            ),
        ]
    )
    db.add(
        MacroIndicatorPoint(
            indicator_code="manufacturing_pmi",
            indicator_name="制造业 PMI",
            period="2026-07",
            value=49.2,
            unit="点",
            frequency="月度",
            region_scope="全国",
            source_name="国家统计局",
            source_url="https://data.stats.gov.cn/",
        )
    )
    run = AnalysisRun(
        company_id=company.id,
        status="running",
        requested_period="2026-07",
        current_stage="evaluate_rules",
    )
    db.add(run)
    db.commit()
    evaluate_rules(db, run.id)
    run.status = "completed"
    db.add(run)
    report = MonthlyReport(
        company_id=company.id,
        analysis_run_id=run.id,
        period="2026-07",
        version=1,
        status="generating",
        title="月报测试公司2026-07投后监测报告",
        created_by="tester",
    )
    db.add(report)
    db.commit()

    generated = generate_monthly_report(db, report.id)
    assert generated.status == "draft"
    assert list(generated.sections) == SECTION_ORDER
    assert "较上月增长20.00%" in generated.sections["finance"]
    assert "2026-07制造业 PMI49.2点" in generated.sections["external"]
    assert generated.evidence_snapshot["as_of_date"] == "2026-07-09"
    assert generated.evidence_snapshot["period_status"] == "month_to_date"
    ratings = generated.evidence_snapshot["ratings"]
    assert list(ratings) == RISK_SECTIONS
    assert all(item["level"] in RATING_LEVELS and item["basis"] for item in ratings.values())
    assert ratings["finance"]["data_pending"] is True
    assert "财务风险" in generated.evidence_snapshot["rating_boundary"]

    db.add(RelatedEntity(company_id=company.id, name="测试股东有限公司", relation_type="shareholder", status="confirmed"))
    db.add(RelatedEntity(company_id=company.id, name="测试平台有限公司", relation_type="partner", status="confirmed"))
    db.add(RelatedEntity(company_id=company.id, name="张某", relation_type="key_person", status="confirmed"))
    db.commit()
    content = export_monthly_report_xlsx(db, generated)
    workbook = load_workbook(filename=__import__("io").BytesIO(content))
    assert workbook.sheetnames == [
        "风险监测报告",
        "监测范围及评估标准",
        "风险概况评级逻辑",
        "监测实体",
        "规则评估",
        "证据索引",
    ]
    report_sheet = workbook["风险监测报告"]
    assert report_sheet["B3"].value == "外部风险"
    assert str(report_sheet["D4"].value).startswith("风险等级：低风险")
    assert "（资料待补）" in str(report_sheet["D4"].value)
    scope_rows = [
        row
        for row in workbook["监测范围及评估标准"].iter_rows(min_row=4, values_only=True)
        if isinstance(row[1], int)
    ]
    assert len(scope_rows) == 82
    assert sum(1 for row in scope_rows if row[5] == "✓") == 79
    assert [row[1] for row in scope_rows[-4:]] == [80, 81, 82, 79]
    assert all(row[5] is None for row in scope_rows[-4:-1])
    logic_values = [cell for row in workbook["风险概况评级逻辑"].iter_rows(values_only=True) for cell in row]
    assert "财务风险" in logic_values and generated.evidence_snapshot["rating_boundary"] in logic_values
    entity_rows = list(workbook["监测实体"].iter_rows(values_only=True))
    assert (1, "测试股东有限公司", "月报测试公司股东方") in entity_rows
    assert (1, "测试平台有限公司", "合作方") in entity_rows
    assert not any("张某" in str(row) for row in entity_rows)
    assert workbook["规则评估"].max_row == 80
    evidence_rows = list(workbook["证据索引"].iter_rows(values_only=True))
    assert any("https://data.stats.gov.cn/" in str(row[5]) for row in evidence_rows)

    monkeypatch.setattr(monitoring_routes, "ensure_demo_company", lambda company: company)
    request = SimpleNamespace(state=SimpleNamespace(user="reviewer"))
    edited = monitoring_routes.update_monthly_report(
        generated.id,
        MonthlyReportUpdate(
            ratings={
                "legal": SectionRatingUpdate(
                    level="较低风险", summary="合作平台涉诉", basis="合作平台涉诉存在间接传导可能。"
                )
            },
            rating_boundary="期间：2026年7月；公开资料核对截至2026-07-09。",
        ),
        request,
        db,
    )
    assert edited["ratings"]["legal"]["level"] == "较低风险"
    assert edited["ratings"]["legal"]["source"] == "manual"
    assert edited["ratings"]["external"]["source"] == "system"
    assert edited["rating_boundary"].startswith("期间：2026年7月")

    snapshot = dict(generated.evidence_snapshot)
    snapshot["ratings"] = {key: value for key, value in snapshot["ratings"].items() if key != "other"}
    generated.evidence_snapshot = snapshot
    db.commit()
    with pytest.raises(HTTPException) as missing_rating:
        monitoring_routes.approve_monthly_report(generated.id, request, db)
    assert "其他风险" in str(missing_rating.value.detail)


def test_ctrip_brand_rules_only_enabled_for_ctrip_companies(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    db = database()
    ctrip = Company(name="上海携程金融信息服务有限公司")
    other = Company(name="测试商业保理有限公司")
    db.add_all([ctrip, other])
    db.flush()
    db.add(
        RiskEvent(
            company_id=ctrip.id,
            category="舆情",
            severity="important",
            sentiment="negative",
            title="携程金融相关负面舆情引发消费者权益投诉",
            content="媒体报道称用户就金融产品展示及消费者权益问题集中投诉。",
            source_name="测试来源",
            source_url="https://example.com/news",
            occurred_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        )
    )
    runs = {}
    for company in (ctrip, other):
        run = AnalysisRun(
            company_id=company.id,
            status="running",
            requested_period="2026-09",
            current_stage="evaluate_rules",
        )
        db.add(run)
        db.commit()
        runs[company.name] = run
        evaluate_rules(db, run.id)

    def evaluated_codes(run: AnalysisRun) -> set[int]:
        return set(
            db.execute(
                select(MonitoringRule.code)
                .join(RuleEvaluation, RuleEvaluation.rule_id == MonitoringRule.id)
                .where(RuleEvaluation.analysis_run_id == run.id)
            ).scalars()
        )

    ctrip_codes = evaluated_codes(runs[ctrip.name])
    other_codes = evaluated_codes(runs[other.name])
    assert len(ctrip_codes) == 82 and {80, 81, 82} <= ctrip_codes
    assert len(other_codes) == 79 and not {80, 81, 82} & other_codes
    rule_80 = db.execute(
        select(RuleEvaluation)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == runs[ctrip.name].id)
        .where(MonitoringRule.code == 80)
    ).scalar_one()
    assert rule_80.status == "hit"
    rule = db.execute(select(MonitoringRule).where(MonitoringRule.code == 80)).scalar_one()
    assert _section_for_rule(rule) == "sentiment"
    assert _section_for_rule(db.execute(select(MonitoringRule).where(MonitoringRule.code == 81)).scalar_one()) == "brand"


def test_section_rating_levels() -> None:
    rule = SimpleNamespace(code=1, monitoring_point="测试要点。")

    def evaluation(status: str, severity: str = "general") -> SimpleNamespace:
        return SimpleNamespace(status=status, severity=severity, rationale="测试理由")

    def level(*items: SimpleNamespace) -> str:
        return _section_rating([(item, rule) for item in items])["level"]

    assert level() == "低风险"
    assert level(evaluation("not_hit")) == "低风险"
    assert level(evaluation("hit")) == "较低风险"
    assert level(evaluation("hit", "important")) == "中风险"
    assert level(evaluation("hit", "important"), evaluation("hit", "important")) == "较高风险"
    assert level(evaluation("hit", "high")) == "较高风险"
    assert level(evaluation("hit", "critical")) == "高风险"
    pending = _section_rating([(evaluation("insufficient_data"), rule)])
    assert pending["level"] == "低风险" and pending["data_pending"] is True
    assert "需补充资料后复核" in pending["basis"]
    assert _code_ranges([7, 1, 2, 3, 5, 6]) == "1-3、5-7"


def test_ctrip_report_groups_legal_and_sentiment_by_entity(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "demo_report_as_of_date", "2026-09-30", raising=False)
    db = database()
    ctrip = Company(name="上海携程金融信息服务有限公司")
    platform = Company(name="携程科技（上海）有限公司")
    group = Company(name="携程集团有限公司")
    db.add_all([ctrip, platform, group])
    db.flush()
    db.add(
        RelatedEntity(
            company_id=ctrip.id,
            name="携程旅游网络技术（上海）有限公司",
            relation_type="shareholder",
            status="candidate",
            source="qichacha_profile",
        )
    )
    db.commit()
    assert ensure_official_related_entities(db, ctrip) == 10
    assert ensure_official_related_entities(db, ctrip) == 0
    confirmed = db.execute(
        select(RelatedEntity).where(RelatedEntity.company_id == ctrip.id).where(RelatedEntity.status == "confirmed")
    ).scalars().all()
    assert len(confirmed) == 10
    by_name = {item.name: item for item in confirmed}
    assert by_name["携程集团有限公司"].relation_type == "penetrating_shareholder"
    assert by_name["中国银联股份有限公司"].relation_type == "penetrating_shareholder"
    assert by_name["中国银联股份有限公司"].metadata_payload["sentiment_group"] == "银联系"
    assert by_name["携程旅游网络技术（上海）有限公司"].metadata_payload["relation_note"].endswith("股东方")

    def event(company: Company, title: str, day: datetime, **extra) -> RiskEvent:
        return RiskEvent(
            company_id=company.id,
            category=extra.pop("category", "legal"),
            severity="general",
            sentiment=extra.pop("sentiment", "negative"),
            title=title,
            content=f"{title}，案件排期持续至11月。",
            source_name="测试来源",
            source_url="https://example.com/case",
            occurred_at=day,
            extra_payload=extra,
        )

    db.add_all(
        [
            event(platform, "携程科技（上海）有限公司新增个人信息保护纠纷开庭公告", datetime(2026, 9, 10, tzinfo=timezone.utc)),
            # 同一事项9月再次披露，只保留一次。
            event(platform, "携程科技（上海）有限公司新增个人信息保护纠纷开庭公告", datetime(2026, 9, 20, tzinfo=timezone.utc)),
            event(group, "携程集团8月诉讼事项", datetime(2026, 8, 15, tzinfo=timezone.utc)),
            # 事件日在8月，但9月发布，按发布日期纳入本期。
            event(
                group,
                "监管部门公布在线酒店预订平台行业行政指导会",
                datetime(2026, 8, 28, tzinfo=timezone.utc),
                category="监管处罚",
                published_at="2026-09-15",
            ),
        ]
    )
    db.commit()
    ensure_company_rule_assignments(db, ctrip.id)
    run = AnalysisRun(company_id=ctrip.id, status="running", requested_period="2026-09", current_stage="evaluate_rules")
    db.add(run)
    db.commit()
    evaluate_rules(db, run.id)
    run.status = "completed"
    report = MonthlyReport(
        company_id=ctrip.id,
        analysis_run_id=run.id,
        period="2026-09",
        version=1,
        status="generating",
        title="携程金融2026-09风险监测报告",
        created_by="tester",
    )
    db.add_all([run, report])
    db.commit()

    generated = generate_monthly_report(db, report.id)
    legal = dict(split_entity_blocks(generated.sections["legal"]))
    assert list(legal) == [
        "本体｜上海携程金融信息服务有限公司",
        "合作方｜携程科技（上海）有限公司",
        "合作方｜携程金融科技（上海）有限公司",
        "关联方｜携程集团有限公司",
        "关联方｜携程旅游网络技术（上海）有限公司",
        "关联方｜银联国际有限公司",
        "关联方｜上海领偕商务咨询有限公司",
        "关联方｜克拉斯（北京）投资有限公司",
        "关联方｜北京中汇优通投资有限公司",
        "关联方｜上海程兑商务咨询有限责任公司",
        "关联方｜中国银联股份有限公司",
        "法律风险综合判断",
    ]
    assert "自身未新增重大诉讼" in legal["本体｜上海携程金融信息服务有限公司"]
    platform_text = legal["合作方｜携程科技（上海）有限公司"]
    assert platform_text.startswith("总结：") and platform_text.count("个人信息保护纠纷开庭公告") == 1
    assert "行政指导会" in legal["关联方｜携程集团有限公司"]
    assert "8月诉讼事项" not in generated.sections["legal"]
    assert legal["关联方｜银联国际有限公司"] == "本期暂未发现相关风险。"
    legal_rating = generated.evidence_snapshot["ratings"]["legal"]
    assert legal_rating["level"] != "低风险"
    assert legal["法律风险综合判断"].startswith(f"综合评定为{legal_rating['level']}")

    sentiment = dict(split_entity_blocks(generated.sections["sentiment"]))
    assert list(sentiment) == ["携程系", "银联系", "其他主体", "舆情风险综合判断"]
    assert "携程科技（上海）有限公司" in sentiment["携程系"]
    assert "UnionPay" in sentiment["银联系"] and sentiment["银联系"].endswith("本期暂未监测到相关风险信息。")
    assert "中国银联股份有限公司" in sentiment["银联系"]
    assert "克拉斯（北京）投资有限公司" in sentiment["其他主体"]
    assert generated.sections["operations"] == "本期暂无相关数据支持。"
    boundary = generated.evidence_snapshot["rating_boundary"]
    assert "已确认的10个合作方、关联方" in boundary and "按9月信息发布日期纳入" in boundary
    assert any(item.startswith("risk-event-") for item in generated.evidence_snapshot["by_section"]["legal"])

    workbook = load_workbook(filename=__import__("io").BytesIO(export_monthly_report_xlsx(db, generated)))
    report_rows = list(workbook["风险监测报告"].iter_rows(values_only=True))
    platform_row = next(row for row in report_rows if row[1] == "合作方\n携程科技（上海）有限公司")
    assert "个人信息保护纠纷" in platform_row[2]
    assert any(row[1] == "银联系" for row in report_rows)
    entity_rows = [row for row in workbook["监测实体"].iter_rows(values_only=True) if isinstance(row[0], int)]
    assert [row[1] for row in entity_rows] == [
        "上海携程金融信息服务有限公司",
        "携程科技（上海）有限公司",
        "携程金融科技（上海）有限公司",
        "携程旅游网络技术（上海）有限公司",
        "银联国际有限公司",
        "上海领偕商务咨询有限公司",
        "克拉斯（北京）投资有限公司",
        "北京中汇优通投资有限公司",
        "上海程兑商务咨询有限责任公司",
        "携程集团有限公司",
        "中国银联股份有限公司",
    ]
    assert entity_rows[1][2] == "携程金融平台开发、运营主体"
    assert entity_rows[-1][2] == "上海携程金融信息服务有限公司穿透股东方"
    evidence_rows = list(workbook["证据索引"].iter_rows(values_only=True))
    assert any("个人信息保护纠纷" in str(row[2]) for row in evidence_rows)


def test_entity_events_raise_low_rating_to_indirect_signal() -> None:
    base = {"level": "低风险", "data_pending": False, "summary": "本期暂未发现相关风险。", "basis": "对应监测范围第71-75项。", "source": "system"}
    members = [
        {"name": "被投企业", "events": []},
        {"name": "合作平台有限公司", "events": [{"kinds": ["legal"]}]},
    ]
    ratings = _apply_entity_signals({"legal": dict(base), "sentiment": dict(base)}, members)
    assert ratings["legal"]["level"] == "较低风险"
    assert "合作平台有限公司" in ratings["legal"]["basis"]
    assert ratings["sentiment"]["level"] == "低风险"
    assert split_entity_blocks("无标题正文") == []


def test_macro_context_includes_all_period_events_and_assigned_ones(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "demo_report_as_of_date", "2026-10-31", raising=False)
    db = database()

    def industry_event(title: str, day: datetime, **payload) -> MacroIndustryEvent:
        return MacroIndustryEvent(
            scope_type="industry",
            scope_key="测试领域",
            dimension="行业监管态势",
            event_type="监管政策",
            title=title,
            summary=title,
            source_name="测试来源",
            source_url="",
            published_at=day,
            dedupe_hash=title,
            raw_payload=payload,
        )

    db.add_all(
        [industry_event(f"十月事件{index}", datetime(2026, 10, index + 1, tzinfo=timezone.utc)) for index in range(15)]
        + [
            # 9月30日施行、人工整理时归入10月报告期。
            industry_event("月末施行的营销办法", datetime(2026, 9, 30, tzinfo=timezone.utc), report_period="2026-10"),
            industry_event("九月旧事件", datetime(2026, 9, 20, tzinfo=timezone.utc)),
            industry_event("十一月事件", datetime(2026, 11, 2, tzinfo=timezone.utc)),
        ]
    )
    db.commit()
    labels = {item["label"] for item in _macro_context(db, "2026-10") if item["kind"] == "event"}
    assert len(labels) == 16
    assert "月末施行的营销办法" in labels
    assert all(item["in_period"] for item in _macro_context(db, "2026-10") if item["kind"] == "event")
    assert not {"九月旧事件", "十一月事件"} & labels

    # 本期没有整理事件时沿用最近事件作为背景。
    assert {item["label"] for item in _macro_context(db, "2026-12") if item["kind"] == "event"}


def test_llm_rewrite_may_cite_context_evidence(monkeypatch) -> None:
    from app.services import monthly_reports

    class FakeLLM:
        is_configured = True

        def complete_json(self, db, **kwargs):
            payload = {
                "summary": "模型摘要",
                "sections": {key: f"模型{key}" for key in SECTION_ORDER},
                "ratings": {},
                # 引用提示中提供的公司事件与主体事项证据。
                "evidence_ids_by_section": {
                    "legal": ["risk-event-company"],
                    "sentiment": ["risk-event-member"],
                    # 规则证据写作 event-<id>，模型改写成 risk-event-<id> 仍指向同一记录。
                    "other": ["risk-event-5cad4e19-3034-4b52-94b0-01add755e154"],
                },
            }
            return SimpleNamespace(payload=payload, token_usage={"total": 1}, model_name="fake-model")

    monkeypatch.setattr(monthly_reports, "LLMService", FakeLLM)
    db = database()
    company = Company(name="引用测试公司")
    db.add(company)
    db.commit()
    report = SimpleNamespace(company_id=company.id, period="2026-10")
    base_rating = {"level": "低风险", "data_pending": False, "summary": "", "basis": "依据", "source": "system"}
    result = _llm_sections(
        db,
        report=report,
        company=company,
        rows=[],
        base_summary="系统摘要",
        base_sections={key: f"系统{key}" for key in SECTION_ORDER},
        evidence_by_section={"other": ["event-5cad4e19-3034-4b52-94b0-01add755e154"]},
        monthly_metrics=[],
        macro_context=[{"kind": "company_event", "evidence_id": "risk-event-company"}],
        as_of_date=datetime(2026, 10, 31).date(),
        base_ratings={key: dict(base_rating) for key in RISK_SECTIONS},
        members=[{"name": "关联方", "events": [{"evidence_id": "risk-event-member", "kinds": ["sentiment"]}]}],
    )
    assert result[4] == "fake-model"
    assert result[1]["legal"] == "模型legal"

    original = FakeLLM.complete_json

    def citing_unknown(self, db, **kwargs):
        response = original(self, db, **kwargs)
        response.payload["evidence_ids_by_section"] = {"legal": ["risk-event-invented"]}
        return response

    def run():
        return _llm_sections(
            db, report=report, company=company, rows=[], base_summary="系统摘要",
            base_sections={key: f"系统{key}" for key in SECTION_ORDER}, evidence_by_section={},
            monthly_metrics=[], macro_context=[], as_of_date=datetime(2026, 10, 31).date(),
            base_ratings={key: dict(base_rating) for key in RISK_SECTIONS}, members=[],
        )

    monkeypatch.setattr(FakeLLM, "complete_json", citing_unknown)
    rejected = run()
    assert rejected[4] == "deterministic"
    assert rejected[3]["fallback_reason"] == "模型引用了未提供的证据编号：risk-event-invented"

    calls = []

    def flaky(self, db, **kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        response = original(self, db, **kwargs)
        response.payload["evidence_ids_by_section"] = {}
        return response

    monkeypatch.setattr(FakeLLM, "complete_json", flaky)
    retried = run()
    assert retried[4] == "fake-model" and calls == [180, 180]


def test_hits_are_split_by_publication_period() -> None:
    db = database()
    company = Company(name="分期测试公司")
    db.add(company)
    db.flush()

    def event(title: str, day: datetime, **payload) -> RiskEvent:
        item = RiskEvent(
            company_id=company.id, category="监管处罚", severity="important", title=title, content=title,
            source_name="测试", source_url="", occurred_at=day, sentiment="negative", extra_payload=payload,
        )
        db.add(item)
        return item

    old = event("七月处罚", datetime(2026, 7, 25, tzinfo=timezone.utc))
    # 事件日在9月，但10月才发布，按发布日期属于10月。
    late = event("十月披露的九月事项", datetime(2026, 9, 20, tzinfo=timezone.utc), published_at="2026-10-12")
    db.commit()

    rule = SimpleNamespace(code=71, monitoring_point="受到重大行政处罚。", category="法律风险")

    def hit(key: str, *evidence_ids: str, severity: str = "important") -> SimpleNamespace:
        return SimpleNamespace(
            id=key, status="hit", severity=severity, rationale=f"{key}理由",
            evidence=[{"id": value, "title": "七月处罚" if value.startswith("event-") else ""} for value in evidence_ids],
        )

    rows = [
        (hit("history", f"event-{old.id}"), rule),
        (hit("current", f"event-{old.id}", f"event-{late.id}"), rule),
        (hit("metric", "fact-1"), rule),
    ]
    periods = hit_periods(db, rows, "2026-10", datetime(2026, 10, 31).date())
    assert periods == {"history": "historical", "current": "current", "metric": "current"}

    history_only = _section_rating([rows[0]], periods)
    assert history_only["level"] == "较低风险" and history_only["history_only"] is True
    assert "本期未见新增披露" in history_only["basis"] and "七月处罚" in history_only["basis"]
    assert "受到重大行政处罚" not in history_only["basis"].split("另有")[1]
    assert _section_rating(rows[:2], periods)["level"] == "中风险"

    summary, sections, _ = _deterministic_sections(
        company, "2026-10", [(rows[0][0], SimpleNamespace(code=71, monitoring_point="受到重大行政处罚。", category="法律风险"))],
        [], [], datetime(2026, 10, 31).date(), periods,
    )
    assert "本期发现0项需关注事项" in summary and "1项为此前已披露" in summary
    assert sections["legal"].startswith("本期未见新增披露。")
    assert sections["actions"].startswith("1. 持续跟踪")


def test_llm_rating_cannot_escalate_history_only_section(monkeypatch) -> None:
    from app.services import monthly_reports

    class FakeLLM:
        is_configured = True

        def complete_json(self, db, **kwargs):
            payload = {
                "summary": "模型摘要",
                "sections": {key: f"模型{key}" for key in SECTION_ORDER},
                "ratings": {key: {"level": "较高风险", "summary": "模型", "basis": "模型依据"} for key in RISK_SECTIONS},
                "evidence_ids_by_section": {},
            }
            return SimpleNamespace(payload=payload, token_usage={}, model_name="fake-model")

    monkeypatch.setattr(monthly_reports, "LLMService", FakeLLM)
    db = database()
    company = Company(name="历史定级测试公司")
    db.add(company)
    db.commit()
    base = {"level": "较低风险", "data_pending": False, "summary": "", "basis": "依据", "source": "system", "history_only": False}
    ratings = {key: dict(base) for key in RISK_SECTIONS}
    ratings["governance"]["history_only"] = True
    result = _llm_sections(
        db, report=SimpleNamespace(company_id=company.id, period="2026-10"), company=company, rows=[],
        base_summary="系统摘要", base_sections={key: f"系统{key}" for key in SECTION_ORDER},
        evidence_by_section={}, monthly_metrics=[], macro_context=[],
        as_of_date=datetime(2026, 10, 31).date(), base_ratings=ratings, members=[],
    )
    assert result[2]["governance"]["level"] == "较低风险"
    assert result[2]["external"]["level"] == "较高风险"


def test_macro_context_picks_latest_period_not_latest_insert(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "demo_report_as_of_date", "2026-10-31", raising=False)
    db = database()
    collected = datetime(2026, 9, 24, tzinfo=timezone.utc)
    for period, value in (("2026-08", 49.5), ("2026-01", 49.3), ("2026-10", 49.1), ("2026-11", 50.0)):
        db.add(MacroIndicatorPoint(
            indicator_code="manufacturing_pmi", indicator_name="制造业 PMI", period=period, value=value,
            unit="点", frequency="月度", region_scope="全国", source_name="国家统计局",
            source_url="https://www.stats.gov.cn/", collected_at=collected,
        ))
    db.commit()
    indicators = [item for item in _macro_context(db, "2026-10") if item["kind"] == "indicator"]
    assert [(item["period"], item["value"]) for item in indicators] == [("2026-10", 49.1)]


def test_redaction_keeps_record_ids_but_masks_personal_numbers() -> None:
    from app.services.llm import redact_sensitive_text

    record = "macro-indicator-355c4210-9876-4000-8a12-32aeaae8ef51 risk-event-62620d851bd14553ab118f46a11407c4"
    assert redact_sensitive_text(record) == record
    text = redact_sensitive_text("卡号6222 0212 3456 7890 123，手机13812345678，身份证11010519491231002X")
    assert "[账号已脱敏]" in text and "[手机号已脱敏]" in text and "[身份证号已脱敏]" in text
    assert "13812345678" not in text and "6222" not in text


def test_llm_service_accepts_report_timeout() -> None:
    import inspect

    from app.services.llm import LLMService

    # 月报调用传入 timeout，接口签名缺少该参数时改写会静默退回确定性草稿。
    assert "timeout" in inspect.signature(LLMService.complete_json).parameters


def test_bootstrap_rebuilds_october_data_and_report(monkeypatch, tmp_path) -> None:
    from sqlalchemy.orm import sessionmaker

    from app import bootstrap_monthly

    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.db'}", future=True)
    monkeypatch.setattr(bootstrap_monthly, "engine", engine)
    monkeypatch.setattr(bootstrap_monthly, "SessionLocal", sessionmaker(bind=engine, future=True))

    result = bootstrap_monthly.bootstrap("2026-10", generate=True, export=tmp_path / "report.xlsx")
    imported = result["workbooks"]["携程金融-2026年10月外部风险数据(企业预警通+公开检索).xlsx"]
    assert imported["risk_events"] == 14 and imported["industry_events"] == 23
    assert result["macro"]["宏观指标_2026-10.csv"] == {"added": 7, "updated": 0}
    assert set(result["report"]["ratings"]) == set(RISK_SECTIONS)
    workbook = load_workbook(tmp_path / "report.xlsx")
    entity_rows = [row for row in workbook["监测实体"].iter_rows(values_only=True) if isinstance(row[0], int)]
    assert len(entity_rows) == 11

    rerun = bootstrap_monthly.bootstrap("2026-10")
    assert all(item["risk_events"] == 0 and item["industry_events"] == 0 for item in rerun["workbooks"].values())
    with Session(engine) as db:
        indicators = [item for item in _macro_context(db, "2026-10") if item["kind"] == "indicator"]
    assert ("2026-10", 49.3) in [(item["period"], item["value"]) for item in indicators]
