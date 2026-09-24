from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditLog


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def add_audit_log(
    db: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: Any,
    before: dict | list | None = None,
    after: dict | list | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_payload=_json_safe(before or {}),
        after_payload=_json_safe(after or {}),
    )
    db.add(entry)
    return entry
