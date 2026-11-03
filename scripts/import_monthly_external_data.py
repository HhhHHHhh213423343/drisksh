"""把人工整理的月度外部风险数据表导入数据库（可重复运行，已有记录自动跳过）。

工作表与入库位置：
- “司法与监管处罚”：各主体的风险事件（risk_events，法律板块）；
- “舆情监测”：被投企业本体的舆情事件（risk_events，舆情板块）；
- “头部机构动态”：行业事件，维度“头部机构动态”（macro_industry_events）；
- “监管政策”：行业事件，维度“行业监管态势”（macro_industry_events）；
- “行业数据”（收集模板格式）：行业事件，维度“市场行业格局”，按子项归类；
  未填数值或标注“待发布”的行跳过。
缺少的工作表自动跳过。
“摘要”及各表的结论行由月报生成时归纳，不入库。
行业事件记录所属报告期（report_period），月报按报告期纳入，因此表内
发布日期略早于当月的事项（如月末施行的政策）也会进入该期报告。

运行方式（在项目根目录，DATABASE_URL 指向目标数据库）：

    python3 scripts/import_monthly_external_data.py <xlsx路径> [--period 2026-10] [--dry-run]
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import Company, MacroIndustryEvent, RelatedEntity, RiskEvent  # noqa: E402
from app.services.monitoring_scope import ensure_official_related_entities  # noqa: E402

COMPANY_NAME = "上海携程金融信息服务有限公司"
QYYJT = "企业预警通"
QYYJT_URL = "https://www.qyyjt.cn/"
NO_EVENT_MARKERS = ("未检索到",)


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_date(value) -> tuple[date | None, str]:
    """返回（入库日期，原始写法）。区间或模糊写法取月内最早可确定的日期。"""

    if isinstance(value, datetime):
        return value.date(), value.date().isoformat()
    raw = _text(value)
    match = re.search(r"(20\d{2})-(\d{1,2})(?:-(\d{1,2}))?", raw)
    if not match:
        return None, raw
    year, month = int(match.group(1)), int(match.group(2))
    if match.group(3):
        day = int(match.group(3))
    elif "中旬" in raw:
        day = 15
    elif "下旬" in raw:
        day = 25
    else:
        day = 1
    return date(year, month, day), raw


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _subject(value: str) -> str:
    # “携程集团有限公司(股东,持股58.8%)” -> “携程集团有限公司”；关系说明以监测实体清单为准。
    # 只去掉名称末尾的关系说明，并统一为全角括号，与工商法定名称一致。
    name = re.sub(r"[（(][^（()）]*[)）]$", "", _text(value))
    return name.replace("(", "（").replace(")", "）")


def _rows(sheet, header_token: str):
    header = None
    for row in sheet.iter_rows(values_only=True):
        cells = [_text(cell) for cell in row]
        if header is None:
            if header_token in cells:
                header = cells
            continue
        if any(cells):
            yield dict(zip(header, cells))


def _sheet(workbook, keyword: str):
    return next((sheet for sheet in workbook.worksheets if keyword in sheet.title), None)


def _sheet_rows(workbook, keyword: str, header_token: str):
    sheet = _sheet(workbook, keyword)
    return _rows(sheet, header_token) if sheet is not None else []


def _company(db, name: str, parent: Company) -> Company:
    company = db.query(Company).filter(Company.name == name).one_or_none()
    if company is None:
        company = Company(
            name=name,
            company_profile={"monitoring_parent_id": str(parent.id)},
        )
        db.add(company)
        db.flush()
    return company


def _add_risk_event(db, report, company: Company, **fields) -> None:
    exists = (
        db.query(RiskEvent)
        .filter(RiskEvent.company_id == company.id)
        .filter(RiskEvent.title == fields["title"])
        .first()
    )
    if exists:
        report["skipped"].append(f"{company.name}：{fields['title']}（已存在）")
        return
    db.add(RiskEvent(company_id=company.id, **fields))
    report["risk_events"].append(f"{company.name}：{fields['title']}")


def _add_industry_event(db, report, **fields) -> None:
    dedupe = _hash(fields["dimension"], fields["scope_key"], fields["title"], fields["published_at"].date().isoformat())
    existing = db.query(MacroIndustryEvent).filter(MacroIndustryEvent.dedupe_hash == dedupe).first()
    if existing:
        period = fields["raw_payload"]["report_period"]
        if (existing.raw_payload or {}).get("report_period") != period:
            existing.raw_payload = {**(existing.raw_payload or {}), "report_period": period}
            report["skipped"].append(f"{fields['dimension']}：{fields['title']}（已存在，补记报告期 {period}）")
        else:
            report["skipped"].append(f"{fields['dimension']}：{fields['title']}（已存在）")
        return
    db.add(MacroIndustryEvent(dedupe_hash=dedupe, indicator_code="", relevance_score=1.0, **fields))
    report["industry_events"].append(f"{fields['dimension']}｜{fields['scope_key']}：{fields['title']}")


def report_period_of(path: Path, explicit: str = "") -> str:
    if explicit:
        return explicit
    match = re.search(r"(20\d{2})年(\d{1,2})月", path.name) or re.search(r"(20\d{2})-(\d{2})", path.name)
    if not match:
        raise SystemExit("无法从文件名识别报告期，请用 --period 2026-10 指定。")
    return f"{match.group(1)}-{int(match.group(2)):02d}"


def import_workbook(path: Path, *, dry_run: bool, period: str = "") -> dict:
    period = report_period_of(path, period)
    period_start = tuple(int(part) for part in period.split("-")) + (1,)
    Base.metadata.create_all(bind=engine)
    workbook = load_workbook(path, data_only=True)
    imported_at = datetime.now(timezone.utc).isoformat()
    report: dict[str, list[str]] = {
        "risk_events": [], "industry_events": [], "skipped": [], "warnings": [],
    }
    db = SessionLocal()
    try:
        parent = db.query(Company).filter(Company.name == COMPANY_NAME).one_or_none()
        if parent is None:
            raise SystemExit(f"数据库中没有 {COMPANY_NAME}，请先在平台建立该企业。")
        ensure_official_related_entities(db, parent)
        monitored = {COMPANY_NAME} | {
            name for (name,) in db.query(RelatedEntity.name)
            .filter(RelatedEntity.company_id == parent.id)
            .filter(RelatedEntity.status == "confirmed")
        }
        origin = {"origin": "manual_import", "source_file": path.name, "imported_at": imported_at}
        industry_origin = {**origin, "report_period": period}

        for row in _sheet_rows(workbook, "司法", "主体(与携程金融关系)"):
            name = _subject(row["主体(与携程金融关系)"])
            published, published_raw = parse_date(row.get("披露日期"))
            occurred, _ = parse_date(row.get("处罚/立案日期"))
            if not published:
                report["warnings"].append(f"司法表第{row.get('序号')}行缺少披露日期，未导入")
                continue
            if name not in monitored:
                report["warnings"].append(
                    f"{name} 不在监测实体清单内，已入库但不会进入月报按主体段落（第{row.get('序号')}行）"
                )
            kind = row.get("类别") or "监管处罚"
            content = row.get("内容") or ""
            details = "；".join(
                part for part in (
                    content,
                    f"文号：{row['文号']}" if row.get("文号") not in ("", "-") else "",
                    f"办理机关：{row['办理机关']}" if row.get("办理机关") not in ("", "-") else "",
                ) if part
            )
            _add_risk_event(
                db, report, _company(db, name, parent),
                category="监管处罚",
                severity="important" if kind in {"行政处罚", "立案调查"} else "general",
                title=f"{kind}：{content.split('：')[0].split('，')[0][:60]}（{published.isoformat()}）",
                content=details,
                source_name=QYYJT,
                source_url=QYYJT_URL,
                occurred_at=_at(occurred or published),
                sentiment="negative",
                extra_payload={**origin, "published_at": published.isoformat(), "date_text": published_raw, "kind": kind},
            )

        positive = ("荣获", "开业")
        for row in _sheet_rows(workbook, "舆情", "标题"):
            published, published_raw = parse_date(row.get("日期"))
            if not published or not row.get("标题"):
                continue
            title = row["标题"]
            sentiment = "positive" if any(token in title for token in positive) else "negative"
            _add_risk_event(
                db, report, parent,
                category="品牌舆情",
                severity="general",
                title=title[:255],
                content="；".join(part for part in (title, row.get("备注")) if part),
                source_name=row.get("来源") or QYYJT,
                source_url=QYYJT_URL,
                occurred_at=_at(published),
                sentiment=sentiment,
                extra_payload={**origin, "published_at": published.isoformat(), "date_text": published_raw, "collected_via": QYYJT},
            )

        for row in _sheet_rows(workbook, "头部机构", "动态"):
            text = row.get("动态") or ""
            no_event = any(marker in text for marker in NO_EVENT_MARKERS)
            published, published_raw = parse_date(row.get("日期"))
            if no_event and not published:
                # “10月全月”等写法：本月无重大动态也保留一条记录，归入本期月初。
                published = date(*period_start)
            if not published:
                report["warnings"].append(f"头部机构动态：{row.get('机构')} 日期“{row.get('日期')}”无法识别，未导入")
                continue
            _add_industry_event(
                db, report,
                scope_type="institution",
                scope_key=row.get("机构") or "",
                dimension="头部机构动态",
                event_type="本月无重大动态" if no_event else "机构动态",
                title=re.split(r"[；;：:（(]", text, maxsplit=1)[0][:120],
                summary=text,
                source_name=(row.get("来源") or "公开检索")[:128],
                source_url="",
                published_at=_at(published),
                impact_direction="neutral",
                severity="normal",
                raw_payload={**industry_origin, "date_text": published_raw},
            )

        for row in _sheet_rows(workbook, "监管政策", "政策/事件"):
            published, published_raw = parse_date(row.get("日期"))
            if not published:
                report["warnings"].append(f"监管政策：{row.get('政策/事件')} 日期无法识别，未导入")
                continue
            if published < date(*period_start):
                report["warnings"].append(
                    f"监管政策“{row['政策/事件']}”日期为{published_raw}，早于报告期；按本表所属报告期 {period} 纳入"
                )
            _add_industry_event(
                db, report,
                scope_type="industry",
                scope_key=row.get("领域") or "",
                dimension="行业监管态势",
                event_type="监管政策",
                title=(row.get("政策/事件") or "")[:255],
                summary=row.get("要点与影响") or "",
                source_name=(row.get("来源") or "公开检索")[:128],
                source_url="",
                published_at=_at(published),
                impact_direction="negative" if any(t in (row.get("要点与影响") or "") for t in ("收紧", "警示", "处罚", "立案", "承压")) else "neutral",
                severity="normal",
                raw_payload={**industry_origin, "date_text": published_raw},
            )

        for row in _sheet_rows(workbook, "行业数据", "数值或事件描述"):
            value = row.get("数值或事件描述") or ""
            if not value or value.startswith("待发布"):
                continue
            published, published_raw = parse_date(row.get("发布日期"))
            if not published:
                # 汇率等逐日数据没有单一发布日，归入本期月末。
                year, month = period_start[0], period_start[1]
                published = date(year, month, calendar.monthrange(year, month)[1])
            yoy = row.get("同比") or ""
            summary = value + (f"（同比{yoy}）" if yoy and yoy not in value else "")
            if row.get("备注"):
                summary += f"。备注：{row['备注']}"
            _add_industry_event(
                db, report,
                scope_type="market",
                scope_key=row.get("子项") or "",
                dimension="市场行业格局",
                event_type="统计数据",
                title=f"{row.get('指标/事件') or ''}（{row.get('统计期间') or period}）"[:255],
                summary=summary,
                source_name=(row.get("建议来源") or "公开数据")[:128],
                source_url=row.get("来源链接") or "",
                published_at=_at(published),
                impact_direction="neutral",
                severity="normal",
                raw_payload={**industry_origin, "date_text": published_raw or row.get("发布日期") or ""},
            )

        if dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="导入月度外部风险数据表")
    parser.add_argument("path", type=Path)
    parser.add_argument("--period", default="", help="报告期 YYYY-MM，默认从文件名识别")
    parser.add_argument("--dry-run", action="store_true", help="只检查不写入")
    args = parser.parse_args()
    report = import_workbook(args.path, dry_run=args.dry_run, period=args.period)
    print(f"{'[试运行] ' if args.dry_run else ''}新增风险事件 {len(report['risk_events'])} 条，行业事件 {len(report['industry_events'])} 条")
    for key, label in (("risk_events", "风险事件"), ("industry_events", "行业事件"), ("skipped", "跳过"), ("warnings", "需确认")):
        for item in report[key]:
            print(f"  [{label}] {item}")


if __name__ == "__main__":
    main()
