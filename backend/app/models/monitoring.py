from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)

from app.db.base import Base, TimestampMixin


class RuleSet(TimestampMixin, Base):
    __tablename__ = "rule_sets"
    __table_args__ = (UniqueConstraint("version", name="uq_rule_sets_version"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    version = Column(String(64), nullable=False)
    source_hash = Column(String(64), nullable=False, server_default="")
    status = Column(String(24), nullable=False, server_default="active", index=True)
    source_name = Column(String(255), nullable=False, server_default="")


class MonitoringRule(TimestampMixin, Base):
    __tablename__ = "monitoring_rules"
    __table_args__ = (
        UniqueConstraint("rule_set_id", "code", name="uq_monitoring_rules_set_code"),
        Index("ix_monitoring_rules_category_code", "category", "code"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    rule_set_id = Column(
        Uuid,
        ForeignKey("rule_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(Integer, nullable=False)
    category = Column(String(80), nullable=False, index=True)
    monitoring_point = Column(Text, nullable=False)
    assessment_standard = Column(Text, nullable=False)
    requirement_type = Column(String(24), nullable=False, server_default="自选")
    evaluation_type = Column(String(24), nullable=False, server_default="qualitative")
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    condition = Column(JSON, nullable=False, default=dict)
    data_requirements = Column(JSON, nullable=False, default=dict)
    applicability = Column(JSON, nullable=False, default=dict)


class CompanyRuleAssignment(TimestampMixin, Base):
    __tablename__ = "company_rule_assignments"
    __table_args__ = (
        UniqueConstraint("company_id", "rule_id", name="uq_company_rule_assignment"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id = Column(
        Uuid,
        ForeignKey("monitoring_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    override = Column(JSON, nullable=False, default=dict)


class RelatedEntity(TimestampMixin, Base):
    __tablename__ = "related_entities"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "name", "relation_type", name="uq_related_entity_scope"
        ),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    relation_type = Column(String(80), nullable=False, server_default="other")
    status = Column(String(24), nullable=False, server_default="candidate", index=True)
    source = Column(String(128), nullable=False, server_default="system")
    external_id = Column(String(128), nullable=False, server_default="")
    metadata_payload = Column(JSON, nullable=False, default=dict)
    confirmed_by = Column(String(128), nullable=False, server_default="")
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


class AnalysisRun(TimestampMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_company_created", "company_id", "created_at"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(24), nullable=False, server_default="queued", index=True)
    requested_period = Column(String(7), nullable=False, server_default="")
    current_stage = Column(String(80), nullable=False, server_default="queued")
    progress_current = Column(Integer, nullable=False, default=0, server_default="0")
    progress_total = Column(Integer, nullable=False, default=5, server_default="5")
    source_status = Column(JSON, nullable=False, default=dict)
    summary = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=False, server_default="")
    requested_by = Column(String(128), nullable=False, server_default="")
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (
        Index("ix_analysis_jobs_status_created", "status", "created_at"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id = Column(
        Uuid,
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    job_type = Column(String(40), nullable=False, index=True)
    status = Column(String(24), nullable=False, server_default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=False, server_default="")
    locked_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "sha256", name="uq_source_document_hash"),
        Index("ix_source_documents_company_status", "company_id", "status"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_run_id = Column(
        Uuid,
        ForeignKey("analysis_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(128), nullable=False, server_default="")
    extension = Column(String(16), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    page_count = Column(Integer, nullable=False, default=0, server_default="0")
    status = Column(String(24), nullable=False, server_default="queued", index=True)
    parse_error = Column(Text, nullable=False, server_default="")
    extracted_text = Column(Text, nullable=False, server_default="")
    parse_metadata = Column(JSON, nullable=False, default=dict)
    uploaded_by = Column(String(128), nullable=False, server_default="")


class ExtractedFact(TimestampMixin, Base):
    __tablename__ = "extracted_facts"
    __table_args__ = (
        Index("ix_extracted_facts_company_metric", "company_id", "metric_code"),
        Index("ix_extracted_facts_document_status", "document_id", "status"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id = Column(
        Uuid,
        ForeignKey("source_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_code = Column(String(128), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    value_numeric = Column(Float, nullable=True)
    value_text = Column(Text, nullable=False, server_default="")
    unit = Column(String(32), nullable=False, server_default="")
    currency = Column(String(16), nullable=False, server_default="")
    period = Column(String(32), nullable=False, server_default="")
    source_locator = Column(String(255), nullable=False, server_default="")
    confidence = Column(Float, nullable=False, default=0.0, server_default="0")
    status = Column(String(24), nullable=False, server_default="pending", index=True)
    raw_payload = Column(JSON, nullable=False, default=dict)
    confirmed_by = Column(String(128), nullable=False, server_default="")
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


class RuleEvaluation(TimestampMixin, Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "rule_id", name="uq_rule_evaluation_run_rule"),
        Index("ix_rule_evaluations_run_status", "analysis_run_id", "status"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    analysis_run_id = Column(
        Uuid,
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_id = Column(
        Uuid,
        ForeignKey("monitoring_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(24), nullable=False, index=True)
    severity = Column(String(16), nullable=False, server_default="normal")
    rationale = Column(Text, nullable=False, server_default="")
    evidence = Column(JSON, nullable=False, default=list)
    facts = Column(JSON, nullable=False, default=list)
    llm_output = Column(JSON, nullable=False, default=dict)
    reviewer_status = Column(String(24), nullable=False, server_default="pending")
    reviewed_by = Column(String(128), nullable=False, server_default="")
    reviewed_at = Column(DateTime(timezone=True), nullable=True)


class MonthlyReport(TimestampMixin, Base):
    __tablename__ = "monthly_reports"
    __table_args__ = (
        UniqueConstraint("company_id", "period", "version", name="uq_monthly_report_version"),
        Index("ix_monthly_reports_company_period", "company_id", "period"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_run_id = Column(
        Uuid,
        ForeignKey("analysis_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    period = Column(String(7), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, server_default="generating", index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False, server_default="")
    sections = Column(JSON, nullable=False, default=dict)
    evidence_snapshot = Column(JSON, nullable=False, default=dict)
    rule_set_version = Column(String(64), nullable=False, server_default="")
    model_name = Column(String(120), nullable=False, server_default="deterministic")
    prompt_version = Column(String(64), nullable=False, server_default="monthly-v1")
    token_usage = Column(JSON, nullable=False, default=dict)
    generated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(128), nullable=False, server_default="")
    approved_by = Column(String(128), nullable=False, server_default="")
    approved_at = Column(DateTime(timezone=True), nullable=True)


class ReportVersion(TimestampMixin, Base):
    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "revision", name="uq_report_revision"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id = Column(
        Uuid,
        ForeignKey("monthly_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    edited_by = Column(String(128), nullable=False, server_default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity_created", "entity_type", "entity_id", "created_at"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    actor = Column(String(128), nullable=False, server_default="")
    action = Column(String(80), nullable=False)
    entity_type = Column(String(80), nullable=False)
    entity_id = Column(String(64), nullable=False)
    before_payload = Column(JSON, nullable=False, default=dict)
    after_payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LLMCache(TimestampMixin, Base):
    __tablename__ = "llm_cache"
    __table_args__ = (UniqueConstraint("input_hash", name="uq_llm_cache_input_hash"),)

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    input_hash = Column(String(64), nullable=False)
    purpose = Column(String(64), nullable=False)
    provider = Column(String(80), nullable=False, server_default="")
    model_name = Column(String(120), nullable=False, server_default="")
    response_payload = Column(JSON, nullable=False, default=dict)
    token_usage = Column(JSON, nullable=False, default=dict)
