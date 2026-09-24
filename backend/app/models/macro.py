from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
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


class MacroIndicatorPoint(Base):
    __tablename__ = "macro_indicator_points"
    __table_args__ = (
        UniqueConstraint(
            "indicator_code", "period", "region_scope", name="uq_macro_indicator_period"
        ),
        Index("ix_macro_indicator_period", "indicator_code", "period"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    indicator_code = Column(String(80), nullable=False)
    indicator_name = Column(String(128), nullable=False)
    period = Column(String(40), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(24), nullable=False)
    frequency = Column(String(24), nullable=False)
    region_scope = Column(String(40), nullable=False, server_default="全国")
    source_name = Column(String(128), nullable=False)
    source_url = Column(String(512), nullable=False)
    raw_payload = Column(JSON, nullable=False, default=dict)
    collected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MacroIndustryEvent(TimestampMixin, Base):
    __tablename__ = "macro_industry_events"
    __table_args__ = (
        UniqueConstraint("dedupe_hash", name="uq_macro_event_dedupe"),
        Index("ix_macro_event_scope", "scope_type", "scope_key", "published_at"),
        Index("ix_macro_event_dimension", "dimension", "published_at"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    scope_type = Column(String(24), nullable=False)
    scope_key = Column(String(128), nullable=False, server_default="")
    dimension = Column(String(48), nullable=False)
    event_type = Column(String(80), nullable=False)
    indicator_code = Column(String(80), nullable=False, server_default="")
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    source_name = Column(String(128), nullable=False)
    source_url = Column(String(512), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False)
    impact_direction = Column(String(16), nullable=False, server_default="neutral")
    severity = Column(String(16), nullable=False, server_default="normal")
    relevance_score = Column(Float, nullable=False, server_default="1")
    dedupe_hash = Column(String(64), nullable=False)
    raw_payload = Column(JSON, nullable=False, default=dict)


class MacroRefreshRun(TimestampMixin, Base):
    __tablename__ = "macro_refresh_runs"
    __table_args__ = (
        Index("ix_macro_refresh_company_created", "company_id", "created_at"),
        Index("ix_macro_refresh_status", "status"),
    )

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id = Column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(20), nullable=False, server_default="queued")
    progress_current = Column(Integer, nullable=False, server_default="0")
    progress_total = Column(Integer, nullable=False, server_default="10")
    inserted_count = Column(Integer, nullable=False, server_default="0")
    updated_count = Column(Integer, nullable=False, server_default="0")
    failed_sources = Column(JSON, nullable=False, default=list)
    source_results = Column(JSON, nullable=False, default=list)
    message = Column(Text, nullable=False, server_default="等待更新")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
