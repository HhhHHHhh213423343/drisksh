from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Company


QICHACHA_PROFILE_KEY = "qichacha_profile"
QICHACHA_DETAIL_ENDPOINT = "https://api.qichacha.com/ECIInfoVerify/GetInfo"


class QichachaProfileError(RuntimeError):
    pass


def build_qichacha_token(app_key: str, secret_key: str, timespan: str) -> str:
    raw = f"{app_key}{timespan}{secret_key}".encode("utf-8")
    return hashlib.md5(raw).hexdigest().upper()


def unavailable_qichacha_profile(reason: str, message: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "source": "qichacha_api",
        "reason": reason,
        "message": message,
        "updated_at": _utc_now(),
    }


def normalize_qichacha_profile(payload: dict[str, Any], search_key: str) -> dict[str, Any]:
    result = _extract_result(payload)
    if not result:
        raise QichachaProfileError("企查查返回结果为空。")

    industry = _pick(result, "Industry", "IndustryName", "IndustryCode")
    if isinstance(industry, dict):
        industry = " / ".join(
            str(value)
            for value in (
                industry.get("Industry"),
                industry.get("SubIndustry"),
            )
            if value not in (None, "")
        )

    return {
        "status": "available",
        "source": "qichacha_api",
        "search_key": search_key,
        "company_name": _pick(result, "Name", "CompanyName"),
        "legal_representative": _pick(result, "OperName", "LegalPerson", "LegalRepresentative"),
        "registered_capital": _pick(result, "RegistCapi", "RegisteredCapital", "RegCapital"),
        "start_date": _pick(result, "StartDate", "TermStart", "EstiblishTime"),
        "approved_date": _pick(result, "CheckDate", "ApprovedDate"),
        "register_status": _pick(result, "Status", "RegStatus"),
        "credit_code": _pick(result, "CreditCode", "No", "OrgNo"),
        "company_type": _pick(result, "EconKind", "CompanyType"),
        "registration_authority": _pick(result, "BelongOrg", "RegistAuthority"),
        "address": _pick(result, "Address", "AddressInfo"),
        "business_scope": _pick(result, "Scope", "BusinessScope"),
        "industry": industry or "",
        "key_no": _pick(result, "KeyNo"),
        "updated_at": _utc_now(),
        "major_personnel": _normalize_people(
            _pick(result, "Employees", "Staffs", "MainPersonnel", default=[])
        ),
        "shareholders": _normalize_shareholders(
            _pick(result, "Partners", "Shareholders", "Stockholders", default=[])
        ),
        "change_records": _normalize_change_records(
            _pick(result, "ChangeRecords", "ChangeRecord", "Changes", default=[])
        ),
    }


class QichachaProfileClient:
    def __init__(
        self,
        *,
        app_key: str | None = None,
        secret_key: str | None = None,
        endpoint: str = QICHACHA_DETAIL_ENDPOINT,
        timeout: float = 12,
    ) -> None:
        settings = get_settings()
        self.app_key = (app_key if app_key is not None else settings.qichacha_app_key).strip()
        self.secret_key = (
            secret_key if secret_key is not None else settings.qichacha_secret_key
        ).strip()
        self.endpoint = endpoint
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        settings = get_settings()
        return settings.qichacha_enabled and bool(self.app_key and self.secret_key)

    async def fetch_profile(self, search_key: str) -> dict[str, Any]:
        if not self.is_configured:
            return unavailable_qichacha_profile(
                "missing_credentials",
                "暂未配置企查查官方 API Key，已保留公司基础档案。",
            )

        timespan = str(int(time.time()))
        headers = {
            "Token": build_qichacha_token(self.app_key, self.secret_key, timespan),
            "Timespan": timespan,
        }
        params = {
            "key": self.app_key,
            "searchKey": search_key,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(self.endpoint, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return unavailable_qichacha_profile(
                "request_failed",
                f"企查查官方 API 请求失败：{exc}",
            )

        status = str(payload.get("Status") or payload.get("status") or "")
        if status and status not in {"200", "1"}:
            message = str(payload.get("Message") or payload.get("message") or "企查查接口未返回成功状态。")
            return unavailable_qichacha_profile("api_rejected", message)

        try:
            return normalize_qichacha_profile(payload, search_key)
        except QichachaProfileError as exc:
            return unavailable_qichacha_profile("empty_result", str(exc))


async def refresh_company_qichacha_profile(
    db: Session,
    company: Company,
    *,
    client: QichachaProfileClient | None = None,
    force: bool = False,
) -> Company:
    profile = dict(company.company_profile or {})
    current = profile.get(QICHACHA_PROFILE_KEY)
    if not force and isinstance(current, dict) and current.get("status") == "available":
        return company

    qichacha_client = client or QichachaProfileClient()
    qichacha_profile = await qichacha_client.fetch_profile(company.name)
    profile[QICHACHA_PROFILE_KEY] = qichacha_profile
    company.company_profile = profile

    if qichacha_profile.get("status") == "available":
        credit_code = _as_string(qichacha_profile.get("credit_code"))
        if credit_code and not company.credit_code:
            existing_credit_code = db.execute(
                select(Company).where(Company.credit_code == credit_code)
            ).scalar_one_or_none()
            if not existing_credit_code or existing_credit_code.id == company.id:
                company.credit_code = credit_code
        company.industry = company.industry or _as_string(qichacha_profile.get("industry"))[:128]
        company.region = company.region or _infer_region(_as_string(qichacha_profile.get("address")))
        company.description = company.description or _as_string(qichacha_profile.get("business_scope"))

    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _extract_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("Result") if isinstance(payload, dict) else None
    if isinstance(result, list):
        first = result[0] if result else {}
        return first if isinstance(first, dict) else {}
    if isinstance(result, dict):
        return result
    return payload if isinstance(payload, dict) else {}


def _pick(source: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_people(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        name = _as_string(_pick(item, "Name", "PersonName", "姓名"))
        position = _as_string(_pick(item, "Job", "Position", "Title", "职务"))
        if name or position:
            normalized.append({"name": name, "position": position})
    return normalized


def _normalize_shareholders(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        name = _as_string(_pick(item, "StockName", "Name", "ShareholderName", "股东"))
        ratio = _as_string(_pick(item, "StockPercent", "Percent", "Rate", "持股比例"))
        capital = _as_string(_pick(item, "ShouldCapi", "SubConAm", "SubscribedCapital", "认缴出资额"))
        if name:
            normalized.append({"name": name, "ratio": ratio, "capital": capital})
    return normalized


def _normalize_change_records(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        project = _as_string(_pick(item, "ProjectName", "ChangeItem", "ItemName", "变更事项"))
        before = _as_string(_pick(item, "BeforeContent", "Before", "变更前"))
        after = _as_string(_pick(item, "AfterContent", "After", "变更后"))
        changed_at = _as_string(_pick(item, "ChangeDate", "Date", "变更日期"))
        if project or before or after:
            normalized.append(
                {
                    "project": project,
                    "before": before,
                    "after": after,
                    "changed_at": changed_at,
                }
            )
    return normalized


def _as_string(value: Any) -> str:
    return "" if value in (None, "") else str(value).strip()


def _infer_region(address: str) -> str:
    if not address:
        return ""
    for suffix in ("省", "市", "自治区"):
        index = address.find(suffix)
        if index > 0:
            return address[: index + len(suffix)]
    return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
