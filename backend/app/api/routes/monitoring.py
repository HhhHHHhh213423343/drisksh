from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session as db_session
from app.models import (
    AnalysisJob,
    AnalysisRun,
    Company,
    CompanyRuleAssignment,
    ExtractedFact,
    MonthlyReport,
    MonitoringRule,
    RelatedEntity,
    RuleEvaluation,
    SourceDocument,
)
from app.schemas.monitoring import (
    AnalysisRunCreate,
    CompanyRulesUpdate,
    FactConfirmationRequest,
    ManualFactCreate,
    MonthlyReportCreate,
    MonthlyReportUpdate,
    RelatedEntitiesUpdate,
    RuleEvaluationReview,
)
from app.services.audit import add_audit_log
from app.services.auth import actor_from_request
from app.services.demo_scope import (
    DemoScopeError,
    canonical_company_name,
    demo_mode_enabled,
    ensure_demo_company,
)
from app.services.document_ingestion import (
    mime_type_for,
    remove_stored_document,
    safe_filename,
    store_document_bytes,
)
from app.services.monitoring_jobs import enqueue_job, process_job_by_id
from app.services.monthly_reports import (
    SECTION_LABELS,
    SECTION_ORDER,
    append_report_revision,
    export_monthly_report_xlsx,
)
from app.services.rule_seed import ensure_company_rule_assignments, ensure_default_rule_set


router = APIRouter(tags=["monitoring"])


def _require_demo_company(db: Session, company_id: Any) -> Company:
    try:
        return ensure_demo_company(db.get(Company, company_id))
    except DemoScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_demo_run(db: Session, run_id: Any) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="分析任务不存在。")
    _require_demo_company(db, run.company_id)
    return run


def _require_demo_report(db: Session, report_id: Any) -> MonthlyReport:
    report = db.get(MonthlyReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="月报不存在。")
    _require_demo_company(db, report.company_id)
    return report


def _schedule(background: BackgroundTasks, job: AnalysisJob) -> None:
    if get_settings().inline_job_execution:
        background.add_task(process_job_by_id, job.id)


def _analysis_run_payload(run: AnalysisRun, job: AnalysisJob | None = None) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "company_id": str(run.company_id),
        "status": run.status,
        "requested_period": run.requested_period,
        "current_stage": run.current_stage,
        "progress_current": run.progress_current,
        "progress_total": run.progress_total,
        "source_status": run.source_status or {},
        "summary": run.summary or {},
        "error_message": run.error_message,
        "requested_by": run.requested_by,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "job": (
            {
                "id": str(job.id),
                "status": job.status,
                "type": job.job_type,
                "error_message": job.error_message,
            }
            if job
            else None
        ),
    }


def _document_payload(document: SourceDocument) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "company_id": str(document.company_id),
        "analysis_run_id": str(document.analysis_run_id) if document.analysis_run_id else None,
        "filename": document.original_filename,
        "mime_type": document.mime_type,
        "extension": document.extension,
        "sha256": document.sha256,
        "size_bytes": document.size_bytes,
        "page_count": document.page_count,
        "status": document.status,
        "parse_error": document.parse_error,
        "parse_metadata": document.parse_metadata or {},
        "uploaded_by": document.uploaded_by,
        "created_at": document.created_at,
    }


def _fact_payload(fact: ExtractedFact) -> dict[str, Any]:
    return {
        "id": str(fact.id),
        "document_id": str(fact.document_id),
        "metric_code": fact.metric_code,
        "label": fact.label,
        "value_numeric": fact.value_numeric,
        "value_text": fact.value_text,
        "unit": fact.unit,
        "currency": fact.currency,
        "period": fact.period,
        "source_locator": fact.source_locator,
        "confidence": fact.confidence,
        "status": fact.status,
        "confirmed_by": fact.confirmed_by,
        "confirmed_at": fact.confirmed_at,
    }


def _report_payload(report: MonthlyReport) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "company_id": str(report.company_id),
        "analysis_run_id": str(report.analysis_run_id) if report.analysis_run_id else None,
        "period": report.period,
        "version": report.version,
        "status": report.status,
        "title": report.title,
        "summary": report.summary,
        "sections": report.sections or {},
        "evidence_snapshot": report.evidence_snapshot or {},
        "section_order": SECTION_ORDER,
        "section_labels": SECTION_LABELS,
        "rule_set_version": report.rule_set_version,
        "model_name": report.model_name,
        "token_usage": report.token_usage or {},
        "generated_at": report.generated_at,
        "approved_by": report.approved_by,
        "approved_at": report.approved_at,
        "created_at": report.created_at,
    }


@router.get("/settings/status")
def monitoring_settings_status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "llm_configured": bool(
            settings.llm_base_url and settings.llm_model and settings.llm_api_key
        ),
        "llm_provider": (
            "DeepSeek" if "api.deepseek.com" in settings.llm_base_url else "OpenAI-compatible"
        ),
        "llm_model": settings.llm_model,
        "search_configured": bool(os.getenv("SERPER_API_KEY", "").strip()),
        "company_profile_worker_enabled": settings.company_profile_worker_enabled,
        "inline_job_execution": settings.inline_job_execution,
        "demo_mode": demo_mode_enabled(),
        "demo_company_name": settings.demo_company_name,
        "report_as_of_date": settings.demo_report_as_of_date,
    }


@router.post("/analysis-runs", status_code=status.HTTP_202_ACCEPTED)
def create_analysis_run(
    payload: AnalysisRunCreate,
    background: BackgroundTasks,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    submitted = payload.company_name.strip()
    try:
        canonical = canonical_company_name(submitted)
    except DemoScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    company = db.execute(
        select(Company).where(Company.name == canonical)
    ).scalar_one_or_none()
    if not company:
        company = Company(name=canonical)
        db.add(company)
        db.flush()
    profile = dict(company.company_profile or {})
    if payload.stock_code:
        profile["stock_code"] = payload.stock_code.strip()
    if payload.market:
        profile["market"] = payload.market.strip()
    company.company_profile = profile
    db.add(company)
    db.commit()
    db.refresh(company)
    rule_set = ensure_company_rule_assignments(db, company.id)
    actor = actor_from_request(request)
    run = AnalysisRun(
        company_id=company.id,
        status="queued",
        requested_period=payload.requested_period,
        current_stage="queued",
        progress_total=5,
        requested_by=actor,
        summary={"rule_set_version": rule_set.version, "fresh_collection": True},
    )
    db.add(run)
    db.flush()
    job = enqueue_job(
        db,
        job_type="analysis",
        analysis_run_id=run.id,
        payload={
            "max_results_per_source": payload.max_results_per_source,
            "max_documents": payload.max_documents,
            "lookback_days": payload.lookback_days,
        },
    )
    add_audit_log(
        db,
        actor=actor,
        action="analysis_run.create",
        entity_type="analysis_run",
        entity_id=run.id,
        after={"company_id": str(company.id), "requested_period": run.requested_period},
    )
    db.commit()
    _schedule(background, job)
    return {
        "company": {"id": str(company.id), "name": company.name},
        "analysis_run": _analysis_run_payload(run, job),
    }


@router.get("/analysis-runs/{run_id}")
def get_analysis_run(
    run_id: UUID, db: Session = Depends(db_session.get_db)
) -> dict[str, Any]:
    run = _require_demo_run(db, run_id)
    job = db.execute(
        select(AnalysisJob)
        .where(AnalysisJob.analysis_run_id == run.id)
        .order_by(AnalysisJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _analysis_run_payload(run, job)


@router.get("/companies/{company_id}/analysis-runs")
def list_analysis_runs(
    company_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(db_session.get_db),
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    runs = db.execute(
        select(AnalysisRun)
        .where(AnalysisRun.company_id == company_id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
    ).scalars()
    return [_analysis_run_payload(run) for run in runs]


@router.get("/analysis-runs/{run_id}/rule-evaluations")
def list_rule_evaluations(
    run_id: UUID, db: Session = Depends(db_session.get_db)
) -> list[dict[str, Any]]:
    _require_demo_run(db, run_id)
    rows = db.execute(
        select(RuleEvaluation, MonitoringRule)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == run_id)
        .order_by(MonitoringRule.code)
    ).all()
    return [
        {
            "id": str(evaluation.id),
            "rule_id": str(rule.id),
            "rule_code": rule.code,
            "category": rule.category,
            "monitoring_point": rule.monitoring_point,
            "assessment_standard": rule.assessment_standard,
            "requirement_type": rule.requirement_type,
            "evaluation_type": rule.evaluation_type,
            "status": evaluation.status,
            "severity": evaluation.severity,
            "rationale": evaluation.rationale,
            "evidence": evaluation.evidence or [],
            "facts": evaluation.facts or [],
            "reviewer_status": evaluation.reviewer_status,
        }
        for evaluation, rule in rows
    ]


@router.get("/analysis-runs/{run_id}/risk-summary")
def analysis_risk_summary(
    run_id: UUID, db: Session = Depends(db_session.get_db)
) -> dict[str, Any]:
    run = _require_demo_run(db, run_id)
    rows = db.execute(
        select(RuleEvaluation, MonitoringRule)
        .join(MonitoringRule, MonitoringRule.id == RuleEvaluation.rule_id)
        .where(RuleEvaluation.analysis_run_id == run_id)
        .order_by(MonitoringRule.category, MonitoringRule.code)
    ).all()
    findings = [
        {
            "id": str(evaluation.id),
            "category": rule.category,
            "status": evaluation.status,
            "severity": evaluation.severity,
            "rationale": evaluation.rationale,
            "evidence": evaluation.evidence or [],
            "reviewer_status": evaluation.reviewer_status,
        }
        for evaluation, rule in rows
    ]
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["status"]] = counts.get(finding["status"], 0) + 1
    return {
        "analysis_run_id": str(run.id),
        "status": run.status,
        "period": run.requested_period,
        "counts": counts,
        "findings": findings,
        "updated_at": run.finished_at or run.updated_at,
    }


@router.get("/rule-sets/current")
def current_rule_set(db: Session = Depends(db_session.get_db)) -> dict[str, Any]:
    rule_set = ensure_default_rule_set(db)
    count = db.execute(
        select(func.count(MonitoringRule.id)).where(
            MonitoringRule.rule_set_id == rule_set.id
        )
    ).scalar_one()
    return {
        "id": str(rule_set.id),
        "name": rule_set.name,
        "version": rule_set.version,
        "status": rule_set.status,
        "source_name": rule_set.source_name,
        "source_hash": rule_set.source_hash,
        "rule_count": count,
    }


@router.patch("/rule-evaluations/{evaluation_id}")
def review_rule_evaluation(
    evaluation_id: UUID,
    payload: RuleEvaluationReview,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    evaluation = db.get(RuleEvaluation, evaluation_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="规则评估不存在。")
    before = {
        "status": evaluation.status,
        "severity": evaluation.severity,
        "rationale": evaluation.rationale,
        "reviewer_status": evaluation.reviewer_status,
    }
    actor = actor_from_request(request)
    evaluation.status = payload.status
    evaluation.severity = payload.severity
    evaluation.rationale = payload.rationale
    evaluation.reviewer_status = "reviewed"
    evaluation.reviewed_by = actor
    evaluation.reviewed_at = datetime.now(timezone.utc)
    db.add(evaluation)
    add_audit_log(
        db,
        actor=actor,
        action="rule_evaluation.review",
        entity_type="rule_evaluation",
        entity_id=evaluation.id,
        before=before,
        after={
            "status": evaluation.status,
            "severity": evaluation.severity,
            "rationale": evaluation.rationale,
            "reviewer_status": evaluation.reviewer_status,
        },
    )
    db.commit()
    return {"id": str(evaluation.id), "status": evaluation.status, "reviewer_status": evaluation.reviewer_status}


@router.get("/companies/{company_id}/rules")
def company_rules(
    company_id: UUID, db: Session = Depends(db_session.get_db)
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    ensure_company_rule_assignments(db, company_id)
    rows = db.execute(
        select(CompanyRuleAssignment, MonitoringRule)
        .join(MonitoringRule, MonitoringRule.id == CompanyRuleAssignment.rule_id)
        .where(CompanyRuleAssignment.company_id == company_id)
        .order_by(MonitoringRule.code)
    ).all()
    return [
        {
            "rule_code": rule.code,
            "category": rule.category,
            "monitoring_point": rule.monitoring_point,
            "assessment_standard": rule.assessment_standard,
            "evaluation_type": rule.evaluation_type,
            "enabled": assignment.enabled,
            "override": assignment.override or {},
        }
        for assignment, rule in rows
    ]


@router.patch("/companies/{company_id}/rules")
def update_company_rules(
    company_id: UUID,
    payload: CompanyRulesUpdate,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    ensure_company_rule_assignments(db, company_id)
    actor = actor_from_request(request)
    for update in payload.rules:
        row = db.execute(
            select(CompanyRuleAssignment, MonitoringRule)
            .join(MonitoringRule, MonitoringRule.id == CompanyRuleAssignment.rule_id)
            .where(CompanyRuleAssignment.company_id == company_id)
            .where(MonitoringRule.code == update.rule_code)
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"规则{update.rule_code}不存在。")
        assignment, rule = row
        before = {"enabled": assignment.enabled, "override": assignment.override or {}}
        assignment.enabled = update.enabled
        assignment.override = update.override
        db.add(assignment)
        add_audit_log(
            db,
            actor=actor,
            action="company_rule.update",
            entity_type="company_rule_assignment",
            entity_id=assignment.id,
            before=before,
            after={"enabled": assignment.enabled, "override": assignment.override},
        )
    db.commit()
    return company_rules(company_id, db)


@router.get("/companies/{company_id}/related-entities")
def list_related_entities(
    company_id: UUID, db: Session = Depends(db_session.get_db)
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    entities = db.execute(
        select(RelatedEntity)
        .where(RelatedEntity.company_id == company_id)
        .order_by(RelatedEntity.status, RelatedEntity.relation_type, RelatedEntity.name)
    ).scalars()
    return [
        {
            "id": str(entity.id),
            "name": entity.name,
            "relation_type": entity.relation_type,
            "status": entity.status,
            "source": entity.source,
            "metadata": entity.metadata_payload or {},
            "confirmed_by": entity.confirmed_by,
            "confirmed_at": entity.confirmed_at,
        }
        for entity in entities
    ]


@router.patch("/companies/{company_id}/related-entities")
def update_related_entities(
    company_id: UUID,
    payload: RelatedEntitiesUpdate,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    actor = actor_from_request(request)
    for item in payload.entities:
        entity = db.get(RelatedEntity, item.id) if item.id else None
        if entity and entity.company_id != company_id:
            raise HTTPException(status_code=400, detail="关联主体不属于当前公司。")
        if not entity:
            entity = db.execute(
                select(RelatedEntity)
                .where(RelatedEntity.company_id == company_id)
                .where(RelatedEntity.name == item.name.strip())
                .where(RelatedEntity.relation_type == item.relation_type)
            ).scalar_one_or_none()
        before = (
            {"name": entity.name, "relation_type": entity.relation_type, "status": entity.status}
            if entity
            else {}
        )
        entity = entity or RelatedEntity(company_id=company_id)
        entity.name = item.name.strip()
        entity.relation_type = item.relation_type
        entity.status = item.status
        entity.metadata_payload = item.metadata
        if item.status == "confirmed":
            entity.confirmed_by = actor
            entity.confirmed_at = datetime.now(timezone.utc)
        db.add(entity)
        db.flush()
        add_audit_log(
            db,
            actor=actor,
            action="related_entity.update",
            entity_type="related_entity",
            entity_id=entity.id,
            before=before,
            after={"name": entity.name, "relation_type": entity.relation_type, "status": entity.status},
        )
    db.commit()
    return list_related_entities(company_id, db)


@router.post("/companies/{company_id}/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    company_id: UUID,
    background: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    analysis_run_id: Optional[UUID] = Form(default=None),
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    _require_demo_company(db, company_id)
    if analysis_run_id:
        run = db.get(AnalysisRun, analysis_run_id)
        if not run or run.company_id != company_id:
            raise HTTPException(status_code=400, detail="分析任务与公司不匹配。")
    content = await file.read(get_settings().max_upload_bytes + 1)
    try:
        path, digest, extension = store_document_bytes(
            company_id=company_id,
            filename=file.filename or "upload",
            content=content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    duplicate = db.execute(
        select(SourceDocument)
        .where(SourceDocument.company_id == company_id)
        .where(SourceDocument.sha256 == digest)
    ).scalar_one_or_none()
    if duplicate:
        remove_stored_document(path)
        return {"duplicate": True, "document": _document_payload(duplicate)}
    actor = actor_from_request(request)
    document = SourceDocument(
        company_id=company_id,
        analysis_run_id=analysis_run_id,
        original_filename=safe_filename(file.filename or "upload"),
        mime_type=mime_type_for(file.filename or "upload", file.content_type or ""),
        extension=extension,
        storage_path=path,
        sha256=digest,
        size_bytes=len(content),
        status="queued",
        uploaded_by=actor,
    )
    db.add(document)
    db.flush()
    job = enqueue_job(
        db,
        job_type="parse_document",
        analysis_run_id=analysis_run_id,
        payload={"document_id": str(document.id)},
    )
    add_audit_log(
        db,
        actor=actor,
        action="document.upload",
        entity_type="source_document",
        entity_id=document.id,
        after={"filename": document.original_filename, "sha256": digest, "size": len(content)},
    )
    db.commit()
    _schedule(background, job)
    return {"duplicate": False, "document": _document_payload(document), "job_id": str(job.id)}


@router.get("/companies/{company_id}/documents")
def list_documents(
    company_id: UUID, db: Session = Depends(db_session.get_db)
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    documents = db.execute(
        select(SourceDocument)
        .where(SourceDocument.company_id == company_id)
        .order_by(SourceDocument.created_at.desc())
    ).scalars()
    return [_document_payload(document) for document in documents]


@router.get("/documents/{document_id}/extracted-facts")
def document_facts(
    document_id: UUID, db: Session = Depends(db_session.get_db)
) -> list[dict[str, Any]]:
    document = db.get(SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="材料不存在。")
    _require_demo_company(db, document.company_id)
    facts = db.execute(
        select(ExtractedFact)
        .where(ExtractedFact.document_id == document_id)
        .order_by(ExtractedFact.metric_code, ExtractedFact.period)
    ).scalars()
    return [_fact_payload(fact) for fact in facts]


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: UUID, db: Session = Depends(db_session.get_db)
) -> FileResponse:
    document = db.get(SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="材料不存在。")
    _require_demo_company(db, document.company_id)
    stored = Path(document.storage_path)
    if not stored.is_file():
        raise HTTPException(status_code=404, detail="材料文件已丢失。")
    return FileResponse(
        path=stored,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename,
    )


@router.post("/documents/{document_id}/confirm-facts")
def confirm_document_facts(
    document_id: UUID,
    payload: FactConfirmationRequest,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> list[dict[str, Any]]:
    document = db.get(SourceDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="材料不存在。")
    _require_demo_company(db, document.company_id)
    actor = actor_from_request(request)
    for decision in payload.decisions:
        fact = db.get(ExtractedFact, decision.fact_id)
        if not fact or fact.document_id != document_id:
            raise HTTPException(status_code=400, detail="事实记录与材料不匹配。")
        before = _fact_payload(fact)
        fact.status = decision.status
        for field in (
            "metric_code",
            "label",
            "value_numeric",
            "value_text",
            "unit",
            "currency",
            "period",
        ):
            value = getattr(decision, field)
            if value is not None:
                setattr(fact, field, value)
        fact.confirmed_by = actor
        fact.confirmed_at = datetime.now(timezone.utc)
        db.add(fact)
        add_audit_log(
            db,
            actor=actor,
            action="extracted_fact.confirm",
            entity_type="extracted_fact",
            entity_id=fact.id,
            before=before,
            after=_fact_payload(fact),
        )
    db.commit()
    return document_facts(document_id, db)


@router.post("/companies/{company_id}/manual-facts", status_code=status.HTTP_201_CREATED)
def create_manual_fact(
    company_id: UUID,
    payload: ManualFactCreate,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    _require_demo_company(db, company_id)
    if payload.value_numeric is None and not payload.value_text.strip():
        raise HTTPException(status_code=400, detail="手工补录必须提供数值或文本。")
    digest = hashlib.sha256(f"manual-facts:{company_id}".encode("utf-8")).hexdigest()
    document = db.execute(
        select(SourceDocument)
        .where(SourceDocument.company_id == company_id)
        .where(SourceDocument.sha256 == digest)
    ).scalar_one_or_none()
    actor = actor_from_request(request)
    if not document:
        document = SourceDocument(
            company_id=company_id,
            original_filename="人工补录数据",
            mime_type="application/x-drisk-manual-facts",
            extension=".txt",
            storage_path="",
            sha256=digest,
            size_bytes=0,
            status="parsed",
            parse_metadata={"manual_entry": True},
            uploaded_by=actor,
        )
        db.add(document)
        db.flush()
    fact = ExtractedFact(
        document_id=document.id,
        company_id=company_id,
        metric_code=payload.metric_code,
        label=payload.label,
        value_numeric=payload.value_numeric,
        value_text=payload.value_text,
        unit=payload.unit,
        currency=payload.currency,
        period=payload.period,
        source_locator="manual-entry",
        confidence=1,
        status="confirmed",
        raw_payload={"entry_mode": "manual"},
        confirmed_by=actor,
        confirmed_at=datetime.now(timezone.utc),
    )
    db.add(fact)
    db.flush()
    add_audit_log(
        db,
        actor=actor,
        action="extracted_fact.manual_create",
        entity_type="extracted_fact",
        entity_id=fact.id,
        after=_fact_payload(fact),
    )
    db.commit()
    db.refresh(fact)
    return _fact_payload(fact)


@router.post("/monthly-reports", status_code=status.HTTP_202_ACCEPTED)
def create_monthly_report(
    payload: MonthlyReportCreate,
    background: BackgroundTasks,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    company = _require_demo_company(db, payload.company_id)
    run = db.get(AnalysisRun, payload.analysis_run_id) if payload.analysis_run_id else None
    if not run:
        run = db.execute(
            select(AnalysisRun)
            .where(AnalysisRun.company_id == company.id)
            .where(AnalysisRun.status == "completed")
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    if not run or run.company_id != company.id or run.status != "completed":
        raise HTTPException(status_code=400, detail="请先完成一次实时分析。")
    if run.requested_period and run.requested_period != payload.period:
        raise HTTPException(
            status_code=400,
            detail=f"所选分析任务属于{run.requested_period}，请为{payload.period}重新运行实时分析。",
        )
    version = (
        db.execute(
            select(func.max(MonthlyReport.version))
            .where(MonthlyReport.company_id == company.id)
            .where(MonthlyReport.period == payload.period)
        ).scalar_one()
        or 0
    ) + 1
    actor = actor_from_request(request)
    report = MonthlyReport(
        company_id=company.id,
        analysis_run_id=run.id,
        period=payload.period,
        version=version,
        status="generating",
        title=payload.title or f"{company.name}{payload.period}投后监测报告",
        rule_set_version=str((run.summary or {}).get("rule_set_version") or ""),
        created_by=actor,
    )
    db.add(report)
    db.flush()
    job = enqueue_job(
        db,
        job_type="generate_monthly_report",
        analysis_run_id=run.id,
        payload={"report_id": str(report.id)},
    )
    add_audit_log(
        db,
        actor=actor,
        action="monthly_report.create",
        entity_type="monthly_report",
        entity_id=report.id,
        after={"period": report.period, "version": report.version, "analysis_run_id": str(run.id)},
    )
    db.commit()
    _schedule(background, job)
    return {"report": _report_payload(report), "job_id": str(job.id)}


@router.get("/companies/{company_id}/monthly-reports")
def list_monthly_reports(
    company_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(db_session.get_db),
) -> list[dict[str, Any]]:
    _require_demo_company(db, company_id)
    reports = db.execute(
        select(MonthlyReport)
        .where(MonthlyReport.company_id == company_id)
        .order_by(MonthlyReport.period.desc(), MonthlyReport.version.desc())
        .limit(limit)
    ).scalars()
    return [_report_payload(report) for report in reports]


@router.get("/monthly-reports/{report_id}")
def get_monthly_report(
    report_id: UUID, db: Session = Depends(db_session.get_db)
) -> dict[str, Any]:
    report = _require_demo_report(db, report_id)
    return _report_payload(report)


@router.patch("/monthly-reports/{report_id}")
def update_monthly_report(
    report_id: UUID,
    payload: MonthlyReportUpdate,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    report = _require_demo_report(db, report_id)
    if report.status == "approved":
        raise HTTPException(status_code=409, detail="已审批报告不可直接修改，请重新生成新版本。")
    if report.status not in {"draft", "in_review"}:
        raise HTTPException(status_code=409, detail="报告尚未生成完成。")
    before = _report_payload(report)
    if payload.title is not None:
        report.title = payload.title
    if payload.summary is not None:
        report.summary = payload.summary
    if payload.sections is not None:
        unknown = set(payload.sections) - set(SECTION_ORDER)
        if unknown:
            raise HTTPException(status_code=400, detail=f"未知报告板块：{sorted(unknown)}")
        report.sections = {**(report.sections or {}), **payload.sections}
    if payload.status is not None:
        report.status = payload.status
    actor = actor_from_request(request)
    append_report_revision(db, report, actor)
    add_audit_log(
        db,
        actor=actor,
        action="monthly_report.update",
        entity_type="monthly_report",
        entity_id=report.id,
        before=before,
        after=_report_payload(report),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_payload(report)


@router.post("/monthly-reports/{report_id}/approve")
def approve_monthly_report(
    report_id: UUID,
    request: Request,
    db: Session = Depends(db_session.get_db),
) -> dict[str, Any]:
    report = _require_demo_report(db, report_id)
    if report.status not in {"draft", "in_review"}:
        raise HTTPException(status_code=409, detail="只有草稿或审阅中报告可以审批。")
    missing = [key for key in SECTION_ORDER if not str((report.sections or {}).get(key) or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"以下板块尚未填写：{missing}")
    actor = actor_from_request(request)
    before = _report_payload(report)
    report.status = "approved"
    report.approved_by = actor
    report.approved_at = datetime.now(timezone.utc)
    append_report_revision(db, report, actor)
    add_audit_log(
        db,
        actor=actor,
        action="monthly_report.approve",
        entity_type="monthly_report",
        entity_id=report.id,
        before=before,
        after=_report_payload(report),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return _report_payload(report)


@router.get("/monthly-reports/{report_id}/export.xlsx")
def export_monthly_report(
    report_id: UUID, db: Session = Depends(db_session.get_db)
) -> Response:
    report = _require_demo_report(db, report_id)
    content = export_monthly_report_xlsx(db, report)
    filename = safe_filename(f"{report.title}-v{report.version}.xlsx")
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=monthly-report-v{report.version}.xlsx; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )
