from __future__ import annotations

import hashlib
import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    Company,
    MacroIndicatorPoint,
    MacroIndustryEvent,
    MacroRefreshRun,
)


MACRO_INDICATORS = [
    {
        "code": "gdp_yoy", "name": "GDP 同比增长", "function": "macro_china_gdp",
        "period_keys": ("季度",), "value_keys": ("国内生产总值-同比增长",),
        "unit": "%", "frequency": "季度", "source_code": "nbs_macro",
        "source_name": "国家统计局（AkShare 聚合）", "source_url": "https://data.stats.gov.cn/",
    },
    {
        "code": "cpi_yoy", "name": "CPI 同比增长", "function": "macro_china_cpi",
        "period_keys": ("月份", "月"), "value_keys": ("全国-同比增长", "同比增长"),
        "unit": "%", "frequency": "月度", "source_code": "nbs_macro",
        "source_name": "国家统计局（AkShare 聚合）", "source_url": "https://data.stats.gov.cn/",
    },
    {
        "code": "ppi_yoy", "name": "PPI 同比增长", "function": "macro_china_ppi",
        "period_keys": ("月份", "月"), "value_keys": ("当月同比增长", "同比增长"),
        "unit": "%", "frequency": "月度", "source_code": "nbs_macro",
        "source_name": "国家统计局（AkShare 聚合）", "source_url": "https://data.stats.gov.cn/",
    },
    {
        "code": "manufacturing_pmi", "name": "制造业 PMI", "function": "macro_china_pmi",
        "period_keys": ("月份", "月"), "value_keys": ("制造业-指数", "制造业PMI"),
        "unit": "点", "frequency": "月度", "source_code": "nbs_macro",
        "source_name": "国家统计局（AkShare 聚合）", "source_url": "https://data.stats.gov.cn/",
    },
    {
        "code": "non_manufacturing_pmi", "name": "非制造业 PMI", "function": "macro_china_pmi",
        "period_keys": ("月份", "月"), "value_keys": ("非制造业-指数", "非制造业PMI"),
        "unit": "点", "frequency": "月度", "source_code": "nbs_macro",
        "source_name": "国家统计局（AkShare 聚合）", "source_url": "https://data.stats.gov.cn/",
    },
    {
        "code": "lpr_1y", "name": "1 年期 LPR", "function": "macro_china_lpr",
        "period_keys": ("TRADE_DATE", "日期"), "value_keys": ("LPR1Y", "1Y"),
        "unit": "%", "frequency": "月度", "source_code": "pbc_macro",
        "source_name": "中国人民银行（AkShare 聚合）", "source_url": "https://www.pbc.gov.cn/",
    },
    {
        "code": "m2_yoy", "name": "M2 同比增长", "function": "macro_china_money_supply",
        "period_keys": ("月份", "月"), "value_keys": ("货币和准货币(M2)-同比增长", "M2同比增长"),
        "unit": "%", "frequency": "月度", "source_code": "pbc_macro",
        "source_name": "中国人民银行（AkShare 聚合）", "source_url": "https://www.pbc.gov.cn/",
    },
]

INDUSTRY_FALLBACKS = {
    "002594": ("汽车制造业", "广东"),
    "300750": ("电气机械和器材制造业", "福建"),
    "601398": ("货币金融服务", "北京"),
    "600612": ("零售业", "上海"),
}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return _json_safe(
        json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
    )


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def _normalize_period(value: Any) -> str:
    text = str(value or "").strip()
    quarter = re.search(r"(20\d{2})年第(?:1-)?([1-4])季度", text)
    if quarter:
        return f"{quarter.group(1)}-Q{quarter.group(2)}"
    month = re.search(r"(20\d{2})年(\d{1,2})月份?", text)
    if month:
        return f"{month.group(1)}-{int(month.group(2)):02d}"
    date_match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if date_match:
        return f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
    compact_month = re.fullmatch(r"(20\d{2})(\d{2})", text)
    if compact_month:
        return f"{compact_month.group(1)}-{compact_month.group(2)}"
    return text[:40]


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _period_datetime(period: str) -> datetime:
    quarter = re.fullmatch(r"(20\d{2})-Q([1-4])", period)
    if quarter:
        return datetime(int(quarter.group(1)), int(quarter.group(2)) * 3, 1, tzinfo=timezone.utc)
    month = re.fullmatch(r"(20\d{2})-(\d{2})", period)
    if month:
        return datetime(int(month.group(1)), int(month.group(2)), 1, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(period.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _resolve_industry(db: Session, company: Company) -> str:
    profile = dict(company.company_profile or {})
    akshare_profile = profile.get("akshare_profile") or {}
    code = str(akshare_profile.get("stock_code") or profile.get("stock_code") or "")
    industry = str(company.industry or "")
    region = str(company.region or "")
    if not industry and code in INDUSTRY_FALLBACKS:
        industry, fallback_region = INDUSTRY_FALLBACKS[code]
        region = region or fallback_region
    if code:
        try:
            import akshare as ak

            function = getattr(ak, "stock_profile_cninfo", None)
            records = _records(function(symbol=code)) if callable(function) else []
            if records:
                record = records[0]
                industry = str(record.get("所属行业") or industry)
                address = str(record.get("注册地址") or record.get("办公地址") or "")
                for candidate in ("北京", "上海", "天津", "重庆", "广东", "浙江", "江苏", "山东", "福建", "四川", "湖北", "湖南", "河南", "河北", "安徽", "江西", "陕西", "山西", "辽宁", "吉林", "黑龙江", "云南", "贵州", "海南", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆"):
                    if candidate in address:
                        region = candidate
                        break
                profile["cninfo_profile"] = {
                    "company_name": record.get("公司名称"),
                    "stock_code": record.get("A股代码"),
                    "industry": industry,
                    "registered_address": record.get("注册地址"),
                    "main_business": record.get("主营业务"),
                    "source": "巨潮资讯",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as exc:
            profile["cninfo_profile"] = {
                "status": "unavailable",
                "message": str(exc)[:300],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
    company.industry = industry or None
    company.region = region or None
    company.company_profile = profile
    db.add(company)
    db.commit()
    return industry


def _indicator_direction(code: str, value: float, previous: float | None) -> tuple[str, str]:
    if code in {"manufacturing_pmi", "non_manufacturing_pmi"}:
        return ("positive", "normal") if value >= 50 else ("negative", "important")
    if code == "gdp_yoy":
        return ("positive", "normal") if value >= 5 else (("negative", "important") if value < 4 else ("neutral", "normal"))
    if code in {"cpi_yoy", "ppi_yoy"} and (value < 0 or abs(value) >= 5):
        return "negative", "important"
    if code == "lpr_1y" and previous is not None and value < previous:
        return "positive", "normal"
    return "neutral", "normal"


def _event_hash(title: str, source_name: str, published_at: datetime, scope_key: str) -> str:
    normalized = re.sub(r"\s+", "", title).lower()
    return hashlib.sha256(
        f"{normalized}|{source_name}|{published_at.date().isoformat()}|{scope_key}".encode("utf-8")
    ).hexdigest()


def _upsert_event(db: Session, payload: dict[str, Any]) -> bool:
    dedupe = _event_hash(
        payload["title"], payload["source_name"], payload["published_at"], payload.get("scope_key", "")
    )
    event = db.execute(
        select(MacroIndustryEvent).where(MacroIndustryEvent.dedupe_hash == dedupe)
    ).scalar_one_or_none()
    created = event is None
    event = event or MacroIndustryEvent(dedupe_hash=dedupe)
    for key, value in payload.items():
        setattr(event, key, value)
    db.add(event)
    return created


def _relevant_codes(industry: str) -> set[str]:
    if any(keyword in industry for keyword in ("银行", "金融", "保险", "证券")):
        return {"gdp_yoy", "lpr_1y", "m2_yoy"}
    if any(keyword in industry for keyword in ("制造", "汽车", "设备", "电气", "材料", "化工", "钢铁", "电子")):
        return {"gdp_yoy", "manufacturing_pmi", "ppi_yoy", "lpr_1y"}
    if any(keyword in industry for keyword in ("零售", "食品", "消费", "餐饮")):
        return {"gdp_yoy", "cpi_yoy", "lpr_1y"}
    return {"gdp_yoy", "cpi_yoy", "lpr_1y"}


def _collect_indicators(
    db: Session, run: MacroRefreshRun
) -> tuple[int, int, list[dict[str, Any]], list[str]]:
    import akshare as ak

    inserted = updated = 0
    results: list[dict[str, Any]] = []
    failed: list[str] = []
    cache: dict[str, list[dict[str, Any]]] = {}
    for index, config in enumerate(MACRO_INDICATORS, start=1):
        try:
            if config["function"] not in cache:
                function = getattr(ak, config["function"])
                cache[config["function"]] = _records(function())
            parsed: list[tuple[str, float, dict[str, Any]]] = []
            for record in cache[config["function"]]:
                period = _normalize_period(_first(record, config["period_keys"]))
                value = _number(_first(record, config["value_keys"]))
                if period and value is not None:
                    parsed.append((period, value, record))
            parsed.sort(key=lambda item: item[0])
            points = parsed[-(24 if config["frequency"] == "季度" else 60):]
            for period, value, record in points:
                point = db.execute(
                    select(MacroIndicatorPoint)
                    .where(MacroIndicatorPoint.indicator_code == config["code"])
                    .where(MacroIndicatorPoint.period == period)
                    .where(MacroIndicatorPoint.region_scope == "全国")
                ).scalar_one_or_none()
                if point:
                    updated += 1
                else:
                    point = MacroIndicatorPoint(
                        indicator_code=config["code"], period=period, region_scope="全国"
                    )
                    inserted += 1
                point.indicator_name = config["name"]
                point.value = value
                point.unit = config["unit"]
                point.frequency = config["frequency"]
                point.source_name = config["source_name"]
                point.source_url = config["source_url"]
                point.raw_payload = _json_safe(record)
                point.collected_at = datetime.now(timezone.utc)
                db.add(point)
            results.append({
                "source": config["source_code"], "indicator": config["code"],
                "status": "success" if points else "no_hit", "points": len(points),
            })
        except Exception as exc:
            failed.append(config["code"])
            results.append({
                "source": config["source_code"], "indicator": config["code"],
                "status": "failed", "message": str(exc)[:300],
            })
        run.progress_current = index + 1
        run.source_results = results
        run.failed_sources = failed
        run.message = f"已更新 {index}/{len(MACRO_INDICATORS)} 项宏观数据"
        db.add(run)
        db.commit()
    return inserted, updated, results, failed


def _generate_indicator_events(db: Session, industry: str) -> tuple[int, int]:
    inserted = updated = 0
    for config in MACRO_INDICATORS:
        rows = list(
            db.execute(
                select(MacroIndicatorPoint)
                .where(MacroIndicatorPoint.indicator_code == config["code"])
                .order_by(MacroIndicatorPoint.period.desc())
                .limit(2)
            ).scalars()
        )
        if not rows:
            continue
        latest, previous = rows[0], rows[1] if len(rows) > 1 else None
        direction, severity = _indicator_direction(
            config["code"], latest.value, previous.value if previous else None
        )
        title = f"{latest.period} {latest.indicator_name}为{latest.value:g}{latest.unit}"
        created = _upsert_event(db, {
            "scope_type": "macro", "scope_key": "全国", "dimension": "macroeconomy",
            "event_type": "macro_indicator_release", "indicator_code": config["code"],
            "title": title, "summary": f"公开宏观指标显示：{title}。",
            "source_name": latest.source_name, "source_url": latest.source_url,
            "published_at": _period_datetime(latest.period), "impact_direction": direction,
            "severity": severity, "relevance_score": 1.0,
            "raw_payload": {"period": latest.period, "value": latest.value},
        })
        inserted += int(created)
        updated += int(not created)
        if industry and config["code"] in _relevant_codes(industry):
            created = _upsert_event(db, {
                "scope_type": "industry", "scope_key": industry, "dimension": "industry_cycle",
                "event_type": "industry_indicator_signal", "indicator_code": config["code"],
                "title": f"{industry}关联信号：{latest.indicator_name} {latest.value:g}{latest.unit}",
                "summary": f"{industry}与{latest.indicator_name}存在经营传导关系，当前公开值为{latest.value:g}{latest.unit}。",
                "source_name": latest.source_name, "source_url": latest.source_url,
                "published_at": _period_datetime(latest.period), "impact_direction": direction,
                "severity": severity, "relevance_score": 0.9,
                "raw_payload": {"period": latest.period, "value": latest.value, "industry": industry},
            })
            inserted += int(created)
            updated += int(not created)
    db.commit()
    return inserted, updated


def _policy_matches(title: str, industry: str) -> bool:
    common = ("民营经济", "全国统一大市场", "投资", "价格", "信用", "数据要素", "物流成本")
    industry_words = {
        "汽车": ("汽车", "充电", "电池", "新能源", "设备更新", "制造"),
        "制造": ("设备更新", "工业", "制造", "节能", "材料"),
        "金融": ("金融", "贷款", "利率", "信用", "融资"),
    }
    return any(word in title for word in common) or any(
        key in industry and any(word in title for word in words)
        for key, words in industry_words.items()
    )


def _collect_ndrc_policy(db: Session, industry: str) -> tuple[int, int, dict[str, Any]]:
    list_url = "https://www.ndrc.gov.cn/xxgk/zcfb/tz/wap_index.html"
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "D-Risk-AI/1.0"}) as client:
        response = client.get(list_url)
        response.raise_for_status()
    pattern = re.compile(
        r'<li>\s*<a\s+href="([^"]+)"[^>]*>(.*?)</a>\s*<span>(20\d{2}/\d{2}/\d{2})</span>\s*</li>',
        re.S,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    inserted = updated = matched = 0
    for href, raw_title, date_text in pattern.findall(response.text):
        title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        published = datetime.fromisoformat(date_text.replace("/", "-")).replace(tzinfo=timezone.utc)
        if published < cutoff or not _policy_matches(title, industry):
            continue
        is_industry = bool(industry and any(keyword in title for keyword in ("汽车", "制造", "新能源", "金融", "设备")))
        created = _upsert_event(db, {
            "scope_type": "industry" if is_industry else "macro",
            "scope_key": industry if is_industry else "全国",
            "dimension": "policy_regulatory", "event_type": "official_policy_release",
            "indicator_code": "", "title": title,
            "summary": f"国家发展改革委于{published.date().isoformat()}发布该政策文件。",
            "source_name": "国家发展和改革委员会", "source_url": urljoin(list_url, href),
            "published_at": published, "impact_direction": "neutral", "severity": "important",
            "relevance_score": 0.95 if is_industry else 0.75,
            "raw_payload": {"list_url": list_url},
        })
        inserted += int(created)
        updated += int(not created)
        matched += 1
    db.commit()
    return inserted, updated, {
        "source": "ndrc_policy", "status": "success", "events": matched,
        "source_url": list_url,
    }


def serialize_macro_run(run: MacroRefreshRun) -> dict[str, Any]:
    return {
        "id": str(run.id), "company_id": str(run.company_id), "run_type": "macro_refresh",
        "status": run.status, "progress_current": run.progress_current,
        "progress_total": run.progress_total, "inserted_count": run.inserted_count,
        "updated_count": run.updated_count, "failed_sources": run.failed_sources or [],
        "source_results": run.source_results or [], "message": run.message,
        "started_at": run.started_at, "completed_at": run.completed_at,
        "updated_at": run.updated_at,
    }


def refresh_macro_data(db: Session, run_id: Any) -> MacroRefreshRun:
    run = db.get(MacroRefreshRun, run_id)
    if not run:
        raise ValueError("宏观更新任务不存在。")
    company = db.get(Company, run.company_id)
    if not company:
        raise ValueError("公司不存在。")
    run.status = "running"
    run.message = "正在识别企业行业"
    db.add(run)
    db.commit()
    try:
        industry = _resolve_industry(db, company)
        run = db.get(MacroRefreshRun, run.id)
        run.progress_current = 1
        run.message = "正在更新宏观与行业数据"
        db.add(run)
        db.commit()
        inserted, updated, results, failed = _collect_indicators(db, run)
        try:
            policy_inserted, policy_updated, policy_result = _collect_ndrc_policy(db, industry)
            inserted += policy_inserted
            updated += policy_updated
            results.append(policy_result)
        except Exception as exc:
            failed.append("ndrc_policy")
            results.append({"source": "ndrc_policy", "status": "failed", "message": str(exc)[:300]})
        event_inserted, event_updated = _generate_indicator_events(db, industry)
        inserted += event_inserted
        updated += event_updated
        run = db.get(MacroRefreshRun, run.id)
        successful = any(item.get("status") in {"success", "no_hit"} for item in results)
        run.status = "partial" if failed and successful else "failed" if failed else "completed"
        run.progress_current = run.progress_total
        run.inserted_count = inserted
        run.updated_count = updated
        run.failed_sources = failed
        run.source_results = results
        run.message = (
            f"更新完成：新增{inserted}条，更新{updated}条"
            + (f"；{len(failed)}个来源暂不可用" if failed else "")
        )
        run.completed_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        run = db.get(MacroRefreshRun, run_id)
        run.status = "failed"
        run.message = str(exc)[:500]
        run.failed_sources = ["refresh_pipeline"]
        run.completed_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        return run


def refresh_macro_data_in_new_session(run_id: Any) -> None:
    with SessionLocal() as db:
        refresh_macro_data(db, run_id)
