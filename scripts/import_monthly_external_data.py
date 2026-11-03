"""把人工整理的月度外部风险数据表导入数据库（逻辑见 app.services.monthly_data_import）。

运行方式（在项目根目录，DATABASE_URL 指向目标数据库）：

    python3 scripts/import_monthly_external_data.py <xlsx路径> [--period 2026-10] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.monthly_data_import import import_workbook  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="导入月度外部风险数据表")
    parser.add_argument("path", type=Path)
    parser.add_argument("--period", default="", help="报告期 YYYY-MM，默认从文件名识别")
    parser.add_argument("--dry-run", action="store_true", help="只检查不写入")
    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        report = import_workbook(db, args.path, dry_run=args.dry_run, period=args.period)
    finally:
        db.close()
    print(f"{'[试运行] ' if args.dry_run else ''}新增风险事件 {len(report['risk_events'])} 条，行业事件 {len(report['industry_events'])} 条")
    for key, label in (("risk_events", "风险事件"), ("industry_events", "行业事件"), ("skipped", "跳过"), ("warnings", "需确认")):
        for item in report[key]:
            print(f"  [{label}] {item}")


if __name__ == "__main__":
    main()
