from __future__ import annotations

from dataclasses import dataclass


SOP_VERSION = "QYYJT-COMPANY-PROFILE-V3.2"


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    title: str
    page: str
    anchor: str
    expected_headers: tuple[str, ...]
    empty_columns: tuple[str, ...]
    container_selectors: tuple[str, ...] = ()
    heading_aliases: tuple[str, ...] = ()
    scan_scope: str = "target"

    def url(self, company_code: str) -> str:
        return (
            f"https://www.qyyjt.cn/detail/enterprise/{self.page}"
            f"?code={company_code}&type=company#{self.anchor}"
        )


MODULE_SPECS = (
    ModuleSpec(
        "overview",
        "企业速览",
        "overview",
        "企业速览",
        (),
        ("字段", "内容"),
        container_selectors=(
            '[id="企业速览"]',
            '[data-anchor="企业速览"]',
            '[name="企业速览"]',
        ),
        heading_aliases=("企业速览", "基本信息"),
        scan_scope="page",
    ),
    ModuleSpec(
        "penalties",
        "监管处罚",
        "creditData",
        "监管处罚",
        ("披露日期", "处罚"),
        ("序号", "披露日期", "处罚日期", "处罚类型", "违规原因"),
        container_selectors=(
            '[id="监管处罚"]',
            '[data-anchor="监管处罚"]',
            '[name="监管处罚"]',
        ),
        heading_aliases=("监管处罚", "行政处罚", "行政监管措施"),
        scan_scope="page",
    ),
    ModuleSpec(
        "dynamic_monitor",
        "动态监测",
        "monitor",
        "module-b7020b94",
        ("日期", "标题", "分类", "重要性", "正负面", "来源"),
        ("日期", "标题", "分类", "重要性", "正负面", "来源"),
        container_selectors=(
            "#module-b7020b94",
            '[id="module-b7020b94"]',
            '[data-anchor="module-b7020b94"]',
        ),
        heading_aliases=("动态监测", "动态", "监测"),
    ),
    ModuleSpec(
        "business_changes",
        "工商变更",
        "overview",
        "工商变更",
        ("变更时间", "变更项目"),
        ("序号", "变更时间", "变更项目", "变更前", "变更后"),
        container_selectors=(
            '[id="工商变更"]',
            '[data-anchor="工商变更"]',
            '[name="工商变更"]',
        ),
        heading_aliases=("工商变更",),
    ),
    ModuleSpec(
        "executives",
        "高管信息",
        "overview",
        "高管信息",
        ("姓名", "职务"),
        ("序号", "姓名", "职务"),
        container_selectors=(
            '[id="高管信息"]',
            '[data-anchor="高管信息"]',
            '[name="高管信息"]',
        ),
        heading_aliases=("高管信息", "主要人员"),
    ),
    ModuleSpec(
        "shareholders",
        "股东信息",
        "overview",
        "股东信息",
        ("股东名称", "持股比例"),
        (
            "序号",
            "股东名称/标签",
            "持股数量/认缴金额",
            "持股比例",
            "法定代表人",
            "成立日期",
            "注册资本",
        ),
        container_selectors=(
            '[id="股东信息"]',
            '[data-anchor="股东信息"]',
            '[name="股东信息"]',
        ),
        heading_aliases=("股东信息", "股东明细"),
    ),
    ModuleSpec(
        "investments",
        "对外投资企业",
        "overview",
        "对外投资企业",
        ("企业名称", "投资比例", "企业状态", "行业"),
        (
            "序号",
            "企业名称/标签",
            "投资比例",
            "成立日期",
            "法定代表人",
            "注册资本",
            "实缴资本",
            "企业状态",
            "省份",
            "行业",
        ),
        container_selectors=(
            '[id="对外投资企业"]',
            '[data-anchor="对外投资企业"]',
            '[name="对外投资企业"]',
        ),
        heading_aliases=("对外投资企业", "对外投资"),
    ),
    ModuleSpec(
        "subsidiaries",
        "控股子公司",
        "overview",
        "控股子公司",
        ("企业名称", "公司层级", "投资比例"),
        (
            "序号",
            "企业名称/标签",
            "公司层级",
            "投资比例",
            "注册资本",
            "成立日期",
            "法定代表人",
            "地区",
            "行业",
        ),
        container_selectors=(
            '[id="控股子公司"]',
            '[data-anchor="控股子公司"]',
            '[name="控股子公司"]',
        ),
        heading_aliases=("控股子公司", "子公司"),
    ),
)

# 司法模块的页面锚点需在已登录的 Windows Edge 上校准；采集失败只标记 FAILED，
# 不影响八个核心模块的快照发布。
LITIGATION_SPECS = (
    ModuleSpec(
        "judgments",
        "裁判文书",
        "creditData",
        "裁判文书",
        ("案由", "案号"),
        ("序号", "发布日期", "案件名称", "案由", "案号", "案件身份", "法院"),
        container_selectors=('[id="裁判文书"]', '[data-anchor="裁判文书"]', '[name="裁判文书"]'),
        heading_aliases=("裁判文书", "法律诉讼", "司法文书"),
        scan_scope="page",
    ),
    ModuleSpec(
        "court_hearings",
        "开庭公告",
        "creditData",
        "开庭公告",
        ("开庭日期", "案由"),
        ("序号", "开庭日期", "案由", "案号", "原告/上诉人", "被告/被上诉人", "法院"),
        container_selectors=('[id="开庭公告"]', '[data-anchor="开庭公告"]', '[name="开庭公告"]'),
        heading_aliases=("开庭公告", "开庭信息"),
        scan_scope="page",
    ),
    ModuleSpec(
        "case_filings",
        "立案信息",
        "creditData",
        "立案信息",
        ("立案日期", "案由"),
        ("序号", "立案日期", "案由", "案号", "原告", "被告", "法院"),
        container_selectors=('[id="立案信息"]', '[data-anchor="立案信息"]', '[name="立案信息"]'),
        heading_aliases=("立案信息", "立案公告"),
        scan_scope="page",
    ),
    ModuleSpec(
        "enforcement",
        "被执行人",
        "creditData",
        "被执行人",
        ("立案日期", "执行标的"),
        ("序号", "立案日期", "案号", "执行标的", "执行法院", "失信情况"),
        container_selectors=('[id="被执行人"]', '[data-anchor="被执行人"]', '[name="被执行人"]'),
        heading_aliases=("被执行人", "失信被执行人", "执行信息"),
        scan_scope="page",
    ),
)

OPTIONAL_MODULE_KEYS = frozenset(item.key for item in LITIGATION_SPECS)

# full：被投企业本体，八个核心模块加司法模块；
# monitor：已确认的合作方、关联方，只采法律和舆情所需模块。
MODE_CORE_KEYS = {
    "full": tuple(item.key for item in MODULE_SPECS),
    "monitor": ("penalties", "dynamic_monitor"),
}

MODULE_BY_KEY = {item.key: item for item in (*MODULE_SPECS, *LITIGATION_SPECS)}


def core_specs_for_mode(mode: str) -> tuple[ModuleSpec, ...]:
    keys = MODE_CORE_KEYS.get(mode, MODE_CORE_KEYS["full"])
    return tuple(MODULE_BY_KEY[key] for key in keys)


def specs_for_mode(mode: str) -> tuple[ModuleSpec, ...]:
    return (*core_specs_for_mode(mode), *LITIGATION_SPECS)
