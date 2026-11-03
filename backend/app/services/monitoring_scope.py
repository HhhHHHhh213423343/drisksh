from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, RelatedEntity


SCOPE_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "monitoring_scopes.json"
SCOPE_SOURCE = "official_scope"

PARTNER_RELATIONS = {"partner", "customer"}


@lru_cache(maxsize=1)
def _scope_payload() -> dict[str, Any]:
    return json.loads(SCOPE_DATA_PATH.read_text(encoding="utf-8"))


def official_scope(company_name: str) -> dict[str, Any] | None:
    for item in _scope_payload().get("companies", []):
        if item.get("company_name") == company_name:
            return item
    return None


def ensure_official_related_entities(db: Session, company: Company) -> int:
    """把客户确认的监测实体清单写为已确认关联主体，返回新增或更新的数量。"""

    scope = official_scope(company.name)
    if not scope:
        return 0
    source_name = str(_scope_payload().get("source_name") or "")
    changed = 0
    for order, item in enumerate(scope.get("entities") or [], start=1):
        name = str(item["name"]).strip()
        relation_type = str(item["relation_type"])
        metadata = {
            "relation_note": item.get("relation_note") or "",
            "sentiment_group": item.get("sentiment_group") or "",
            "scope_order": order,
            "scope_source": source_name,
        }
        entity = db.execute(
            select(RelatedEntity)
            .where(RelatedEntity.company_id == company.id)
            .where(RelatedEntity.name == name)
            .where(RelatedEntity.relation_type == relation_type)
        ).scalar_one_or_none()
        if entity is None:
            entity = RelatedEntity(
                company_id=company.id,
                name=name,
                relation_type=relation_type,
                metadata_payload={},
            )
        current = entity.metadata_payload or {}
        if (
            entity.status == "confirmed"
            and all(current.get(key) == value for key, value in metadata.items())
        ):
            continue
        entity.status = "confirmed"
        entity.source = SCOPE_SOURCE
        entity.metadata_payload = {**current, **metadata}
        entity.confirmed_by = entity.confirmed_by or SCOPE_SOURCE
        entity.confirmed_at = entity.confirmed_at or datetime.now(timezone.utc)
        db.add(entity)
        changed += 1
    if changed:
        db.commit()
    return changed


def scope_order(entity: RelatedEntity) -> tuple[int, str]:
    # 正式清单内的主体按客户清单顺序排列，其余已确认主体排在后面。
    order = (entity.metadata_payload or {}).get("scope_order")
    return (int(order) if isinstance(order, int) else 10_000, entity.name)


def confirmed_entities(db: Session, company: Company) -> list[RelatedEntity]:
    entities = db.execute(
        select(RelatedEntity)
        .where(RelatedEntity.company_id == company.id)
        .where(RelatedEntity.status == "confirmed")
        .where(RelatedEntity.relation_type != "key_person")
    ).scalars()
    return sorted(entities, key=scope_order)
