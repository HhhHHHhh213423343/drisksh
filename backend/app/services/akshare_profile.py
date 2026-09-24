from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import math
from typing import Any

from sqlalchemy.orm import Session

from app.models import Company

try:
    import akshare as ak
except ImportError:  # pragma: no cover - optional runtime dependency
    ak = None


AKSHARE_PROFILE_KEY = "akshare_profile"

# Keep a tiny bootstrap directory for companies that are part of the shipped demo.
# Successful live resolutions are persisted in company_profile, so this is not
# intended to replace a full security master.
LOCAL_A_SHARE_SECURITIES = (
    {
        "stock_code": "002594",
        "stock_name": "比亚迪",
        "aliases": ("比亚迪", "比亚迪股份", "比亚迪股份有限公司"),
    },
)

STOCK_DIRECTORY_SOURCES = (
    ("深交所", "stock_info_sz_name_code", {"symbol": "A股列表"}),
    ("上交所主板", "stock_info_sh_name_code", {"symbol": "主板A股"}),
    ("上交所科创板", "stock_info_sh_name_code", {"symbol": "科创板"}),
    ("北交所", "stock_info_bj_name_code", {}),
)


async def refresh_company_akshare_profile(
    db: Session,
    company: Company,
    *,
    force: bool = False,
) -> Company:
    profile = dict(company.company_profile or {})
    current = profile.get(AKSHARE_PROFILE_KEY)
    if (
        not force
        and isinstance(current, dict)
        and current.get("status") == "available"
    ):
        return company

    stock_code = str(profile.get("stock_code") or profile.get("stockCode") or "").strip()
    akshare_profile = await asyncio.to_thread(
        build_akshare_profile,
        company.name,
        stock_code=stock_code,
    )
    profile[AKSHARE_PROFILE_KEY] = akshare_profile

    if akshare_profile.get("status") == "available":
        resolved_code = _as_string(akshare_profile.get("stock_code"))
        if resolved_code:
            profile["stock_code"] = resolved_code
        industry = _as_string(
            (akshare_profile.get("individual_info") or {}).get("行业")
        )
        company.industry = company.industry or industry[:128]
        company.description = company.description or _build_description(akshare_profile)

    company.company_profile = profile
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def build_akshare_profile(company_name: str, *, stock_code: str = "") -> dict[str, Any]:
    if ak is None:
        return _unavailable("missing_dependency", "当前运行环境未安装 akshare。")

    match = _resolve_stock(company_name, stock_code=stock_code)
    if not match.get("stock_code"):
        resolution_errors = match.get("resolution_errors") or []
        resolution_sources = match.get("resolution_sources") or []
        if resolution_errors and not resolution_sources:
            return {
                "status": "unavailable",
                "source": "akshare",
                "reason": "source_error",
                "search_key": company_name,
                "message": "AkShare 股票名单数据源暂时不可用，请稍后重试。",
                "resolution_errors": resolution_errors,
                "updated_at": _utc_now(),
            }
        return {
            "status": "unmatched",
            "source": "akshare",
            "reason": "not_found",
            "search_key": company_name,
            "message": "AkShare 未匹配到 A 股上市公司代码，已保留自由搜索公司名称。",
            "resolution_sources": resolution_sources,
            "resolution_errors": resolution_errors,
            "updated_at": _utc_now(),
        }

    code = match["stock_code"]
    individual_info = _fetch_individual_info(code)
    financial_rows = _fetch_financial_abstract(code)
    financial_indicators = _fetch_financial_indicators(code)

    return {
        "status": "available",
        "source": "akshare",
        "search_key": company_name,
        "stock_code": code,
        "stock_name": match.get("stock_name", ""),
        "matched_name": match.get("matched_name", ""),
        "resolution_source": match.get("resolution_source", ""),
        "resolution_errors": match.get("resolution_errors", []),
        "updated_at": _utc_now(),
        "individual_info": individual_info,
        "financial_abstract": financial_rows[:6],
        "financial_indicators": financial_indicators[:6],
        "financial_highlights": _build_financial_highlights(
            individual_info,
            financial_rows,
            financial_indicators,
        ),
        # Macro data belongs to the dedicated macro-analysis pipeline. Keeping
        # it out of an interactive company lookup avoids several unrelated,
        # slow upstream requests.
        "macro_trends": [],
    }


def _resolve_stock(company_name: str, *, stock_code: str = "") -> dict[str, Any]:
    if stock_code:
        local_security = next(
            (
                security
                for security in LOCAL_A_SHARE_SECURITIES
                if security["stock_code"] == stock_code
            ),
            None,
        )
        return {
            "stock_code": stock_code,
            "stock_name": local_security["stock_name"] if local_security else "",
            "matched_name": company_name,
            "resolution_source": "provided_stock_code",
            "resolution_errors": [],
        }

    local_match = _resolve_local_stock(company_name)
    if local_match and _plausible_match(company_name, local_match.get("matched_name", "")):
        return local_match

    resolution_errors: list[dict[str, str]] = []
    resolution_sources: list[str] = []
    supported_source_count = 0

    for source_name, function_name, kwargs in STOCK_DIRECTORY_SOURCES:
        function = getattr(ak, function_name, None)
        if not callable(function):
            continue
        supported_source_count += 1
        try:
            rows = _records_from_dataframe(function(**kwargs))
        except Exception as exc:
            resolution_errors.append(
                {
                    "source": source_name,
                    "error": _exception_summary(exc),
                }
            )
            continue

        resolution_sources.append(source_name)
        match = _match_stock_rows(company_name, rows)
        if match and _plausible_match(company_name, match.get("matched_name", "")):
            match["resolution_source"] = source_name
            match["resolution_sources"] = resolution_sources
            match["resolution_errors"] = resolution_errors
            return match

    # Compatibility fallback for older AkShare versions without the individual
    # exchange directory functions.
    aggregate_function = getattr(ak, "stock_info_a_code_name", None)
    if supported_source_count == 0 and callable(aggregate_function):
        try:
            rows = _records_from_dataframe(aggregate_function())
            resolution_sources.append("沪深京汇总")
            match = _match_stock_rows(company_name, rows)
            if match and _plausible_match(company_name, match.get("matched_name", "")):
                match["resolution_source"] = "沪深京汇总"
                match["resolution_sources"] = resolution_sources
                match["resolution_errors"] = resolution_errors
                return match
        except Exception as exc:
            resolution_errors.append(
                {
                    "source": "沪深京汇总",
                    "error": _exception_summary(exc),
                }
            )

    return {
        "resolution_sources": resolution_sources,
        "resolution_errors": resolution_errors,
    }


def _resolve_local_stock(company_name: str) -> dict[str, Any]:
    normalized_query = _normalize_name(company_name)
    for security in LOCAL_A_SHARE_SECURITIES:
        for alias in security["aliases"]:
            normalized_alias = _normalize_name(alias)
            if normalized_alias and (
                normalized_alias == normalized_query
                or normalized_alias in normalized_query
                or normalized_query in normalized_alias
            ):
                return {
                    "stock_code": security["stock_code"],
                    "stock_name": security["stock_name"],
                    "matched_name": company_name,
                    "resolution_source": "local_alias",
                    "resolution_errors": [],
                }
    return {}


def _match_stock_rows(company_name: str, rows: list[dict[str, Any]]) -> dict[str, str]:
    normalized_query = _normalize_name(company_name)
    best: dict[str, str] = {}

    for row in rows:
        code = _first_text(row, ("code", "代码", "A股代码", "证券代码", "股票代码"))
        name = _first_text(row, ("name", "名称", "A股简称", "证券简称", "股票简称"))
        if not code or not name:
            continue

        candidate_names = [
            name,
            _first_text(row, ("公司全称", "公司名称", "全称")),
        ]
        for candidate_name in candidate_names:
            normalized_name = _normalize_name(candidate_name)
            if normalized_name and (
                normalized_name in normalized_query or normalized_query in normalized_name
            ):
                return {
                    "stock_code": code,
                    "stock_name": name,
                    "matched_name": candidate_name,
                }
            if (
                not best
                and len(normalized_name) >= 4
                and normalized_name[:4] in normalized_query
            ):
                best = {
                    "stock_code": code,
                    "stock_name": name,
                    "matched_name": candidate_name,
                }
    return best


def _plausible_match(company_name: str, matched_name: str) -> bool:
    """判断上市公司解析结果是否可以采信。

    A 股简称里“金融”“科技”“控股”等通用词非常常见，仅凭两三个字的重合就把一家
    非上市主体匹配到上市公司，会把错误主体的财报带进风险评估。这里要求候选名称
    与查询名称存在足够强的字面重合，否则视为不匹配。
    """

    query = _normalize_name(company_name)
    candidate = _normalize_name(matched_name)
    if not query or not candidate:
        return False
    if candidate in query or query in candidate:
        return True
    overlap = len(set(candidate) & set(query))
    return overlap >= max(3, len(candidate) - 1)


def _fetch_individual_info(stock_code: str) -> dict[str, Any]:
    if not hasattr(ak, "stock_individual_info_em"):
        return {}
    try:
        rows = _records_from_dataframe(ak.stock_individual_info_em(symbol=stock_code))
    except Exception:
        return {}
    info: dict[str, Any] = {}
    for row in rows:
        key = _as_string(row.get("item") or row.get("指标"))
        if key:
            info[key] = row.get("value") if "value" in row else row.get("值")
    return info


def _fetch_financial_abstract(stock_code: str) -> list[dict[str, Any]]:
    if hasattr(ak, "stock_financial_abstract"):
        try:
            rows = _records_from_dataframe(
                ak.stock_financial_abstract(symbol=stock_code)
            )
            if rows:
                return rows
        except Exception:
            pass

    if hasattr(ak, "stock_financial_abstract_new_ths"):
        try:
            return _records_from_dataframe(
                ak.stock_financial_abstract_new_ths(
                    symbol=stock_code,
                    indicator="按报告期",
                )
            )
        except Exception:
            pass
    return []


def _fetch_financial_indicators(stock_code: str) -> list[dict[str, Any]]:
    if hasattr(ak, "stock_financial_analysis_indicator"):
        try:
            return _records_from_dataframe(
                ak.stock_financial_analysis_indicator(
                    symbol=stock_code,
                    start_year=str(datetime.now(timezone.utc).year - 3),
                )
            )
        except Exception:
            return []
    return []


def _build_financial_highlights(
    individual_info: dict[str, Any],
    financial_rows: list[dict[str, Any]],
    financial_indicators: list[dict[str, Any]],
) -> dict[str, str]:
    return {
        "revenue": _pick_financial_metric(
            financial_rows,
            ["营业总收入", "营业收入", "总营收", "营收"],
        ),
        "net_profit": _pick_financial_metric(
            financial_rows,
            ["归母净利润", "归属于母公司所有者的净利润", "净利润"],
        ),
        "gross_margin": _pick_latest_indicator(
            financial_indicators,
            ["销售毛利率", "毛利率"],
        ),
        "market_value": _as_string(individual_info.get("总市值")),
        "industry": _as_string(individual_info.get("行业")),
    }


def _build_description(akshare_profile: dict[str, Any]) -> str:
    stock_name = _as_string(akshare_profile.get("stock_name"))
    stock_code = _as_string(akshare_profile.get("stock_code"))
    industry = _as_string(
        (akshare_profile.get("individual_info") or {}).get("行业")
    )
    parts = [part for part in [stock_name, stock_code, industry] if part]
    return "AkShare 已匹配上市公司：" + " / ".join(parts) if parts else ""


def _pick_by_keywords(row: dict[str, Any], keywords: list[str]) -> str:
    for key, value in row.items():
        key_text = _as_string(key)
        if any(keyword in key_text for keyword in keywords):
            return _as_string(value)
    return ""


def _pick_financial_metric(rows: list[dict[str, Any]], keywords: list[str]) -> str:
    matching_rows = []
    for row in rows:
        metric_name = _first_text(row, ("指标", "metric_name", "项目", "item"))
        if metric_name and any(keyword in metric_name for keyword in keywords):
            matching_rows.append(row)

    if not matching_rows:
        return ""

    # Long-form data returned by the current Tonghuashun interface.
    latest_long_row = _latest_row(
        matching_rows,
        date_keys=("report_date", "日期", "报告期", "报告日期"),
    )
    if "value" in latest_long_row:
        value = _as_string(latest_long_row.get("value"))
        if value:
            return value

    # Wide-form data returned by stock_financial_abstract: metric rows and
    # reporting periods as columns.
    period_values: list[tuple[str, Any]] = []
    for key, value in matching_rows[0].items():
        period = _period_key(key)
        if period and value not in (None, ""):
            period_values.append((period, value))
    if not period_values:
        return ""
    _, latest_value = max(period_values, key=lambda item: item[0])
    return _as_string(latest_value)


def _pick_latest_indicator(rows: list[dict[str, Any]], keywords: list[str]) -> str:
    date_keys = ("日期", "report_date", "报告期", "报告日期")
    sorted_rows = sorted(
        rows,
        key=lambda row: _period_key(_first_text(row, date_keys)),
        reverse=True,
    )
    for row in sorted_rows:
        value = _pick_by_keywords(row, keywords)
        if value and value.lower() != "nan":
            return value
    return ""


def _latest_row(
    rows: list[dict[str, Any]],
    *,
    date_keys: tuple[str, ...],
) -> dict[str, Any]:
    if not rows:
        return {}

    def row_key(row: dict[str, Any]) -> str:
        return _period_key(_first_text(row, date_keys))

    return max(rows, key=row_key)


def _period_key(value: Any) -> str:
    digits = "".join(character for character in _as_string(value) if character.isdigit())
    return digits if len(digits) >= 6 else ""


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _as_string(row.get(key))
        if value:
            return value
    return ""


def _exception_summary(exc: Exception) -> str:
    detail = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {detail}"[:500]


def _records_from_dataframe(dataframe: Any) -> list[dict[str, Any]]:
    if dataframe is None or getattr(dataframe, "empty", True):
        return []
    cleaned = dataframe.where(dataframe.notna(), None)
    records = cleaned.to_dict(orient="records")
    return [
        {str(key): _json_safe_value(value) for key, value in row.items()}
        for row in records
    ]


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]

    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _json_safe_value(item_method())
        except (TypeError, ValueError):
            pass
    return str(value)


def _normalize_name(value: str) -> str:
    normalized = "".join(value.split()).lower()
    for suffix in (
        "股份有限公司",
        "有限责任公司",
        "有限公司",
        "集团",
        "控股",
        "公司",
        "a",
        "Ａ",
    ):
        normalized = normalized.replace(suffix.lower(), "")
    return normalized


def _as_string(value: Any) -> str:
    return "" if value in (None, "") else str(value).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unavailable(reason: str, message: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "source": "akshare",
        "reason": reason,
        "message": message,
        "updated_at": _utc_now(),
    }
