from __future__ import annotations

from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AnalysisRunCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=255)
    stock_code: str = Field(default="", max_length=32)
    market: str = Field(default="", max_length=32)
    requested_period: str = Field(default="", pattern=r"^$|^20\d{2}-(0[1-9]|1[0-2])$")
    max_results_per_source: int = Field(default=8, ge=1, le=20)
    max_documents: int = Field(default=60, ge=5, le=200)
    lookback_days: int = Field(default=30, ge=1, le=365)


class RelatedEntityInput(BaseModel):
    id: Optional[UUID] = None
    name: str = Field(..., min_length=1, max_length=255)
    relation_type: str = Field(default="other", max_length=80)
    status: Literal["candidate", "confirmed", "rejected"] = "confirmed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelatedEntitiesUpdate(BaseModel):
    entities: list[RelatedEntityInput] = Field(default_factory=list)


class FactDecision(BaseModel):
    fact_id: UUID
    status: Literal["confirmed", "rejected"]
    metric_code: Optional[str] = Field(default=None, max_length=128)
    label: Optional[str] = Field(default=None, max_length=255)
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = Field(default=None, max_length=32)
    currency: Optional[str] = Field(default=None, max_length=16)
    period: Optional[str] = Field(default=None, max_length=32)


class FactConfirmationRequest(BaseModel):
    decisions: list[FactDecision] = Field(default_factory=list)


class ManualFactCreate(BaseModel):
    metric_code: str = Field(..., min_length=1, max_length=128)
    label: str = Field(..., min_length=1, max_length=255)
    value_numeric: Optional[float] = None
    value_text: str = ""
    unit: str = Field(default="", max_length=32)
    currency: str = Field(default="", max_length=16)
    period: str = Field(default="", max_length=32)


class CompanyRuleUpdate(BaseModel):
    rule_code: int = Field(..., ge=1, le=999)
    enabled: bool
    override: dict[str, Any] = Field(default_factory=dict)


class CompanyRulesUpdate(BaseModel):
    rules: list[CompanyRuleUpdate]


class RuleEvaluationReview(BaseModel):
    status: Literal["hit", "not_hit", "insufficient_data", "not_applicable"]
    severity: Literal["general", "important", "high", "critical"] = "general"
    rationale: str = Field(..., min_length=1, max_length=4000)


class MonthlyReportCreate(BaseModel):
    company_id: UUID
    period: str = Field(..., pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    analysis_run_id: Optional[UUID] = None
    title: str = Field(default="", max_length=255)


class MonthlyReportUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)
    summary: Optional[str] = None
    sections: Optional[dict[str, str]] = None
    status: Optional[Literal["draft", "in_review"]] = None
