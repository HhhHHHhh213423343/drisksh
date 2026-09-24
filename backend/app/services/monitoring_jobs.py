from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import (
    AnalysisJob,
    AnalysisRun,
    Company,
    CompanyProfileSnapshot,
    MonthlyReport,
    MacroRefreshRun,
    RelatedEntity,
)
from app.services.akshare_profile import refresh_company_akshare_profile
from app.services.authoritative_ingestion import collect_authoritative_sources
from app.services.document_ingestion import parse_document
from app.services.generic_ingestion import DailyIngestionService
from app.services.monthly_reports import generate_monthly_report
from app.services.macro_ingestion import MACRO_INDICATORS, refresh_macro_data
from app.services.qichacha_profile import refresh_company_qichacha_profile
from app.services.rule_engine import evaluate_rules


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    analysis_run_id: Any | None = None,
) -> AnalysisJob:
    job = AnalysisJob(
        analysis_run_id=analysis_run_id,
        job_type=job_type,
        status="queued",
        payload=payload,
        result={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _set_run_progress(
    db: Session,
    run: AnalysisRun,
    *,
    stage: str,
    current: int,
    source_status: dict[str, Any] | None = None,
) -> None:
    run.current_stage = stage
    run.progress_current = current
    if source_status is not None:
        run.source_status = source_status
    db.add(run)
    db.commit()


def _discover_related_entities(db: Session, company: Company) -> int:
    profile = company.company_profile or {}
    qichacha = profile.get("qichacha_profile") or {}
    candidates: list[tuple[str, str, str, dict[str, Any]]] = []
    for shareholder in qichacha.get("shareholders") or []:
        name = str(shareholder.get("name") or "").strip()
        if name:
            candidates.append((name, "shareholder", "qichacha_profile", shareholder))
    for person in qichacha.get("major_personnel") or []:
        name = str(person.get("name") or "").strip()
        if name:
            candidates.append((name, "key_person", "qichacha_profile", person))
    inserted = 0
    for name, relation_type, source, metadata in candidates:
        existing = db.execute(
            select(RelatedEntity)
            .where(RelatedEntity.company_id == company.id)
            .where(RelatedEntity.name == name)
            .where(RelatedEntity.relation_type == relation_type)
        ).scalar_one_or_none()
        if not existing:
            db.add(
                RelatedEntity(
                    company_id=company.id,
                    name=name,
                    relation_type=relation_type,
                    status="candidate",
                    source=source,
                    metadata_payload=metadata,
                )
            )
            inserted += 1
    db.commit()
    return inserted


def _confirmed_related_companies(db: Session, company: Company) -> list[Company]:
    entities = list(
        db.execute(
            select(RelatedEntity)
            .where(RelatedEntity.company_id == company.id)
            .where(RelatedEntity.status == "confirmed")
            .where(RelatedEntity.relation_type != "key_person")
            .order_by(RelatedEntity.created_at.asc())
            .limit(10)
        ).scalars()
    )
    companies: list[Company] = []
    for entity in entities:
        related = db.execute(
            select(Company).where(Company.name == entity.name)
        ).scalar_one_or_none()
        if not related:
            related = Company(
                name=entity.name,
                company_profile={
                    "monitoring_parent_id": str(company.id),
                    "monitoring_relation_type": entity.relation_type,
                },
            )
            db.add(related)
            db.flush()
        companies.append(related)
    db.commit()
    return companies


def _run_analysis_job(db: Session, job: AnalysisJob) -> dict[str, Any]:
    run = db.get(AnalysisRun, job.analysis_run_id)
    if not run:
        raise ValueError("分析任务不存在。")
    company = db.get(Company, run.company_id)
    if not company:
        raise ValueError("公司不存在。")
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.progress_total = 5
    source_status: dict[str, Any] = {}
    _set_run_progress(db, run, stage="refresh_company_profile", current=1)

    profile_snapshot = db.execute(
        select(CompanyProfileSnapshot)
        .where(CompanyProfileSnapshot.company_id == company.id)
        .order_by(CompanyProfileSnapshot.captured_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    source_status["qyyjt_company_profile"] = (
        {
            "status": "success",
            "captured_at": profile_snapshot.captured_at.isoformat(),
            "module_statuses": profile_snapshot.module_statuses or {},
            "evidence_import": (profile_snapshot.normalized_data or {}).get("evidence_import", {}),
        }
        if profile_snapshot
        else {
            "status": "not_connected",
            "message": "尚未上传企业预警通八模块快照。",
        }
    )

    try:
        company = asyncio.run(refresh_company_akshare_profile(db, company, force=True))
        profile = (company.company_profile or {}).get("akshare_profile") or {}
        source_status["akshare"] = {
            "status": "success" if profile.get("status") == "available" else "no_hit",
            "message": profile.get("message", ""),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        db.rollback()
        company = db.get(Company, run.company_id)
        source_status["akshare"] = {"status": "failed", "message": str(exc)[:500]}
    try:
        company = asyncio.run(
            refresh_company_qichacha_profile(db, company, force=True)
        )
        profile = (company.company_profile or {}).get("qichacha_profile") or {}
        source_status["qichacha_api"] = {
            "status": "success" if profile.get("status") == "available" else "not_connected",
            "message": profile.get("message", ""),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        db.rollback()
        company = db.get(Company, run.company_id)
        source_status["qichacha_api"] = {"status": "failed", "message": str(exc)[:500]}
    _set_run_progress(
        db, run, stage="collect_public_sources", current=2, source_status=source_status
    )

    macro_run = MacroRefreshRun(
        company_id=company.id,
        status="queued",
        progress_total=len(MACRO_INDICATORS) + 3,
        message="等待更新",
    )
    db.add(macro_run)
    db.commit()
    macro_run = refresh_macro_data(db, macro_run.id)
    source_status["macro_industry"] = {
        "status": macro_run.status,
        "inserted_count": macro_run.inserted_count,
        "updated_count": macro_run.updated_count,
        "failures": macro_run.failed_sources or [],
    }

    try:
        ingestion = asyncio.run(
            DailyIngestionService(db).run(
                company_ids=[company.id],
                max_results_per_source=int(job.payload.get("max_results_per_source") or 8),
                dry_run=False,
            )
        )
        source_status["public_connectors"] = {
            "status": "partial" if ingestion.failures else "success",
            "inserted_count": ingestion.inserted_count,
            "raw_count": ingestion.total_raw_count,
            "failures": ingestion.failures or [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        db.rollback()
        source_status["public_connectors"] = {
            "status": "failed",
            "message": str(exc)[:500],
        }
    try:
        authoritative = collect_authoritative_sources(
            db,
            company_id=company.id,
            max_documents=int(job.payload.get("max_documents") or 60),
            mode="daily",
            lookback_days=int(job.payload.get("lookback_days") or 30),
        )
        source_status["authoritative_sources"] = {
            "status": "success",
            "document_count": authoritative.get("document_count", 0),
            "ingested_event_count": authoritative.get("ingested_event_count", 0),
            "manual_review_count": authoritative.get("manual_review_count", 0),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        db.rollback()
        source_status["authoritative_sources"] = {
            "status": "failed",
            "message": str(exc)[:500],
        }
    _set_run_progress(
        db, run, stage="discover_related_entities", current=3, source_status=source_status
    )
    company = db.get(Company, run.company_id)
    related_count = _discover_related_entities(db, company)
    confirmed_related = _confirmed_related_companies(db, company)
    if confirmed_related:
        try:
            related_ingestion = asyncio.run(
                DailyIngestionService(db).run(
                    company_ids=[item.id for item in confirmed_related],
                    max_results_per_source=3,
                    dry_run=False,
                )
            )
            source_status["confirmed_related_entities"] = {
                "status": "partial" if related_ingestion.failures else "success",
                "company_count": len(confirmed_related),
                "inserted_count": related_ingestion.inserted_count,
                "failures": related_ingestion.failures or [],
            }
            related_authoritative = []
            for related_company in confirmed_related:
                try:
                    result = collect_authoritative_sources(
                        db,
                        company_id=related_company.id,
                        max_documents=20,
                        mode="daily",
                        lookback_days=int(job.payload.get("lookback_days") or 30),
                    )
                    related_authoritative.append(
                        {
                            "company_name": related_company.name,
                            "status": "success",
                            "ingested_event_count": result.get("ingested_event_count", 0),
                        }
                    )
                except Exception as exc:
                    db.rollback()
                    related_authoritative.append(
                        {
                            "company_name": related_company.name,
                            "status": "failed",
                            "message": str(exc)[:300],
                        }
                    )
            source_status["confirmed_related_entities"]["authoritative"] = related_authoritative
        except Exception as exc:
            db.rollback()
            source_status["confirmed_related_entities"] = {
                "status": "failed",
                "company_count": len(confirmed_related),
                "message": str(exc)[:500],
            }

    _set_run_progress(db, run, stage="evaluate_rules", current=4)
    evaluation = evaluate_rules(db, run.id)
    run = db.get(AnalysisRun, run.id)
    run.status = "completed"
    run.current_stage = "completed"
    run.progress_current = 5
    run.finished_at = datetime.now(timezone.utc)
    run.source_status = source_status
    run.summary = {
        **(run.summary or {}),
        "related_entity_candidates_added": related_count,
        "fresh_collection": True,
    }
    db.add(run)
    db.commit()
    return {"analysis_run_id": str(run.id), "evaluation": evaluation}


def _run_job(db: Session, job: AnalysisJob) -> dict[str, Any]:
    if job.job_type == "analysis":
        return _run_analysis_job(db, job)
    if job.job_type == "parse_document":
        document = parse_document(db, UUID(str(job.payload["document_id"])))
        return {
            "document_id": str(document.id),
            "status": document.status,
            "metadata": document.parse_metadata or {},
        }
    if job.job_type == "generate_monthly_report":
        report = generate_monthly_report(db, UUID(str(job.payload["report_id"])))
        return {"report_id": str(report.id), "status": report.status}
    raise ValueError(f"未知任务类型：{job.job_type}")


def process_job_by_id(job_id: Any) -> None:
    if isinstance(job_id, str):
        job_id = UUID(job_id)
    with SessionLocal() as db:
        job = db.get(AnalysisJob, job_id)
        if not job or job.status not in {"queued", "running"}:
            return
        if job.status == "queued":
            job.status = "running"
            job.attempts = int(job.attempts or 0) + 1
            job.locked_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
        try:
            result = _run_job(db, job)
            job = db.get(AnalysisJob, job.id)
            job.status = "completed"
            job.result = result
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(AnalysisJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:4000]
                job.completed_at = datetime.now(timezone.utc)
                db.add(job)
            if job and job.analysis_run_id:
                run = db.get(AnalysisRun, job.analysis_run_id)
                if run:
                    run.status = "failed"
                    run.current_stage = "failed"
                    run.error_message = str(exc)[:4000]
                    run.finished_at = datetime.now(timezone.utc)
                    db.add(run)
            report_id = (job.payload or {}).get("report_id") if job else None
            if report_id:
                report = db.get(MonthlyReport, report_id)
                if report:
                    report.status = "failed"
                    report.summary = str(exc)[:2000]
                    db.add(report)
            db.commit()


def claim_next_job(db: Session) -> AnalysisJob | None:
    statement = (
        select(AnalysisJob)
        .where(AnalysisJob.status == "queued")
        .order_by(AnalysisJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = db.execute(statement).scalar_one_or_none()
    if not job:
        return None
    job.status = "running"
    job.attempts = int(job.attempts or 0) + 1
    job.locked_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_worker_forever(poll_seconds: float = 2.0) -> None:
    with SessionLocal() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        stale_jobs = list(
            db.execute(
                select(AnalysisJob)
                .where(AnalysisJob.status == "running")
                .where(AnalysisJob.locked_at < cutoff)
            ).scalars()
        )
        for job in stale_jobs:
            if int(job.attempts or 0) >= 3:
                job.status = "failed"
                job.error_message = "任务在 worker 重启后仍超过最大重试次数。"
                job.completed_at = datetime.now(timezone.utc)
            else:
                job.status = "queued"
                job.locked_at = None
            db.add(job)
        db.commit()
    while True:
        with SessionLocal() as db:
            job = claim_next_job(db)
            job_id = job.id if job else None
        if job_id:
            process_job_by_id(job_id)
        else:
            time.sleep(poll_seconds)
