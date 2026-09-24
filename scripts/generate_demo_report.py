"""手动触发一次携程金融投后监测月报生成，用于验证文风与数据效果。

运行方式（在项目根目录）：

    python3 scripts/generate_demo_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db.session import SessionLocal  # noqa: E402
from app.models import AnalysisRun, Company, MonthlyReport, ReportVersion  # noqa: E402
from app.services.monthly_reports import (  # noqa: E402
    SECTION_LABELS,
    SECTION_ORDER,
    generate_monthly_report,
)

COMPANY_NAME = "上海携程金融信息服务有限公司"
PERIOD = "2026-09"


def main() -> None:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.name == COMPANY_NAME).one_or_none()
        if company is None:
            print(f"未找到公司：{COMPANY_NAME}")
            return

        run = (
            db.query(AnalysisRun)
            .filter(AnalysisRun.company_id == company.id)
            .filter(AnalysisRun.status == "completed")
            .order_by(AnalysisRun.created_at.desc())
            .first()
        )
        if run is None:
            print("没有已完成的分析任务，请先在页面点击“开始分析”。")
            return

        existing = (
            db.query(MonthlyReport)
            .filter(MonthlyReport.company_id == company.id)
            .filter(MonthlyReport.period == PERIOD)
            .all()
        )
        for old in existing:
            db.query(ReportVersion).filter(ReportVersion.report_id == old.id).delete()
            db.delete(old)
        if existing:
            db.commit()
            print(f"已清理 {len(existing)} 份旧月报草稿，重新生成")

        report = MonthlyReport(
            company_id=company.id,
            analysis_run_id=run.id,
            period=PERIOD,
            version=1,
            status="generating",
            title=f"{company.name}{PERIOD}投后监测月报",
            created_by="demo",
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        print(f"已创建月报 {report.id}")

        generate_monthly_report(db, report.id)
        db.refresh(report)

        print(f"\n模型：{report.model_name}")
        print(f"状态：{report.status}")
        print(f"Token：{report.token_usage}")
        print("\n========== 风险概况 ==========")
        print(report.summary)
        for key in SECTION_ORDER:
            print(f"\n========== {SECTION_LABELS[key]} ==========")
            print((report.sections or {}).get(key) or "")
    finally:
        db.close()


if __name__ == "__main__":
    main()
