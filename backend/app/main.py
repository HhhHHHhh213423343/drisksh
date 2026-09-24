from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.router import api_router
from app.api.routes.authoritative_ingestion import router as authoritative_ingestion_router
from app.config import get_settings
from app.db.base import Base
from app.db.session import engine
from app.models import (  # noqa: F401
    AnalysisReport,
    Company,
    CompanyProfileRun,
    CompanyProfileSnapshot,
    IngestionRun,
    RiskEvent,
)
from app.services.auth import SESSION_COOKIE, read_session
from app.services.rule_seed import ensure_default_rule_set
from app.services.company_profile import enable_supported_company


settings = get_settings()
allow_origins = (
    ["*"]
    if settings.cors_origins == "*"
    else [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=settings.cors_origins != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def create_tables() -> None:
    if settings.auth_required:
        if not (settings.app_password or settings.app_password_hash):
            raise RuntimeError("AUTH_REQUIRED=true 时必须配置 APP_PASSWORD 或 APP_PASSWORD_HASH。")
        if settings.session_secret == "development-only-change-me" or len(settings.session_secret) < 32:
            raise RuntimeError("生产环境 SESSION_SECRET 必须是至少32位的随机值。")
    Base.metadata.create_all(bind=engine)
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        ensure_default_rule_set(db)
        if settings.demo_company_name:
            company = db.execute(
                select(Company).where(Company.name == settings.demo_company_name)
            ).scalar_one_or_none()
            if not company:
                company = Company(name=settings.demo_company_name, company_profile={})
                db.add(company)
                db.commit()
                db.refresh(company)
            enable_supported_company(db, company)


@app.middleware("http")
async def require_internal_session(request: Request, call_next):
    settings = get_settings()
    path = request.url.path
    exempt = (
        path == "/health"
        or path == f"{settings.api_v1_prefix}/auth/login"
        or path == f"{settings.api_v1_prefix}/auth/logout"
        or path.startswith(f"{settings.api_v1_prefix}/company-profile/worker/")
        or path.startswith(f"{settings.api_v1_prefix}/authoritative-ingestion/daily/run")
    )
    if not settings.auth_required:
        request.state.user = "local-dev"
        return await call_next(request)
    if exempt:
        return await call_next(request)
    session = read_session(request.cookies.get(SESSION_COOKIE, ""))
    if not session:
        return JSONResponse(
            status_code=401,
            content={"detail": "请先登录 D.Risk。"},
        )
    request.state.user = session["sub"]
    return await call_next(request)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(authoritative_ingestion_router, prefix=settings.api_v1_prefix)
