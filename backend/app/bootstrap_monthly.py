"""一键重建携程金融月度监测数据，并可直接生成该月月报。

部署后在 backend 服务内（工作目录为 backend 根目录）运行：

    python -m app.bootstrap_monthly --period 2026-10 --generate

依次执行：建表、写入携程金融演示数据与监测实体清单、导入
app/data/monthly/<期间>/ 下的月度数据表（*.xlsx）和宏观指标（宏观指标_*.csv）、
按该期规则评估并生成月报。可重复运行：已有记录自动跳过，月报每次新增一个版本。
配置了 LLM_* 环境变量时由大模型改写正文，否则生成确定性草稿。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import AnalysisRun, Company, MonthlyReport
from app.services.ctrip_demo_seed import COMPANY_NAME, seed_ctrip_demo
from app.services.monitoring_scope import ensure_official_related_entities
from app.services.monthly_data_import import import_macro_csv, import_workbook
from app.services.monthly_reports import export_monthly_report_xlsx, generate_monthly_report
from app.services.rule_engine import evaluate_rules
from app.services.rule_seed import ensure_company_rule_assignments

MONTHLY_DATA_DIR = Path(__file__).resolve().parent / "data" / "monthly"


def available_periods() -> list[str]:
    return sorted(path.name for path in MONTHLY_DATA_DIR.iterdir() if path.is_dir())


def bootstrap(period: str, *, generate: bool = False, export: Path | None = None) -> dict:
    data_dir = MONTHLY_DATA_DIR / period
    if not data_dir.is_dir():
        raise SystemExit(f"没有 {period} 的月度数据目录：{data_dir}；可用期间：{available_periods()}")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    result: dict = {"period": period, "workbooks": {}, "macro": {}}
    try:
        seed_ctrip_demo(db, create_company=True)
        company = db.execute(select(Company).where(Company.name == COMPANY_NAME)).scalar_one()
        ensure_official_related_entities(db, company)
        ensure_company_rule_assignments(db, company.id)

        for path in sorted(data_dir.glob("*.xlsx")):
            report = import_workbook(db, path, period=period)
            result["workbooks"][path.name] = {
                "risk_events": len(report["risk_events"]),
                "industry_events": len(report["industry_events"]),
                "warnings": report["warnings"],
            }
        for path in sorted(data_dir.glob("宏观指标_*.csv")):
            result["macro"][path.name] = import_macro_csv(db, path)

        if generate:
            run = AnalysisRun(
                company_id=company.id,
                status="running",
                requested_period=period,
                current_stage="evaluate_rules",
                source_status={"manual_import": {"status": "success", "message": f"{period} 月度数据（人工整理导入）"}},
            )
            db.add(run)
            db.commit()
            evaluate_rules(db, run.id)
            run = db.get(AnalysisRun, run.id)
            run.status = "completed"
            run.current_stage = "completed"
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()
            version = (
                db.execute(
                    select(func.max(MonthlyReport.version))
                    .where(MonthlyReport.company_id == company.id)
                    .where(MonthlyReport.period == period)
                ).scalar()
                or 0
            ) + 1
            year, month = period.split("-")
            report = MonthlyReport(
                company_id=company.id,
                analysis_run_id=run.id,
                period=period,
                version=version,
                status="generating",
                title=f"{company.name}风险监测报告（{year}年{int(month)}月）",
                created_by="bootstrap",
            )
            db.add(report)
            db.commit()
            report = generate_monthly_report(db, report.id)
            result["report"] = {
                "id": str(report.id),
                "version": report.version,
                "model": report.model_name,
                "fallback_reason": (report.token_usage or {}).get("fallback_reason", ""),
                "ratings": {
                    key: value.get("level")
                    for key, value in ((report.evidence_snapshot or {}).get("ratings") or {}).items()
                },
            }
            if export:
                export.parent.mkdir(parents=True, exist_ok=True)
                export.write_bytes(export_monthly_report_xlsx(db, report))
                result["report"]["export"] = str(export)
    finally:
        db.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="重建携程金融月度监测数据并生成月报")
    parser.add_argument("--period", default="", help="报告期 YYYY-MM，默认取最新一期数据目录")
    parser.add_argument("--generate", action="store_true", help="导入后按该期评估并生成月报")
    parser.add_argument("--export", type=Path, default=None, help="同时导出月报 Excel 到该路径")
    args = parser.parse_args()
    period = args.period or available_periods()[-1]
    result = bootstrap(period, generate=args.generate, export=args.export)
    print(f"期间：{result['period']}")
    for name, item in result["workbooks"].items():
        print(f"  导入 {name}：风险事件 {item['risk_events']} 条，行业事件 {item['industry_events']} 条")
        for warning in item["warnings"]:
            print(f"    需确认：{warning}")
    for name, item in result["macro"].items():
        print(f"  宏观指标 {name}：新增 {item['added']}，更新 {item['updated']}")
    if "report" in result:
        report = result["report"]
        print(f"  月报 v{report['version']}（{report['model']}）：{report['id']}")
        if report["fallback_reason"]:
            print(f"    未使用大模型改写：{report['fallback_reason']}")
        print("  评级：" + "，".join(f"{key}={level}" for key, level in report["ratings"].items()))
        if report.get("export"):
            print(f"  已导出：{report['export']}")


if __name__ == "__main__":
    main()
