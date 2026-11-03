from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from enterprise_sentinel.company_profile.contract import (
    MODULE_SPECS,
    OPTIONAL_MODULE_KEYS,
    ModuleSpec,
)
from enterprise_sentinel.engine import BrowserConfig, CrawlerEngine, LoginRequiredError


class CaptchaRequiredError(RuntimeError):
    """页面要求人工完成验证码或滑块验证。"""


class CompanyIdentityError(RuntimeError):
    """当前页面不能确认是目标企业。"""


class ModuleExtractionError(RuntimeError):
    """模块页面已打开，但没有形成可校验的结构化结果。"""


Heartbeat = Callable[[int, str, dict[str, str]], None]


ACCESS_PROBE_SCRIPT = r"""
const text = (document.body && document.body.innerText) || "";
return {
  captcha: ["滑块验证", "人机验证", "安全验证", "请输入验证码"].some((token) => text.includes(token)),
  permissionDenied: ["暂无权限", "无权访问", "权限不足"].some((token) => text.includes(token)),
  regionDenied: text.includes("暂不支持中国大陆以外地区的访问") || text.includes("请确认您的IP"),
};
"""


TARGET_SNAPSHOT_SCRIPT = r"""
const config = __CONFIG__;
const clean = (value) => (value || "").replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
const visible = (node) => {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
};
const headingFor = (node) => {
  let cursor = node;
  for (let level = 0; cursor && level < 8; level += 1, cursor = cursor.parentElement) {
    let previous = cursor.previousElementSibling;
    for (let step = 0; previous && step < 8; step += 1, previous = previous.previousElementSibling) {
      const candidates = previous.matches("h1,h2,h3,h4,h5,h6,[role=heading]")
        ? [previous]
        : Array.from(previous.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading],[class*=title],[class*=header]"));
      const match = candidates.reverse().find((item) => visible(item) && clean(item.innerText).length <= 80);
      if (match) return clean(match.innerText);
    }
  }
  return "";
};
const findMarker = () => {
  for (const selector of config.selectors) {
    try {
      const match = document.querySelector(selector);
      if (visible(match)) return match;
    } catch (_) {}
  }
  const candidates = Array.from(document.querySelectorAll(
    "h1,h2,h3,h4,h5,h6,[role=heading],[class*=title],[class*=header]"
  )).filter(visible);
  return candidates.find((node) => {
    const value = clean(node.innerText);
    return config.headings.some((heading) => value === heading || value.includes(heading));
  }) || null;
};
const rootFor = (marker) => {
  if (config.scanScope === "page") return document.body || document.documentElement;
  let cursor = marker;
  for (let level = 0; cursor && level < 8; level += 1, cursor = cursor.parentElement) {
    const text = clean(cursor.innerText);
    const hasData = Boolean(cursor.querySelector(
      "table,dl,.ant-descriptions,.el-descriptions,[class*=table],[class*=list]"
    ));
    const hasEmpty = config.emptyTokens.some((token) => text.includes(token));
    if (level > 0 && (hasData || hasEmpty)) return cursor;
  }
  return marker.parentElement || marker;
};
const marker = findMarker() || (config.scanScope === "page" ? document.documentElement : null);
if (!marker) {
  return {found: false, title: document.title, url: location.href, text: "", tables: [], keyValues: []};
}
try { marker.scrollIntoView({block: "center", inline: "nearest"}); } catch (_) {}
const root = rootFor(marker);
const tables = Array.from(root.querySelectorAll("table"))
  .filter(visible)
  .map((table) => ({
    title: headingFor(table),
    rows: Array.from(table.querySelectorAll("tr"))
      .filter(visible)
      .map((row) => Array.from(row.querySelectorAll("th,td")).filter(visible).map((cell) => {
        const before = Array.from(cell.querySelectorAll(".chg-before li,[class*=before] li"))
          .filter(visible).map((item) => clean(item.innerText)).filter(Boolean);
        const after = Array.from(cell.querySelectorAll(".chg-after li,[class*=after] li"))
          .filter(visible).map((item) => clean(item.innerText)).filter(Boolean);
        if (before.length || after.length) return [before.join("\n"), after.join("\n")];
        const clone = cell.cloneNode(true);
        clone.querySelectorAll("img,svg,[class*=avatar],[class*=logo],[class*=icon]").forEach((item) => item.remove());
        const value = clean(clone.innerText || clone.textContent || "");
        if (!value && cell.querySelector("a,button,svg,[role=button],[class*=icon]")) return "X";
        return value;
      }).flat())
      .filter((row) => row.some(Boolean)),
  }))
  .filter((table) => table.rows.length > 0);
const keyValues = [];
const addPair = (label, value) => {
  label = clean(label).replace(/[：:]$/, ""); value = clean(value);
  if (label && value && label !== value && label.length <= 60 && value.length <= 4000) keyValues.push([label, value]);
};
root.querySelectorAll("dl").forEach((dl) => {
  const terms = Array.from(dl.querySelectorAll(":scope > dt")).filter(visible);
  terms.forEach((term) => {
    let value = term.nextElementSibling;
    while (value && value.tagName !== "DD") value = value.nextElementSibling;
    if (value && visible(value)) addPair(term.innerText, value.innerText);
  });
});
[
  [".ant-descriptions-item-label", ".ant-descriptions-item-content"],
  [".el-descriptions__label", ".el-descriptions__content"],
  ["[class*=label]", "[class*=value]"]
].forEach(([labelSelector, valueSelector]) => {
  root.querySelectorAll(labelSelector).forEach((label) => {
    if (!visible(label)) return;
    const parent = label.parentElement;
    const value = parent && parent.querySelector(valueSelector);
    if (value && value !== label && visible(value)) addPair(label.innerText, value.innerText);
  });
});
const uniquePairs = [];
const seenPairs = new Set();
keyValues.forEach((pair) => {
  const key = pair.join("\u0000");
  if (!seenPairs.has(key)) { seenPairs.add(key); uniquePairs.push(pair); }
});
const rootText = clean(root.innerText || "");
const emptyState = config.emptyTokens.some((token) => rootText.includes(token));
const rootLines = rootText.split("\n").map(clean).filter(Boolean);
let expectedCount = null;
for (let index = 0; index < rootLines.length - 1; index += 1) {
  if (config.headings.some((heading) => rootLines[index] === heading) && /^\d+$/.test(rootLines[index + 1])) {
    expectedCount = Number(rootLines[index + 1]);
    break;
  }
}
return {
  found: true,
  ready: tables.length > 0 || emptyState || (config.acceptKeyValues && uniquePairs.length > 0),
  title: document.title,
  url: location.href,
  text: rootText.slice(0, 8000),
  emptyState,
  expectedCount,
  tables,
  keyValues: uniquePairs.slice(0, 300),
};
"""


TARGET_NEXT_SCRIPT = r"""
const config = __CONFIG__;
const clean = (value) => (value || "").replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").trim();
const visible = (node) => {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  return style.display !== "none" && style.visibility !== "hidden" && node.getClientRects().length > 0;
};
const findMarker = () => {
  for (const selector of config.selectors) {
    try {
      const match = document.querySelector(selector);
      if (visible(match)) return match;
    } catch (_) {}
  }
  return Array.from(document.querySelectorAll(
    "h1,h2,h3,h4,h5,h6,[role=heading],[class*=title],[class*=header]"
  )).filter(visible).find((node) => {
    const value = clean(node.innerText);
    return config.headings.some((heading) => value === heading || value.includes(heading));
  }) || null;
};
const rootFor = (marker) => {
  if (config.scanScope === "page") return document.body || document.documentElement;
  let cursor = marker;
  for (let level = 0; cursor && level < 8; level += 1, cursor = cursor.parentElement) {
    if (level > 0 && cursor.querySelector("table,[class*=table],[class*=list]")) return cursor;
  }
  return marker.parentElement || marker;
};
const marker = findMarker() || (config.scanScope === "page" ? document.documentElement : null);
if (!marker) return false;
const root = rootFor(marker);
const candidates = Array.from(root.querySelectorAll("button,a,li"));
const next = candidates.find((node) => {
  if (!visible(node)) return false;
  const className = String(node.className || "").toLowerCase();
  const label = `${clean(node.innerText)} ${clean(node.getAttribute("aria-label"))}`;
  const disabled = node.hasAttribute("disabled") ||
    String(node.getAttribute("aria-disabled") || "").toLowerCase() === "true" ||
    className.includes("disabled");
  if (disabled) return false;
  return label.includes("下一页") || label.toLowerCase().includes("next") ||
    /(^|[-_ ])next($|[-_ ])/.test(className);
});
if (!next) return false;
next.click();
return true;
"""


def _clean_text(value: Any) -> str:
    return "\n".join(
        line.strip()
        for line in str(value or "").replace("\u00a0", " ").splitlines()
        if line.strip()
    )


def _deduplicate_rows(rows: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for row in rows:
        cleaned = [_clean_text(item) for item in row]
        if not any(cleaned):
            continue
        marker = tuple(cleaned)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(cleaned)
    return result


def _clean_avatar_prefix(value: str) -> str:
    text = _clean_text(value).replace("\n", " ")
    tokens = text.split()
    if len(tokens) >= 2 and len(tokens[0]) == 1 and tokens[1].startswith(tokens[0]):
        return " ".join(tokens[1:])
    if len(text) > 1 and text[0] == text[1]:
        return text[1:]
    return text


def _clean_entity_label(value: str) -> str:
    text = _clean_avatar_prefix(value)
    endings = ("有限责任公司", "有限公司")
    end = max((text.rfind(item) + len(item) for item in endings if item in text), default=0)
    if end <= 3 or end >= len(text):
        return text
    company = text[:end]
    suffix = text[end:]
    known_tags = ("高新技术企业", "央企子", "央企", "国企", "民企")
    tags: list[str] = []
    while suffix:
        tag = next((item for item in known_tags if suffix.startswith(item)), None)
        if not tag:
            return text
        if tag not in tags:
            tags.append(tag)
        suffix = suffix[len(tag) :]
    return f"{company}（{'；'.join(tags)}）" if tags else company


OVERVIEW_FIELD_ORDER = (
    "企业名称", "登记状态", "科创分", "信用分", "曾用名", "企业标签",
    "法定代表人", "联系电话", "同电话企业", "统一社会信用代码", "成立日期",
    "电子邮箱", "同邮箱企业", "企业类型", "注册资本", "网站", "国标行业",
    "实缴资本", "注册地址", "企业规模", "登记机关", "通信地址", "从业人数",
    "最新变更", "年报", "营业收入", "经营范围",
)


def _normalize_overview_pairs(rows: list[list[str]]) -> list[list[str]]:
    pairs = _deduplicate_rows(rows)
    values: dict[str, str] = {}
    extensions: list[list[str]] = []
    for row in pairs:
        if len(row) < 2:
            continue
        label, value = row[0], row[1]
        if label == "电子邮箱":
            match = re.match(r"^(.*?)[；;]?同邮箱企业\s*(\d+)\s*家?$", value)
            if match:
                value = match.group(1).strip()
                values.setdefault("同邮箱企业", f"{match.group(2)}家")
        if label in OVERVIEW_FIELD_ORDER:
            if value and label in values and value not in values[label].split("；"):
                values[label] = f"{values[label]}；{value}"
            elif value:
                values.setdefault(label, value)
        else:
            extensions.append([label, value])
    ordered = [[label, values[label]] for label in OVERVIEW_FIELD_ORDER if values.get(label)]
    return ordered + extensions


def _header_score(row: list[str], expected: tuple[str, ...]) -> int:
    return sum(
        1
        for expected_header in expected
        if any(
            expected_header in cell or cell in expected_header
            for cell in row
            if cell
        )
    )


def _normalize_table(
    raw_rows: list[list[str]],
    expected: tuple[str, ...],
) -> tuple[list[str], list[list[str]]] | None:
    rows = _deduplicate_rows(raw_rows)
    if not rows:
        return None
    if expected:
        scores = [_header_score(row, expected) for row in rows[:4]]
        best_index = max(range(len(scores)), key=scores.__getitem__)
        threshold = min(2, len(expected))
        if scores[best_index] < threshold:
            return None
        columns = rows[best_index]
        data_rows = rows[best_index + 1 :]
    else:
        columns = rows[0]
        data_rows = rows[1:]
    columns = [column or f"字段{index + 1}" for index, column in enumerate(columns)]
    width = max([len(columns)] + [len(row) for row in data_rows])
    if width > len(columns):
        columns.extend(
            f"扩展字段{index + 1}"
            for index in range(width - len(columns))
        )
    data_rows = [row + [""] * (width - len(row)) for row in data_rows]
    for column_index, column in enumerate(columns):
        if not re.search(r"企业名称|姓名|股东", column):
            continue
        is_entity = bool(re.search(r"企业名称|股东", column))
        for row in data_rows:
            row[column_index] = (
                _clean_entity_label(row[column_index])
                if is_entity
                else _clean_avatar_prefix(row[column_index])
            )
    return columns, data_rows


def sections_from_snapshot(spec: ModuleSpec, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if spec.key == "overview":
        sections: list[dict[str, Any]] = []
        pairs = _normalize_overview_pairs(snapshot.get("keyValues") or [])
        if pairs:
            sections.append(
                {"title": "基本信息", "columns": ["字段", "内容"], "rows": pairs}
            )
        for index, table in enumerate(snapshot.get("tables") or []):
            normalized = _normalize_table(table.get("rows") or [], ())
            if not normalized:
                continue
            columns, rows = normalized
            sections.append(
                {
                    "title": table.get("title") or f"扩展信息 {index + 1}",
                    "columns": columns,
                    "rows": rows,
                }
            )
        return sections

    tables = list(snapshot.get("tables") or [])
    sections = []
    if spec.key == "shareholders":
        for label, value in _deduplicate_rows(snapshot.get("keyValues") or []):
            if "疑似实际控制人" in label or label == "实际控制人":
                sections.append(
                    {
                        "kind": "note",
                        "title": label,
                        "text": f"{label}：{value}",
                        "emphasis": True,
                        "columns": [],
                        "rows": [],
                    }
                )
            elif "控制路径" in label:
                sections.append(
                    {
                        "kind": "note",
                        "title": label,
                        "text": f"{label}：{value}",
                        "emphasis": False,
                        "columns": [],
                        "rows": [],
                    }
                )
    used_indexes: set[int] = set()
    for index, table in enumerate(tables):
        if index in used_indexes:
            continue
        rows = table.get("rows") or []
        expected = spec.expected_headers
        if spec.key == "penalties":
            visible_text = " ".join(" ".join(row) for row in rows[:4])
            title_text = str(table.get("title") or "")
            if not any(
                token in f"{title_text} {visible_text}"
                for token in ("处罚", "监管", "催报", "税种", "披露日期")
            ):
                continue
            expected = ()
        normalized = _normalize_table(rows, expected)
        if not normalized:
            continue
        columns, data_rows = normalized
        if spec.key == "business_changes":
            if len(columns) >= 5:
                columns[:5] = ["序号", "变更时间", "变更项目", "变更前", "变更后"]
            elif len(columns) == 4:
                columns[3] = "变更内容（原页面）"
        if not data_rows and index + 1 < len(tables):
            following = _deduplicate_rows(tables[index + 1].get("rows") or [])
            joined = _normalize_table([columns] + following, ()) if following else None
            if joined:
                columns, data_rows = joined
                used_indexes.add(index + 1)
        sections.append(
            {
                "title": table.get("title") or spec.title,
                "columns": columns,
                "rows": data_rows,
            }
        )
    return sections


def _merge_sections(
    destination: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for section in incoming:
        marker = (section.get("title"), tuple(section.get("columns") or []))
        existing = next(
            (
                item
                for item in destination
                if (item.get("title"), tuple(item.get("columns") or [])) == marker
            ),
            None,
        )
        if existing is None:
            destination.append(section)
            continue
        existing["rows"] = _deduplicate_rows(
            list(existing.get("rows") or []) + list(section.get("rows") or [])
        )
    return destination


def _business_change_needs_review(sections: list[dict[str, Any]]) -> bool:
    table_sections = [section for section in sections if section.get("kind") != "note"]
    for section in table_sections:
        columns = list(section.get("columns") or [])
        rows = list(section.get("rows") or [])
        if len(columns) < 5:
            return True
        if rows and all(len(row) < 5 or not _clean_text(row[4]) for row in rows):
            return True
    return False


class CompanyProfileCollector:
    def __init__(
        self,
        browser_config: BrowserConfig,
        *,
        max_pages: int = 50,
        retry_times: int = 2,
        target_wait_seconds: float = 8.0,
        retry_wait_seconds: float = 15.0,
        page_change_wait_seconds: float = 6.0,
        poll_interval: float = 0.25,
    ) -> None:
        self.engine = CrawlerEngine("qyyjt", browser_config)
        self.max_pages = max_pages
        self.retry_times = retry_times
        self.target_wait_seconds = target_wait_seconds
        self.retry_wait_seconds = retry_wait_seconds
        self.page_change_wait_seconds = page_change_wait_seconds
        self.poll_interval = poll_interval

    def close(self) -> None:
        self.engine.close()

    def collect(
        self,
        company_name: str,
        *,
        company_code: str = "",
        heartbeat: Heartbeat | None = None,
        specs: tuple[ModuleSpec, ...] = MODULE_SPECS,
    ) -> dict[str, Any]:
        run_started = time.perf_counter()
        code_started = time.perf_counter()
        code = company_code or self._resolve_company_code(company_name)
        code_lookup_ms = self._elapsed_ms(code_started)
        captured_at = datetime.now(timezone.utc)
        modules: dict[str, Any] = {}
        raw_modules: dict[str, Any] = {}
        statuses: dict[str, str] = {}
        pages: dict[str, Any] = {}
        loaded_families: set[str] = set()
        module_timings: dict[str, dict[str, Any]] = {}
        module_errors: dict[str, str] = {}

        try:
            for index, spec in enumerate(specs):
                if heartbeat:
                    heartbeat(index, spec.key, statuses)
                page = pages.get(spec.page)
                if page is None:
                    page = self.engine.browser.new_tab()
                    pages[spec.page] = page
                last_error: Exception | None = None
                attempts: list[dict[str, Any]] = []
                for attempt in range(1, self.retry_times + 1):
                    attempt_started = time.perf_counter()
                    try:
                        module, raw = self._collect_module(
                            page,
                            spec,
                            company_name,
                            code,
                            force_reload=(spec.page not in loaded_families or attempt > 1),
                            fallback_scope=attempt > 1,
                            retry_count=attempt - 1,
                        )
                        loaded_families.add(spec.page)
                        attempts.append(
                            {
                                "attempt": attempt,
                                "status": "completed",
                                "duration_ms": self._elapsed_ms(attempt_started),
                            }
                        )
                        module["timing"]["attempts"] = attempts
                        raw["timing"] = module["timing"]
                        modules[spec.key] = module
                        raw_modules[spec.key] = raw
                        module_timings[spec.key] = module["timing"]
                        statuses[spec.key] = module["status"]
                        break
                    except (LoginRequiredError, CaptchaRequiredError, PermissionError):
                        raise
                    except Exception as exc:
                        last_error = exc
                        attempts.append(
                            {
                                "attempt": attempt,
                                "status": "failed",
                                "duration_ms": self._elapsed_ms(attempt_started),
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            }
                        )
                else:
                    statuses[spec.key] = "FAILED"
                    message = f"{spec.title} 连续 {self.retry_times} 次采集失败：{last_error}"
                    if spec.key not in OPTIONAL_MODULE_KEYS:
                        raise ModuleExtractionError(message)
                    # 司法模块失败只记录原因，核心模块照常发布。
                    module_errors[spec.key] = message[:500]
                if heartbeat:
                    heartbeat(index + 1, spec.key, statuses)
        finally:
            for page in pages.values():
                try:
                    page.close()
                except Exception:
                    pass

        performance = {
            "timing_granularity": "collector_stage",
            "total_ms": self._elapsed_ms(run_started),
            "company_code_lookup_ms": code_lookup_ms,
            "page_family_count": len(pages),
            "module_timings": module_timings,
            "module_errors": module_errors,
        }

        return {
            "company_name": company_name,
            "company_code": code,
            "captured_at": captured_at,
            "module_statuses": statuses,
            "normalized_data": {
                "company_name": company_name,
                "company_code": code,
                "captured_at": captured_at.isoformat(),
                "modules": modules,
                "performance_summary": performance,
            },
            "raw_data": {
                "company_name": company_name,
                "company_code": code,
                "captured_at": captured_at.isoformat(),
                "modules": raw_modules,
                "performance": performance,
            },
        }

    def _resolve_company_code(self, company_name: str) -> str:
        page = self.engine.browser.new_tab()
        try:
            page.get(self.engine.profile.home_url)
            self._check_access(page)
            self.engine._search_company(page, company_name)
            clicked = self.engine._click_first_existing(
                page,
                self.engine._render_company_locators(company_name),
                required=False,
            )
            if not clicked:
                raise CompanyIdentityError(f"搜索结果中未找到精确企业：{company_name}")
            time.sleep(2)
            self._check_access(page)
            code = parse_qs(urlparse(str(page.url)).query).get("code", [""])[0]
            if not code:
                raise CompanyIdentityError("企业详情 URL 中没有 company_code。")
            self._check_identity(page, company_name, code, "企业详情")
            return code
        finally:
            page.close()

    def _collect_module(
        self,
        page,
        spec: ModuleSpec,
        company_name: str,
        company_code: str,
        *,
        force_reload: bool,
        fallback_scope: bool,
        retry_count: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        total_started = time.perf_counter()
        source_url = spec.url(company_code)
        snapshots: list[dict[str, Any]] = []
        sections: list[dict[str, Any]] = []
        navigation_started = time.perf_counter()
        if force_reload:
            page.get(source_url)
        else:
            self._activate_module(page, spec)
        navigation_ms = self._elapsed_ms(navigation_started)

        self._check_access(page)

        target_wait_started = time.perf_counter()
        snapshot = self._wait_for_snapshot(
            page,
            spec,
            timeout=(self.retry_wait_seconds if fallback_scope else self.target_wait_seconds),
            fallback_scope=fallback_scope,
        )
        target_wait_ms = self._elapsed_ms(target_wait_started)
        self._check_access(page)
        self._check_identity(page, company_name, company_code, spec.title)
        extract_ms = 0
        pagination_wait_ms = 0

        for _ in range(self.max_pages):
            snapshots.append(snapshot)
            extract_started = time.perf_counter()
            sections = _merge_sections(sections, sections_from_snapshot(spec, snapshot))
            extract_ms += self._elapsed_ms(extract_started)
            before = self._snapshot_fingerprint(snapshot)
            if not self._click_target_next(page, spec, fallback_scope=fallback_scope):
                break
            pagination_started = time.perf_counter()
            snapshot = self._wait_for_changed_snapshot(
                page,
                spec,
                before,
                fallback_scope=fallback_scope,
            )
            pagination_wait_ms += self._elapsed_ms(pagination_started)
        else:
            raise ModuleExtractionError(
                f"{spec.title} 分页超过安全上限 {self.max_pages}，任务已停止。"
            )

        table_sections = [
            section for section in sections if section.get("kind") != "note"
        ]
        row_count = sum(len(section.get("rows") or []) for section in table_sections)
        display_total = max(
            (
                int(item["expectedCount"])
                for item in snapshots
                if isinstance(item.get("expectedCount"), (int, float))
            ),
            default=None,
        )
        has_empty_state = any(bool(item.get("emptyState")) for item in snapshots)
        if not table_sections:
            if has_empty_state:
                sections = [
                    {
                        "title": spec.title,
                        "columns": list(spec.empty_columns),
                        "rows": [],
                    }
                ]
                status = "EMPTY_VALID"
            else:
                raise ModuleExtractionError(f"{spec.title} 未识别到目标表格或空状态。")
        else:
            if row_count:
                status = "PASS"
            elif has_empty_state:
                status = "EMPTY_VALID"
            else:
                raise ModuleExtractionError(
                    f"{spec.title} 已识别表头，但没有数据行或明确空状态。"
                )
        warnings: list[str] = []
        if display_total is not None and row_count < display_total:
            status = "PARTIAL"
            warnings.append(
                f"页面显示 {display_total} 条，实际采集 {row_count} 条。"
            )
        if spec.key == "business_changes" and _business_change_needs_review(sections):
            status = "PARTIAL"
            warnings.append("工商变更未能稳定拆分为变更前和变更后。")
        timing = {
            "navigation_ms": navigation_ms,
            "target_wait_ms": target_wait_ms,
            "extract_ms": extract_ms,
            "pagination_wait_ms": pagination_wait_ms,
            "total_ms": self._elapsed_ms(total_started),
            "retry_count": retry_count,
            "fallback_level": "L1_PAGE_SCOPE" if fallback_scope else "L0_TARGET",
        }
        module = {
            "key": spec.key,
            "title": spec.title,
            "status": status,
            "source_url": source_url,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "page_count": len(snapshots),
            "display_total": display_total,
            "row_count": row_count,
            "sections": sections,
            "warnings": warnings,
            "timing": timing,
        }
        return module, {
            "source_url": source_url,
            "pages": snapshots,
            "timing": timing,
        }

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))

    @staticmethod
    def _snapshot_config(spec: ModuleSpec, *, fallback_scope: bool) -> dict[str, Any]:
        return {
            "selectors": list(spec.container_selectors),
            "headings": list(dict.fromkeys((spec.title, spec.anchor, *spec.heading_aliases))),
            "emptyTokens": ["暂无数据", "暂无记录", "无数据"],
            "scanScope": "page" if fallback_scope else spec.scan_scope,
            "acceptKeyValues": spec.key == "overview",
        }

    def _render_script(
        self,
        template: str,
        spec: ModuleSpec,
        *,
        fallback_scope: bool,
    ) -> str:
        config = self._snapshot_config(spec, fallback_scope=fallback_scope)
        return template.replace("__CONFIG__", json.dumps(config, ensure_ascii=False))

    def _activate_module(self, page, spec: ModuleSpec) -> None:
        anchor = json.dumps(spec.anchor, ensure_ascii=False)
        page.run_js(
            f"window.location.hash = {anchor}; return window.location.href;"
        )

    def _wait_for_snapshot(
        self,
        page,
        spec: ModuleSpec,
        *,
        timeout: float,
        fallback_scope: bool,
    ) -> dict[str, Any]:
        script = self._render_script(
            TARGET_SNAPSHOT_SCRIPT,
            spec,
            fallback_scope=fallback_scope,
        )
        deadline = time.monotonic() + timeout
        last_snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            snapshot = page.run_js(script) or {}
            if isinstance(snapshot, dict):
                last_snapshot = snapshot
                if snapshot.get("found") and snapshot.get("ready"):
                    return snapshot
            time.sleep(self.poll_interval)
        self._check_access(page)
        raise ModuleExtractionError(
            f"{spec.title} 在 {timeout:g} 秒内未出现目标容器。"
            + (f" 最后页面：{last_snapshot.get('url')}" if last_snapshot else "")
        )

    @staticmethod
    def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
        return json.dumps(
            {
                "tables": snapshot.get("tables") or [],
                "keyValues": snapshot.get("keyValues") or [],
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _wait_for_changed_snapshot(
        self,
        page,
        spec: ModuleSpec,
        previous_fingerprint: str,
        *,
        fallback_scope: bool,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.page_change_wait_seconds
        script = self._render_script(
            TARGET_SNAPSHOT_SCRIPT,
            spec,
            fallback_scope=fallback_scope,
        )
        while time.monotonic() < deadline:
            snapshot = page.run_js(script) or {}
            if (
                snapshot.get("found")
                and snapshot.get("ready")
                and self._snapshot_fingerprint(snapshot) != previous_fingerprint
            ):
                return snapshot
            time.sleep(self.poll_interval)
        raise ModuleExtractionError(f"{spec.title} 点击下一页后内容未发生变化。")

    def _click_target_next(
        self,
        page,
        spec: ModuleSpec,
        *,
        fallback_scope: bool,
    ) -> bool:
        script = self._render_script(
            TARGET_NEXT_SCRIPT,
            spec,
            fallback_scope=fallback_scope,
        )
        try:
            return bool(page.run_js(script))
        except Exception:
            return self._click_visible_next(page)

    def _check_access(self, page) -> None:
        self.engine._raise_if_login_redirect(page)
        try:
            state = page.run_js(ACCESS_PROBE_SCRIPT) or {}
        except Exception:
            body = _clean_text(page.text)
            state = {
                "captcha": any(
                    token in body
                    for token in ("滑块验证", "人机验证", "安全验证", "请输入验证码")
                ),
                "permissionDenied": any(
                    token in body for token in ("暂无权限", "无权访问", "权限不足")
                ),
                "regionDenied": any(
                    token in body
                    for token in ("暂不支持中国大陆以外地区的访问", "请确认您的IP")
                ),
            }
        if state.get("captcha"):
            raise CaptchaRequiredError("企业预警通要求人工完成验证码或滑块验证。")
        if state.get("permissionDenied"):
            raise PermissionError("当前企业预警通账号无权访问该页面。")
        if state.get("regionDenied"):
            raise PermissionError(
                "企业预警通拒绝当前网络出口：站点暂不支持中国大陆以外地区访问，请切换到合规的中国大陆网络后重新采集。"
            )

    @staticmethod
    def _check_identity(page, company_name: str, company_code: str, module: str) -> None:
        page_code = parse_qs(urlparse(str(page.url)).query).get("code", [""])[0]
        if page_code != company_code:
            raise CompanyIdentityError(f"{module} 页面企业代码与任务不一致。")
        company_literal = json.dumps(company_name, ensure_ascii=False)
        try:
            name_matches = bool(
                page.run_js(
                    "return Boolean(document.body && "
                    f"document.body.innerText.includes({company_literal}));"
                )
            )
        except Exception:
            name_matches = company_name in _clean_text(page.text)
        if not name_matches:
            raise CompanyIdentityError(f"{module} 页面未显示目标企业名称。")

    def _click_visible_next(self, page) -> bool:
        for locator in self.engine.profile.next_page_candidates:
            try:
                elements = page.eles(locator)
            except Exception:
                elements = []
            for element in elements:
                try:
                    class_name = str(element.attr("class") or "").lower()
                    disabled = element.attr("disabled") is not None or str(
                        element.attr("aria-disabled") or ""
                    ).lower() == "true"
                    if disabled or "disabled" in class_name:
                        continue
                    if hasattr(element, "states") and not element.states.is_displayed:
                        continue
                    element.click()
                    return True
                except Exception:
                    continue
        return False
