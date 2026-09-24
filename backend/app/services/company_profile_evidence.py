from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CompanyProfileSnapshot, RelatedEntity, RiskEvent


SOURCE_NAME = "企业预警通"


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sections(module: dict[str, Any] | None) -> Iterable[tuple[str, list[str], list[Any]]]:
    for section in (module or {}).get("sections") or []:
        columns = [_text(value) for value in section.get("columns") or []]
        rows = section.get("rows") or []
        if columns and isinstance(rows, list):
            yield _text(section.get("title")), columns, rows


def _records(module: dict[str, Any] | None) -> Iterable[tuple[str, int, dict[str, str]]]:
    for section_title, columns, rows in _sections(module):
        for row_index, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                record = {_text(key): _text(value) for key, value in row.items()}
            elif isinstance(row, list):
                record = {
                    column: _text(row[index]) if index < len(row) else ""
                    for index, column in enumerate(columns)
                }
            else:
                continue
            if any(record.values()):
                yield section_title, row_index, record


def _first(record: dict[str, str], *names: str) -> str:
    for name in names:
        exact = record.get(name, "")
        if exact:
            return exact
        match = next(
            (value for key, value in record.items() if name in key and value), ""
        )
        if match:
            return match
    return ""


def _parse_date(value: str, fallback: datetime) -> datetime:
    normalized = _text(value).replace("年", "-").replace("月", "-").replace("日", "")
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", normalized)
    if not match:
        match = re.search(r"(20\d{2})(\d{2})(\d{2})", normalized)
    if not match:
        return fallback
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return fallback


def _content(record: dict[str, str]) -> str:
    return "；".join(f"{key}：{value}" for key, value in record.items() if value)


def _event_category(category: str, title: str, body: str) -> str:
    text = f"{category} {title} {body}"
    if any(token in text for token in ("诉讼", "仲裁", "被执行", "失信", "处罚", "监管", "司法")):
        return "legal"
    if any(token in text for token in ("舆情", "投诉", "热搜", "消费者", "品牌")):
        return "brand"
    if any(token in text for token in ("债务", "融资", "资产", "利润", "现金流", "财务")):
        return "finance"
    if any(token in text for token in ("股东", "高管", "董事", "法定代表人", "实际控制人", "工商变更")):
        return "governance"
    return "operations"


def _severity(value: str) -> str:
    return "important" if any(token in value for token in ("重要", "高", "严重", "重大")) else "general"


def _sentiment(value: str) -> str:
    if any(token in value for token in ("负面", "消极", "-1")):
        return "negative"
    if any(token in value for token in ("正面", "积极")):
        return "positive"
    return "neutral"


def _dedupe_hash(company_id: Any, module_key: str, title: str, occurred_at: datetime, content: str) -> str:
    payload = "\u0000".join(
        [str(company_id), module_key, title, occurred_at.date().isoformat(), content]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_url(module: dict[str, Any], company_code: str, module_key: str) -> str:
    return _text(module.get("source_url")) or (
        f"https://www.qyyjt.cn/detail/enterprise/overview?code={company_code}"
        f"&type=company#{module_key}"
    )


def _existing_hashes(db: Session, company_id: Any) -> set[str]:
    rows = db.execute(
        select(RiskEvent.extra_payload)
        .where(RiskEvent.company_id == company_id)
        .where(RiskEvent.source_name == SOURCE_NAME)
    ).scalars()
    return {
        str(payload.get("dedupe_hash"))
        for payload in rows
        if isinstance(payload, dict) and payload.get("dedupe_hash")
    }


def _append_event(
    db: Session,
    *,
    snapshot: CompanyProfileSnapshot,
    module_key: str,
    module: dict[str, Any],
    section_title: str,
    row_index: int,
    title: str,
    content: str,
    occurred_at: datetime,
    category: str,
    severity: str = "general",
    sentiment: str = "neutral",
    existing_hashes: set[str],
) -> bool:
    title = _text(title)[:255] or f"{module_key}更新"
    content = _text(content) or title
    event_hash = _dedupe_hash(snapshot.company_id, module_key, title, occurred_at, content)
    if event_hash in existing_hashes:
        return False
    normalized = snapshot.normalized_data or {}
    db.add(
        RiskEvent(
            company_id=snapshot.company_id,
            category=category,
            severity=severity,
            title=title,
            content=content,
            source_url=_source_url(module, _text(normalized.get("company_code")), module_key),
            source_name=SOURCE_NAME,
            occurred_at=occurred_at,
            sentiment=sentiment,
            extra_payload={
                "source_type": "qyyjt_company_profile",
                "profile_snapshot_id": str(snapshot.id),
                "module": module_key,
                "section": section_title,
                "row_index": row_index,
                "dedupe_hash": event_hash,
            },
        )
    )
    existing_hashes.add(event_hash)
    return True


def _upsert_related_entity(
    db: Session,
    *,
    snapshot: CompanyProfileSnapshot,
    name: str,
    relation_type: str,
    module_key: str,
    row: dict[str, str],
) -> bool:
    name = _text(name)
    if not name or name == (snapshot.normalized_data or {}).get("company_name"):
        return False
    existing = db.execute(
        select(RelatedEntity)
        .where(RelatedEntity.company_id == snapshot.company_id)
        .where(RelatedEntity.name == name)
        .where(RelatedEntity.relation_type == relation_type)
    ).scalar_one_or_none()
    if existing:
        if existing.status == "candidate":
            existing.metadata_payload = {
                **(existing.metadata_payload or {}),
                "latest_profile_snapshot_id": str(snapshot.id),
                "latest_row": row,
            }
            db.add(existing)
        return False
    db.add(
        RelatedEntity(
            company_id=snapshot.company_id,
            name=name,
            relation_type=relation_type,
            status="candidate",
            source=SOURCE_NAME,
            metadata_payload={
                "profile_snapshot_id": str(snapshot.id),
                "module": module_key,
                "row": row,
            },
        )
    )
    return True


def _identity_rows(module: dict[str, Any] | None, field_names: tuple[str, ...]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for _, _, row in _records(module):
        name = _first(row, *field_names)
        if name:
            result[name] = row
    return result


def ingest_company_profile_snapshot(
    db: Session,
    snapshot: CompanyProfileSnapshot,
    previous_snapshot: CompanyProfileSnapshot | None,
) -> dict[str, int]:
    """Convert an accepted eight-module snapshot into auditable monitoring evidence."""

    normalized = snapshot.normalized_data or {}
    modules = normalized.get("modules") or {}
    previous_modules = (previous_snapshot.normalized_data or {}).get("modules") if previous_snapshot else {}
    previous_modules = previous_modules or {}
    captured_at = snapshot.captured_at
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    existing_hashes = _existing_hashes(db, snapshot.company_id)
    event_count = 0
    related_count = 0

    dynamic = modules.get("dynamic_monitor") or {}
    for section_title, row_index, row in _records(dynamic):
        title = _first(row, "标题", "事件名称", "摘要") or "动态监测信息"
        body = _content(row)
        occurred_at = _parse_date(_first(row, "日期", "发布日期", "事件日期"), captured_at)
        source_category = _first(row, "分类", "风险类型")
        event_count += int(
            _append_event(
                db,
                snapshot=snapshot,
                module_key="dynamic_monitor",
                module=dynamic,
                section_title=section_title,
                row_index=row_index,
                title=title,
                content=body,
                occurred_at=occurred_at,
                category=_event_category(source_category, title, body),
                severity=_severity(_first(row, "重要性", "程度")),
                sentiment=_sentiment(_first(row, "正负面", "情感", "舆情倾向")),
                existing_hashes=existing_hashes,
            )
        )

    penalties = modules.get("penalties") or {}
    for section_title, row_index, row in _records(penalties):
        penalty_type = _first(row, "处罚类型", "决定文书号", "违规原因", "处罚事由")
        title = f"监管处罚：{penalty_type}" if penalty_type else "监管处罚信息"
        event_count += int(
            _append_event(
                db,
                snapshot=snapshot,
                module_key="penalties",
                module=penalties,
                section_title=section_title,
                row_index=row_index,
                title=title,
                content=_content(row),
                occurred_at=_parse_date(_first(row, "处罚日期", "披露日期", "日期"), captured_at),
                category="legal",
                severity="important",
                sentiment="negative",
                existing_hashes=existing_hashes,
            )
        )

    changes = modules.get("business_changes") or {}
    for section_title, row_index, row in _records(changes):
        project = _first(row, "变更项目", "变更事项") or "工商变更"
        body = _content(row)
        event_count += int(
            _append_event(
                db,
                snapshot=snapshot,
                module_key="business_changes",
                module=changes,
                section_title=section_title,
                row_index=row_index,
                title=f"工商变更：{project}",
                content=body,
                occurred_at=_parse_date(_first(row, "变更时间", "变更日期", "日期"), captured_at),
                category=_event_category(project, project, body),
                severity="general",
                sentiment="neutral",
                existing_hashes=existing_hashes,
            )
        )

    related_specs = (
        ("shareholders", "shareholder", ("股东名称", "股东", "企业名称")),
        ("investments", "investment", ("企业名称", "被投资企业")),
        ("subsidiaries", "subsidiary", ("企业名称", "子公司名称")),
    )
    for module_key, relation_type, field_names in related_specs:
        for _, _, row in _records(modules.get(module_key) or {}):
            related_count += int(
                _upsert_related_entity(
                    db,
                    snapshot=snapshot,
                    name=_first(row, *field_names),
                    relation_type=relation_type,
                    module_key=module_key,
                    row=row,
                )
            )

    if previous_snapshot:
        for module_key, label, names in (
            ("executives", "高管变更", ("姓名", "高管姓名")),
            ("shareholders", "股东变更", ("股东名称", "股东", "企业名称")),
        ):
            current_rows = _identity_rows(modules.get(module_key), names)
            previous_rows = _identity_rows(previous_modules.get(module_key), names)
            added = sorted(set(current_rows) - set(previous_rows))
            removed = sorted(set(previous_rows) - set(current_rows))
            changed = sorted(
                name
                for name in set(current_rows) & set(previous_rows)
                if json.dumps(current_rows[name], ensure_ascii=False, sort_keys=True)
                != json.dumps(previous_rows[name], ensure_ascii=False, sort_keys=True)
            )
            if not (added or removed or changed):
                continue
            details = []
            if added:
                details.append("新增：" + "、".join(added))
            if removed:
                details.append("退出：" + "、".join(removed))
            if changed:
                details.append("信息变更：" + "、".join(changed))
            module = modules.get(module_key) or {}
            event_count += int(
                _append_event(
                    db,
                    snapshot=snapshot,
                    module_key=module_key,
                    module=module,
                    section_title=label,
                    row_index=0,
                    title=label,
                    content=f"与上一次企业预警通快照比较，{'；'.join(details)}。",
                    occurred_at=captured_at,
                    category="governance",
                    severity="important",
                    sentiment="neutral",
                    existing_hashes=existing_hashes,
                )
            )

    return {"risk_events_inserted": event_count, "related_entities_inserted": related_count}
