from __future__ import annotations

import unicodedata

from app.config import get_settings
from app.models import Company
from app.services.company_profile import resolve_profile_company_name


class DemoScopeError(ValueError):
    """The requested company is outside the configured single-company demo."""


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())


def demo_company_name() -> str:
    return get_settings().demo_company_name


def demo_mode_enabled() -> bool:
    return bool(demo_company_name())


def canonical_company_name(submitted: str) -> str:
    canonical = resolve_profile_company_name(submitted.strip()) or submitted.strip()
    target = demo_company_name()
    if not target:
        return canonical
    submitted_key = _key(canonical)
    target_key = _key(target)
    # 允许"携程金融""上海携程金融"等常见简称直接命中目标企业，
    # 不要求逐字匹配法定全称；但要求片段足够长以避免误判到无关公司。
    if submitted_key == target_key or (len(submitted_key) >= 4 and submitted_key in target_key):
        return target
    raise DemoScopeError(f"演示环境仅支持分析“{target}”。")


def ensure_demo_company(company: Company | None) -> Company:
    if company is None:
        raise DemoScopeError("公司不存在。")
    target = demo_company_name()
    if target and _key(company.name) != _key(target):
        raise DemoScopeError(f"演示环境仅支持访问“{target}”。")
    return company
