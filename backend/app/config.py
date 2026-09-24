from __future__ import annotations

import os
from functools import lru_cache


def _normalize_http_path(value: str, default: str = "") -> str:
    normalized = value.strip()
    if not normalized:
        return default
    return normalized if normalized.startswith("/") else f"/{normalized}"


class Settings:
    app_name = "Business Analysis API"
    api_v1_prefix = "/api/v1"
    database_url = os.getenv(
        "DATABASE_URL",
        "sqlite:///./demo.db",
    )
    fastgpt_base_url = os.getenv("FASTGPT_BASE_URL", "").rstrip("/")
    fastgpt_api_key = os.getenv("FASTGPT_API_KEY", "")
    fastgpt_dataset_id = os.getenv("FASTGPT_DATASET_ID", "")
    fastgpt_dataset_upsert_path = _normalize_http_path(
        os.getenv("FASTGPT_DATASET_UPSERT_PATH", "")
    )
    fastgpt_chat_path = _normalize_http_path(
        os.getenv("FASTGPT_CHAT_PATH", "/chat/completions"),
        default="/chat/completions",
    )
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
    qichacha_app_key = os.getenv("QICHACHA_APP_KEY", "")
    qichacha_secret_key = os.getenv("QICHACHA_SECRET_KEY", "")
    qichacha_enabled = os.getenv("QICHACHA_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    default_context_limit = int(os.getenv("RISK_CONTEXT_LIMIT", "12"))
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    collection_api_key = os.getenv("COLLECTION_API_KEY", "")
    company_profile_worker_enabled = os.getenv(
        "COMPANY_PROFILE_WORKER_ENABLED", "false"
    ).lower() in {"1", "true", "yes"}
    demo_company_name = os.getenv("DEMO_COMPANY_NAME", "").strip()
    demo_report_as_of_date = os.getenv("DEMO_REPORT_AS_OF_DATE", "").strip()
    auth_required = os.getenv("AUTH_REQUIRED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    app_username = os.getenv("APP_USERNAME", "admin")
    app_password = os.getenv("APP_PASSWORD", "")
    app_password_hash = os.getenv("APP_PASSWORD_HASH", "")
    session_secret = os.getenv("SESSION_SECRET", "development-only-change-me")
    session_ttl_hours = int(os.getenv("SESSION_TTL_HOURS", "12"))
    upload_root = os.getenv("UPLOAD_ROOT", "./data/uploads")
    max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    max_document_pages = int(os.getenv("MAX_DOCUMENT_PAGES", "200"))
    inline_job_execution = os.getenv("INLINE_JOB_EXECUTION", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    llm_base_url = (
        os.getenv("LLM_BASE_URL", "")
        or ("https://api.deepseek.com" if deepseek_api_key else "")
        or fastgpt_base_url
    ).rstrip("/")
    llm_api_key = os.getenv("LLM_API_KEY", "") or deepseek_api_key or fastgpt_api_key
    llm_model = os.getenv("LLM_MODEL", "") or (
        "deepseek-flash" if deepseek_api_key else ""
    )
    llm_chat_path = _normalize_http_path(
        os.getenv("LLM_CHAT_PATH", fastgpt_chat_path),
        default="/chat/completions",
    )
    llm_max_evidence_items = int(os.getenv("LLM_MAX_EVIDENCE_ITEMS", "12"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
