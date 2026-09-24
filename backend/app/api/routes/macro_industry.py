from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.models import Company, MacroRefreshRun
from app.services.macro_ingestion import (
    MACRO_INDICATORS,
    refresh_macro_data_in_new_session,
    serialize_macro_run,
)


router = APIRouter(tags=["macro-industry"])


@router.post(
    "/companies/{company_id}/macro-industry/refresh",
    status_code=status.HTTP_202_ACCEPTED,
)
def refresh_macro_industry(
    company_id: UUID,
    background: BackgroundTasks,
    db: Session = Depends(db_session.get_db),
) -> dict:
    if not db.get(Company, company_id):
        raise HTTPException(status_code=404, detail="公司不存在。")
    active = db.execute(
        select(MacroRefreshRun)
        .where(MacroRefreshRun.company_id == company_id)
        .where(MacroRefreshRun.status.in_(["queued", "running"]))
        .order_by(MacroRefreshRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if active:
        return serialize_macro_run(active)
    run = MacroRefreshRun(
        company_id=company_id,
        status="queued",
        progress_total=len(MACRO_INDICATORS) + 3,
        message="等待更新",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(refresh_macro_data_in_new_session, run.id)
    return serialize_macro_run(run)


@router.get("/ingestion-runs/{run_id}")
def get_macro_refresh_run(
    run_id: UUID, db: Session = Depends(db_session.get_db)
) -> dict:
    run = db.get(MacroRefreshRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="未找到更新任务。")
    return serialize_macro_run(run)
