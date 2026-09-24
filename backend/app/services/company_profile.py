from __future__ import annotations

import base64
import hashlib
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Company, CompanyProfileRun, CompanyProfileSnapshot
from app.services.company_profile_evidence import ingest_company_profile_snapshot


PROFILE_COMPANY_NAME = "上海携程金融信息服务有限公司"
# 首次标定来自已验收样表的 8 个来源 URL；这是公开企业标识，不是登录凭证。
PROFILE_COMPANY_CODE = "C71376C50868F97142E2EDD20C553765"
PROFILE_COMPANY_DIRECT_ALIASES = (
    PROFILE_COMPANY_NAME,
    "携程金融信息服务有限公司",
    "上海携程金融信息服务",
    "携程金融信息服务",
)
PROFILE_COMPANY_CONFIRM_ALIASES = (
    "携程金融",
    "上海携程金融",
)
PROFILE_MODULE_KEYS = (
    "overview",
    "penalties",
    "dynamic_monitor",
    "business_changes",
    "executives",
    "shareholders",
    "investments",
    "subsidiaries",
)
ACTIVE_STATUSES = {"queued", "running", "waiting_for_login", "waiting_for_captcha"}
COMPLETE_MODULE_STATUSES = {"PASS", "EMPTY_VALID"}
CACHE_TTL = timedelta(hours=24)
LEASE_TTL = timedelta(minutes=3)
RETENTION = timedelta(days=365)
MAX_EXCEL_BYTES = 20 * 1024 * 1024


def normalize_company_name(value: str) -> str:
    """只消除不影响企业身份的排版差异，不做任意子串模糊匹配。"""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


_DIRECT_ALIAS_KEYS = {
    normalize_company_name(alias) for alias in PROFILE_COMPANY_DIRECT_ALIASES
}
_CONFIRM_ALIAS_KEYS = {
    normalize_company_name(alias) for alias in PROFILE_COMPANY_CONFIRM_ALIASES
}


def resolve_profile_company_name(value: str) -> str | None:
    """将已审批的安全别名映射为唯一法定名称。"""

    return (
        PROFILE_COMPANY_NAME
        if normalize_company_name(value) in _DIRECT_ALIAS_KEYS
        else None
    )


def profile_company_suggestion(value: str) -> dict[str, Any] | None:
    """对较短但可明确联想的输入返回待用户确认的候选。"""

    key = normalize_company_name(value)
    if key in _DIRECT_ALIAS_KEYS:
        return {
            "name": PROFILE_COMPANY_NAME,
            "company_code": PROFILE_COMPANY_CODE,
            "requires_confirmation": False,
        }
    controlled_prefix = (
        len(key) >= 4
        and (key.startswith("携程金融") or key.startswith("上海携程金融"))
        and any(key in alias_key for alias_key in _DIRECT_ALIAS_KEYS)
    )
    if key in _CONFIRM_ALIAS_KEYS or controlled_prefix:
        return {
            "name": PROFILE_COMPANY_NAME,
            "company_code": PROFILE_COMPANY_CODE,
            "requires_confirmation": True,
        }
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def profile_config(company: Company) -> dict[str, Any]:
    profile = dict(company.company_profile or {})
    return dict(profile.get("qyyjt_profile") or {})


def enable_supported_company(db: Session, company: Company) -> bool:
    config = profile_config(company)
    enabled = company.name == PROFILE_COMPANY_NAME or bool(config.get("enabled"))
    if not enabled:
        return False
    changed = False
    if config.get("enabled") is not True:
        config["enabled"] = True
        changed = True
    if company.name == PROFILE_COMPANY_NAME and not config.get("company_code"):
        config["company_code"] = PROFILE_COMPANY_CODE
        changed = True
    if "sop_version" not in config:
        config["sop_version"] = "QYYJT-COMPANY-PROFILE-V3.2"
        changed = True
    if changed:
        profile = dict(company.company_profile or {})
        profile["qyyjt_profile"] = config
        company.company_profile = profile
        db.add(company)
        db.commit()
        db.refresh(company)
    return True


def serialize_run(run: CompanyProfileRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "company_id": run.company_id,
        "status": run.status,
        "mode": run.mode,
        "force": bool(run.force),
        "worker_id": run.worker_id or "",
        "progress_current": run.progress_current or 0,
        "progress_total": run.progress_total or 8,
        "current_module": run.current_module or "",
        "error_code": run.error_code or "",
        "error_message": run.error_message or "",
        "module_statuses": run.module_statuses or {},
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "heartbeat_at": run.heartbeat_at,
    }


def serialize_snapshot(snapshot: CompanyProfileSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "id": snapshot.id,
        "company_id": snapshot.company_id,
        "run_id": snapshot.run_id,
        "captured_at": snapshot.captured_at,
        "sop_version": snapshot.sop_version,
        "module_statuses": snapshot.module_statuses or {},
        "normalized_data": snapshot.normalized_data or {},
        "excel_filename": snapshot.excel_filename,
        "excel_sha256": snapshot.excel_sha256,
    }


def latest_snapshot(db: Session, company_id: UUID) -> CompanyProfileSnapshot | None:
    return db.execute(
        select(CompanyProfileSnapshot)
        .where(CompanyProfileSnapshot.company_id == company_id)
        .order_by(CompanyProfileSnapshot.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def latest_run(db: Session, company_id: UUID) -> CompanyProfileRun | None:
    return db.execute(
        select(CompanyProfileRun)
        .where(CompanyProfileRun.company_id == company_id)
        .order_by(CompanyProfileRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def active_run(db: Session, company_id: UUID) -> CompanyProfileRun | None:
    return db.execute(
        select(CompanyProfileRun)
        .where(
            CompanyProfileRun.company_id == company_id,
            CompanyProfileRun.status.in_(ACTIVE_STATUSES),
        )
        .order_by(CompanyProfileRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def create_or_reuse_run(
    db: Session,
    company: Company,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if not enable_supported_company(db, company):
        return {"enabled": False, "reused": False, "run": None, "snapshot": None}

    existing = active_run(db, company.id)
    if existing and existing.status in {"queued", "running"}:
        return {
            "enabled": True,
            "reused": True,
            "run": serialize_run(existing),
            "snapshot": serialize_snapshot(latest_snapshot(db, company.id)),
        }
    if existing and not force:
        return {
            "enabled": True,
            "reused": True,
            "run": serialize_run(existing),
            "snapshot": serialize_snapshot(latest_snapshot(db, company.id)),
        }
    if existing and force:
        existing.status = "failed"
        existing.error_code = "retry_requested"
        existing.error_message = "用户处理登录或验证码后重新发起采集。"
        existing.finished_at = utcnow()

    snapshot = latest_snapshot(db, company.id)
    captured_at = _aware(snapshot.captured_at) if snapshot else None
    if snapshot and not force and captured_at and captured_at >= utcnow() - CACHE_TTL:
        db.commit()
        return {
            "enabled": True,
            "reused": True,
            "run": None,
            "snapshot": serialize_snapshot(snapshot),
        }

    run = CompanyProfileRun(
        company_id=company.id,
        status="queued",
        mode="full",
        force=force,
        progress_current=0,
        progress_total=len(PROFILE_MODULE_KEYS),
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = active_run(db, company.id)
        if concurrent is None:
            raise
        return {
            "enabled": True,
            "reused": True,
            "run": serialize_run(concurrent),
            "snapshot": serialize_snapshot(latest_snapshot(db, company.id)),
        }
    db.refresh(run)
    return {
        "enabled": True,
        "reused": False,
        "run": serialize_run(run),
        "snapshot": serialize_snapshot(snapshot),
    }


def get_profile_state(db: Session, company: Company) -> dict[str, Any]:
    enabled = enable_supported_company(db, company)
    current = active_run(db, company.id) if enabled else None
    latest = latest_run(db, company.id) if enabled else None
    snapshot = latest_snapshot(db, company.id) if enabled else None
    config = profile_config(company)
    return {
        "enabled": enabled,
        "company_name": company.name,
        "qyyjt_company_code": str(config.get("company_code") or ""),
        "active_run": serialize_run(current),
        "latest_run": serialize_run(latest),
        "latest_snapshot": serialize_snapshot(snapshot),
    }


def claim_next_run(db: Session, worker_id: str) -> dict[str, Any] | None:
    now = utcnow()
    run = db.execute(
        select(CompanyProfileRun)
        .where(
            or_(
                CompanyProfileRun.status == "queued",
                (
                    (CompanyProfileRun.status == "running")
                    & (CompanyProfileRun.lease_expires_at < now)
                ),
            )
        )
        .order_by(CompanyProfileRun.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if run is None:
        return None

    run.status = "running"
    run.worker_id = worker_id
    run.claimed_at = run.claimed_at or now
    run.started_at = run.started_at or now
    run.heartbeat_at = now
    run.lease_expires_at = now + LEASE_TTL
    run.error_code = ""
    run.error_message = ""
    db.commit()
    db.refresh(run)

    company = db.get(Company, run.company_id)
    if company is None:
        run.status = "failed"
        run.error_code = "company_missing"
        run.error_message = "任务对应企业不存在。"
        run.finished_at = utcnow()
        db.commit()
        return None
    return {
        "run": serialize_run(run),
        "company": {
            "id": str(company.id),
            "name": company.name,
            "credit_code": company.credit_code,
            "qyyjt_profile": profile_config(company),
        },
    }


def _owned_running_run(db: Session, run_id: UUID, worker_id: str) -> CompanyProfileRun:
    run = db.get(CompanyProfileRun, run_id)
    if run is None:
        raise ValueError("采集任务不存在。")
    if run.status != "running":
        raise ValueError(f"采集任务当前状态为 {run.status}，不能更新。")
    if run.worker_id != worker_id:
        raise PermissionError("采集任务已由其他本机助手领取。")
    return run


def heartbeat_run(
    db: Session,
    run_id: UUID,
    worker_id: str,
    *,
    progress_current: int,
    current_module: str,
    module_statuses: dict,
) -> dict[str, Any]:
    run = _owned_running_run(db, run_id, worker_id)
    now = utcnow()
    run.heartbeat_at = now
    run.lease_expires_at = now + LEASE_TTL
    run.progress_current = progress_current
    run.current_module = current_module
    run.module_statuses = module_statuses
    db.commit()
    db.refresh(run)
    return serialize_run(run) or {}


def complete_run(db: Session, run_id: UUID, payload: Any) -> dict[str, Any]:
    run = _owned_running_run(db, run_id, payload.worker_id)
    statuses = dict(payload.module_statuses or {})
    missing = [key for key in PROFILE_MODULE_KEYS if key not in statuses]
    invalid = {
        key: statuses.get(key)
        for key in PROFILE_MODULE_KEYS
        if statuses.get(key) not in COMPLETE_MODULE_STATUSES
    }
    if missing or invalid:
        raise ValueError(
            f"八模块尚未全部通过校验。缺失：{missing or '无'}；失败：{invalid or '无'}。"
        )

    try:
        excel_bytes = base64.b64decode(payload.excel_base64, validate=True)
    except Exception as exc:
        raise ValueError("Excel 内容不是有效的 Base64 数据。") from exc
    if not excel_bytes or len(excel_bytes) > MAX_EXCEL_BYTES:
        raise ValueError("Excel 文件为空或超过 20MB 限制。")
    digest = hashlib.sha256(excel_bytes).hexdigest()
    if digest.lower() != payload.excel_sha256.lower():
        raise ValueError("Excel SHA-256 校验失败。")
    if not payload.excel_filename.lower().endswith(".xlsx"):
        raise ValueError("正式交付文件必须是 .xlsx。")

    previous_snapshot = latest_snapshot(db, run.company_id)
    snapshot = CompanyProfileSnapshot(
        company_id=run.company_id,
        run_id=run.id,
        captured_at=payload.captured_at,
        sop_version=payload.sop_version,
        module_statuses=statuses,
        normalized_data=payload.normalized_data,
        raw_data=payload.raw_data,
        excel_filename=payload.excel_filename,
        excel_sha256=digest,
        excel_file=excel_bytes,
    )
    db.add(snapshot)
    db.flush()
    evidence_import = ingest_company_profile_snapshot(
        db,
        snapshot,
        previous_snapshot,
    )
    snapshot.normalized_data = {
        **(snapshot.normalized_data or {}),
        "evidence_import": evidence_import,
    }
    db.add(snapshot)
    run.status = "completed"
    run.progress_current = len(PROFILE_MODULE_KEYS)
    run.current_module = ""
    run.module_statuses = statuses
    run.finished_at = utcnow()
    run.lease_expires_at = None

    company = db.get(Company, run.company_id)
    if company is not None and payload.company_code:
        profile = dict(company.company_profile or {})
        config = dict(profile.get("qyyjt_profile") or {})
        config.update(
            {
                "enabled": True,
                "company_code": payload.company_code,
                "sop_version": payload.sop_version,
                "last_captured_at": payload.captured_at.isoformat(),
            }
        )
        profile["qyyjt_profile"] = config
        company.company_profile = profile

    cutoff = utcnow() - RETENTION
    db.execute(
        delete(CompanyProfileSnapshot)
        .where(
            CompanyProfileSnapshot.company_id == run.company_id,
            CompanyProfileSnapshot.captured_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(snapshot)
    return serialize_snapshot(snapshot) or {}


def fail_run(db: Session, run_id: UUID, payload: Any) -> dict[str, Any]:
    run = _owned_running_run(db, run_id, payload.worker_id)
    run.status = payload.status
    run.error_code = payload.error_code
    run.error_message = payload.error_message
    run.module_statuses = payload.module_statuses
    run.finished_at = utcnow()
    run.lease_expires_at = None
    db.commit()
    db.refresh(run)
    return serialize_run(run) or {}
