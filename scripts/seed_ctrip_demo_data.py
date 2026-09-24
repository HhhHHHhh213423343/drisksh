"""为单公司演示补充携程金融的公开风险数据。

数据来源：国家外汇管理局官网、金融监管总局官网、网信上海、市场监管总局、天眼查、
百度百科、公开财经媒体（新浪财经/东方财富/经济观察报），以及用户 2026-09-23 整理的
行业监管态势与同业动态笔记。本脚本只写入有公开来源的记录，不生成任何推测数据。

运行方式（在项目根目录）：

    python3 scripts/seed_ctrip_demo_data.py
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.db.session import SessionLocal  # noqa: E402
from app.models import Company, MacroIndustryEvent, RiskEvent  # noqa: E402

COMPANY_NAME = "上海携程金融信息服务有限公司"

# ---------------------------------------------------------------- 公司档案修正
PROFILE_PATCH = {
    "industry": "互联网生产服务平台（金融科技 / 互联网金融服务）",
    "region": "上海市长宁区",
    "description": (
        "上海携程金融信息服务有限公司成立于2014年1月9日，曾用名上海携程商务咨询有限公司，"
        "注册资本25,000万元且已全额实缴，法定代表人章婷婷，注册地上海市长宁区金钟路968号，"
        "国标行业为互联网生产服务平台，2025年员工规模122人。公司为携程集团旗下金融科技服务平台，"
        "已取得保险代理、网络小贷、消费金融、第三方支付等金融牌照，"
        "业务涵盖消费金融与信贷、助贷与金融产品导流、供应链金融与小微金融、B2B跨境支付、保险经纪，"
        "主要产品包括“拿去花”“信用贷”。初始股东为携程旅游网络技术（上海）有限公司（58.8%）、"
        "银联国际有限公司（20%）、上海领偕商务咨询有限公司（9.6%）、"
        "克拉斯（北京）投资有限公司（7.2%）、北京中汇优通投资有限公司（4.4%）。"
    ),
}

WRONG_AKSHARE_MATCH = {"000402", "金 融 街", "金融街"}

# ------------------------------------------------------------------ 公司级事件
RISK_EVENTS = [
    {
        "category": "监管处罚",
        "severity": "important",
        "sentiment": "negative",
        "title": "市场监管总局对携程滥用市场支配地位作出行政处罚，罚没款合计51.79亿元",
        "content": (
            "2026年7月25日，市场监管总局对携程滥用市场支配地位案作出行政处罚，罚没款合计51.79亿元，"
            "为在线旅游行业反垄断首张巨额罚单。携程当日发布公告称诚恳接受、坚决服从，落实各项整改。"
            "被处罚主体属携程集团体系，与本公司同属携程系，需评估集团层面反垄断处罚对金融业务"
            "导流场景、品牌声誉及股东支持的传导影响。"
        ),
        "source_name": "市场监管总局 / 经济观察报",
        "source_url": "https://www.163.com/dy/article/L7JB1RCV0556FRM6.html",
        "occurred_at": "2026-07-25",
    },
    {
        "category": "监管处罚",
        "severity": "important",
        "sentiment": "negative",
        "title": "上海市网信办对上海携程商务有限公司罚款1000万元，涉数据出境违规",
        "content": (
            "2026年6月13日，据“网信上海”发布，在国家网信办指导下，上海市网信办针对属地企业网络数据"
            "安全主体责任履行不到位办理执法案件：上海携程商务有限公司因未落实数据出境安全评估要求、"
            "违法出境个人信息等行为，依据《个人信息保护法》被罚款1000万元并责令限期改正。"
            "上海携程商务有限公司为本公司股东（持股约6.17%），属同一集团体系，"
            "数据出境与个人信息合规问题在集团内具有共性，需同步排查本公司数据合规链路。"
        ),
        "source_name": "网信上海 / 上海市网信办",
        "source_url": "https://cj.sina.cn/articles/view/6697701948/18f36d23c00102chra",
        "occurred_at": "2026-06-13",
    },
    {
        "category": "监管处罚",
        "severity": "important",
        "sentiment": "negative",
        "title": "金融监管总局联合两部门约谈携程旅行等六家出行平台，直指借贷业务不规范",
        "content": (
            "2026年2月13日，金融监管总局联合市场监管总局、中国人民银行对携程旅行、高德地图、同程旅行、"
            "飞猪旅行、航旅纵横、去哪儿旅行等六家出行平台企业约谈，针对与金融机构合作开展借贷业务中"
            "存在的问题提出要求：规范营销行为、不得使用误导性宣传用语；清晰披露贷款机构名称及信贷产品"
            "信息并向借款人提示理性借贷；畅通客户投诉渠道，及时妥善处理消费纠纷。"
            "本公司为携程系金融业务运营主体，助贷与导流业务直接落在监管要求范围内。"
        ),
        "source_name": "国家金融监督管理总局",
        "source_url": "https://www.nfra.gov.cn/",
        "occurred_at": "2026-02-13",
    },
    {
        "category": "合规风险",
        "severity": "important",
        "sentiment": "negative",
        "title": "携程金融App因违规收集用户个人信息被监管部门通报",
        "content": (
            "国家计算机病毒应急处理中心检测发现携程金融存在违规行为，被列入67款违法违规App名单："
            "一是未经用户单独同意向第三方提供个人信息且未做匿名化处理，嵌入的第三方SDK"
            "（如信贷评估工具）在未明确告知的情况下将设备信息、通讯录等数据提供给合作方；"
            "二是用户借贷记录、身份证号等敏感信息收集时未单独取得授权。"
            "2023年8月上海网信办曾就同类问题指导整改，整改效果有限。"
        ),
        "source_name": "国家计算机病毒应急处理中心 / 财中社",
        "source_url": "https://finance.eastmoney.com/a/202504223384453224.html",
        "occurred_at": "2025-04-22",
    },
    {
        "category": "关联方风险",
        "severity": "normal",
        "sentiment": "negative",
        "title": "关联方尚诚消费金融因违反五项规则被罚160万元，两名负责人被警告",
        "content": (
            "2026年3月，携程金融关联方尚诚消费金融因违反五项规则被处以160万元罚款，两名负责人受到警告。"
            "另有报道称其助贷业务已协助41家机构，其中8家机构尚未列入监管白名单，违反2025年10月执行的"
            "《助贷新规》；“信用贷”页面标注年化利率24%，实际合同利率达35.94%，触及民间借贷利率红线。"
        ),
        "source_name": "新浪财经",
        "source_url": "https://cj.sina.cn/articles/view/6697701948/18f36d23c00102chra",
        "occurred_at": "2026-03-31",
    },
    {
        "category": "品牌舆情",
        "severity": "normal",
        "sentiment": "negative",
        "title": "暴力催收与信息泄露投诉持续，隐私管理漏洞多次被曝光",
        "content": (
            "公开报道显示，携程金融相关业务因暴力催收、信息泄露等问题收到大量投诉。"
            "黑猫投诉平台显示2025年4月仍有用户指控携程金融通过非法获取亲属信息进行暴力催收；"
            "2024年3·15晚会曾曝光携程入股的同程金融App礼品卡套路。"
            "相关投诉若持续发酵，可能引发监管介入与获客成本上升。"
        ),
        "source_name": "黑猫投诉 / 财中社",
        "source_url": "https://finance.eastmoney.com/a/202504223384453224.html",
        "occurred_at": "2026-04-30",
    },
    {
        "category": "法律风险",
        "severity": "normal",
        "sentiment": "neutral",
        "title": "司法信息显示存在开庭公告2条，需持续跟踪案件进展",
        "content": (
            "第三方企业信息平台显示，上海携程金融信息服务有限公司存在开庭公告2条，"
            "此外关联风险条目数量较多。具体案由、标的金额与进展需通过企业预警通或司法公开渠道进一步核验，"
            "暂不据此认定重大法律风险，但要纳入持续跟踪清单。"
        ),
        "source_name": "天眼查",
        "source_url": "https://www.tianyancha.com/company/2350710871",
        "occurred_at": "2026-09-01",
    },
    {
        "category": "监管处罚",
        "severity": "normal",
        "sentiment": "negative",
        "title": "市场监管总局年初对携程涉嫌利用市场支配地位问题立案调查",
        "content": (
            "2026年1月，市场监管总局基于《中华人民共和国反垄断法》对携程涉嫌利用市场支配地位的问题"
            "立案调查，重点关注“二选一”、单方面提升佣金、屏蔽竞争对手流量等行为。"
            "该案已于2026年7月作出行政处罚，罚没款合计51.79亿元。"
        ),
        "source_name": "市场监管总局 / 新浪财经",
        "source_url": "https://cj.sina.cn/articles/view/6697701948/18f36d23c00102chra",
        "occurred_at": "2026-01-31",
    },
]

# ------------------------------------------------------------------ 行业级事件
INDUSTRY_EVENTS = [
    {
        "scope_key": "外汇兑换与跨境支付",
        "dimension": "行业监管态势",
        "event_type": "监管会议",
        "title": "2026年上海市外汇管理工作会议：个人本外币业务两端发力",
        "summary": (
            "会议强调个人本外币业务需两端发力：一端继续提升个人用汇便利化水平，"
            "另一端同步加强便利化政策风险评估、异常资金流监测与非法跨境金融活动综合治理。"
            "风险分析：便利化与风险防控两端同步收紧意味着合规边界进一步细化、展业门槛持续抬升，"
            "对风控模型迭代、数据监测能力和应急处置机制都提出更高要求。监管资源客观上向"
            "合规体系完备的机构倾斜，合规底子薄、风险工具缺位的中小机构展业空间持续收窄。"
            "建议头部机构配套完善风险管控机制，落实业务合规要求，审慎布局便利化用汇场景下的业务渠道。"
        ),
        "source_name": "内部整理（用户2026-09-23提供）",
        "source_url": "",
        "published_at": "2026-09-23",
        "impact_direction": "neutral",
        "severity": "normal",
    },
    {
        "scope_key": "外汇兑换与跨境支付",
        "dimension": "同业动态",
        "event_type": "市场结构",
        "title": "非现钞支付增速显著快于现钞兑换，渠道资源向头部机构集中",
        "summary": (
            "行业最显著的结构变化是非现钞支付增速明显快于现钞兑换需求本身。2026年一季度上海外卡刷卡"
            "与“外卡内绑”“外包内用”移动支付交易笔数同比分别增长52%和104%，金额同比分别增长37%和97%；"
            "同期外币兑换交易金额同比增长15%，部分原本需要“先兑现金再消费”的入境支付需求"
            "正被外卡和移动支付直接消化。市场格局上，截至6月末上海6家特许经营主体在官方清单中共有41条"
            "机构及网点记录，其中携程金融20条、北京联合货币11条，合计占75.6%；"
            "2025年10月至2026年6月全市场净增6个网点中携程占5个，渠道资源向头部机构进一步集中。"
            "场景分布上，浦东机场已布局15家货币兑换点，占上海网点总数的36.5%，机场端仍为布局最密集的"
            "核心场景；携程金融布局非机场端支付赛道，打通陆家嘴、南京西路、中山公园、漕宝路等核心商圈"
            "兑换渠道，尚未形成明显规模效应，是否可实现对业务增长的拉动仍待观察。"
            "风险分析：建议头部机构在巩固核心商圈渠道卡位优势的同时，加快外卡受理与移动支付能力建设，"
            "把增量需求转化为有效业务，严控核心商圈依赖与渠道集中度带来的结构性风险。"
        ),
        "source_name": "内部整理（用户2026-09-23提供）",
        "source_url": "",
        "published_at": "2026-09-23",
        "impact_direction": "neutral",
        "severity": "normal",
    },
    {
        "scope_key": "外汇兑换与跨境支付",
        "dimension": "行业监管态势",
        "event_type": "监管会议",
        "title": "2026年全国外汇管理工作会议召开：深化便利化改革并强化事中事后监管",
        "summary": (
            "会议指出，外汇管理部门加强跨境资金流动宏观审慎管理和预期引导，严厉打击地下钱庄等"
            "外汇领域违法违规活动，查处违法违规案件1100余起。2026年重点工作中明确："
            "深化外汇便利化改革、加大力度支持跨境电商等贸易新业态发展；"
            "稳步推进外汇领域高水平制度型开放；坚持底线思维，加强外汇形势分析研判与宏观审慎管理；"
            "进一步巩固和强化外汇监管，严格规范公正文明执法，深化非现场监管能力建设，"
            "加强异常渠道和线索分析，加强外汇市场交易行为监管。"
        ),
        "source_name": "国家外汇管理局",
        "source_url": "http://m.safe.gov.cn/safe/2026/0106/27015.html",
        "published_at": "2026-01-06",
        "impact_direction": "negative",
        "severity": "normal",
    },
    {
        "scope_key": "外汇兑换与跨境支付",
        "dimension": "行业监管态势",
        "event_type": "制度发布",
        "title": "国家外汇管理局修订《个人本外币兑换特许业务试点管理办法》",
        "summary": (
            "国家外汇管理局修订《个人本外币兑换特许业务试点管理办法》，要求各级外汇管理部门"
            "坚持便捷准入与严格监管相结合，加强事中事后管理，有效履行属地金融监管职责，防范金融风险；"
            "外汇分局在首次批准辖内非金融机构开展兑换特许业务前应向总局备案。"
            "按现行规定，特许机构通过柜台或电子渠道为个人办理兑换业务，"
            "每人每天兑入与兑出上限均为等值5000美元，个人年度便利化额度为等值5万美元。"
            "制度修订对特许经营主体的系统对接、系统自动接口连接个人外汇业务系统提出明确要求。"
        ),
        "source_name": "国家外汇管理局",
        "source_url": "https://www.safe.gov.cn/",
        "published_at": "2026-03-04",
        "impact_direction": "neutral",
        "severity": "normal",
    },
    {
        "scope_key": "金融科技与数据合规",
        "dimension": "行业监管态势",
        "event_type": "制度发布",
        "title": "六部门印发《金融信息服务数据分类分级指南》",
        "summary": (
            "央行、国家金融监督管理总局、网信办、证监会、国家统计局、国家外汇管理局等六部门联合印发"
            "《金融信息服务数据分类分级指南》，适用对象为境内从事金融信息服务的金融信息服务提供者，"
            "范围覆盖互联网助贷平台等场景化平台。指南采用“三级分类、四级分级”体系："
            "按业务属性分为业务数据、用户数据、企业数据三大类，细化为9个二级类和67个三级类；"
            "数据由高到低分为核心数据、重要数据、敏感一般数据、常规一般数据，"
            "其中“敏感一般数据”为新增层级。同日上海市网信办对携程系企业开出1000万元罚单，"
            "释放金融信息服务数据合规进入“有标可依、违规必究”的信号。"
        ),
        "source_name": "中国网信网 / 六部门联合印发",
        "source_url": "https://www.cac.gov.cn/",
        "published_at": "2026-06-13",
        "impact_direction": "negative",
        "severity": "normal",
    },
    {
        "scope_key": "在线旅游平台",
        "dimension": "同业动态",
        "event_type": "监管执法",
        "title": "北京市场监管局对飞猪、同程、途家、美团酒旅立案调查，平台反垄断整治蔓延",
        "summary": (
            "2026年9月19日，北京市市场监督管理局依据《反不正当竞争法》《电子商务法》等，"
            "对飞猪、同程旅行、途家民宿、美团酒旅涉嫌违法行为正式立案调查，"
            "调查范围涵盖算法、营销、法务等业务条线，重点指向流量展示竞价排名、"
            "要求商家按全网最低价销售、剥夺商家定价自主权等违法行为。"
            "此前2026年9月15日市场监管总局会同文化和旅游部召开行政指导会，"
            "要求平台终止自动跟价、干预商家定价等行为。"
            "在线旅游行业“内卷式”竞争整治进入执法深水区，平台流量与场景金融的合规边界同步收紧。"
        ),
        "source_name": "北京市市场监督管理局 / 经济观察报",
        "source_url": "https://www.163.com/dy/article/L7JB1RCV0556FRM6.html",
        "published_at": "2026-09-19",
        "impact_direction": "negative",
        "severity": "normal",
    },
]


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def seed() -> None:
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.name == COMPANY_NAME).one_or_none()
        if company is None:
            print(f"[跳过] 未找到公司：{COMPANY_NAME}")
            return

        # 1. 修正公司档案：剔除错误 AkShare 匹配，补齐真实行业与工商信息
        profile = dict(company.company_profile or {})
        akshare = dict(profile.get("akshare_profile") or {})
        matched_name = str(akshare.get("matched_name") or "").strip()
        if akshare and (
            str(akshare.get("stock_code")) in WRONG_AKSHARE_MATCH
            or matched_name in WRONG_AKSHARE_MATCH
        ):
            profile["akshare_profile"] = {
                "status": "unavailable",
                "source": "akshare",
                "reason": (
                    "非上市主体，AkShare 名称模糊匹配结果（金融街/000402）已人工剔除，"
                    "避免财报数据串用。"
                ),
                "previous_match": {
                    "stock_code": akshare.get("stock_code"),
                    "matched_name": matched_name,
                },
            }
            print("[修正] 已剔除错误的 AkShare 上市公司匹配（金融街/000402）")
        # 顶层若残留上市公司代码，会导致分析直接拉取错误公司的财报，必须清除
        for key in ("stock_code", "market"):
            if key in profile:
                print(f"[修正] 已清除 profile.{key}={profile[key]}（非上市主体，避免财报串用）")
                profile.pop(key)
        # 巨潮资讯解析结果同样来自上述错误代码，一并置为不可用
        cninfo = dict(profile.get("cninfo_profile") or {})
        if cninfo and str(cninfo.get("stock_code")) in WRONG_AKSHARE_MATCH:
            profile["cninfo_profile"] = {
                "status": "unavailable",
                "reason": "非上市主体，巨潮资讯解析结果（金融街控股/000402）已剔除。",
            }
            print("[修正] 已清除错误的巨潮资讯公司解析结果（金融街控股/000402）")

        profile["business_profile"] = {
            "licenses": ["保险代理", "网络小贷", "消费金融", "第三方支付"],
            "business_lines": [
                "消费金融与信贷",
                "助贷与金融产品导流",
                "供应链金融与小微金融",
                "B2B跨境支付",
                "保险经纪",
            ],
            "main_products": ["拿去花", "信用贷"],
            "shareholders": [
                "携程旅游网络技术（上海）有限公司 58.8%",
                "银联国际有限公司 20%",
                "上海领偕商务咨询有限公司 9.6%",
                "克拉斯（北京）投资有限公司 7.2%",
                "北京中汇优通投资有限公司 4.4%",
            ],
            "legal_representative": "章婷婷",
            "registered_capital": "25,000万元人民币（已实缴）",
            "employees_2025": 122,
            "established_at": "2014-01-09",
        }
        company.company_profile = profile
        for field, value in PROFILE_PATCH.items():
            setattr(company, field, value)
        print("[更新] 公司档案：行业、地区、经营概况已补齐")

        # 2. 公司级风险事件
        added_events = 0
        for item in RISK_EVENTS:
            exists = (
                db.query(RiskEvent)
                .filter(RiskEvent.company_id == company.id)
                .filter(RiskEvent.title == item["title"])
                .first()
            )
            if exists:
                continue
            db.add(
                RiskEvent(
                    company_id=company.id,
                    category=item["category"],
                    severity=item["severity"],
                    title=item["title"],
                    content=item["content"],
                    source_url=item["source_url"],
                    source_name=item["source_name"],
                    occurred_at=_to_datetime(item["occurred_at"]),
                    sentiment=item["sentiment"],
                    extra_payload={"origin": "public_source", "seeded_at": "2026-09-24"},
                )
            )
            added_events += 1
        print(f"[新增] 公司风险事件 {added_events} 条（已有记录自动跳过）")

        # 3. 行业级事件
        added_industry = 0
        for item in INDUSTRY_EVENTS:
            dedupe_hash = _hash(item["title"], item["source_url"])
            exists = (
                db.query(MacroIndustryEvent)
                .filter(MacroIndustryEvent.dedupe_hash == dedupe_hash)
                .first()
            )
            if exists:
                continue
            db.add(
                MacroIndustryEvent(
                    scope_type="industry",
                    scope_key=item["scope_key"],
                    dimension=item["dimension"],
                    event_type=item["event_type"],
                    indicator_code="",
                    title=item["title"],
                    summary=item["summary"],
                    source_name=item["source_name"],
                    source_url=item["source_url"],
                    published_at=_to_datetime(item["published_at"]),
                    impact_direction=item["impact_direction"],
                    severity=item["severity"],
                    relevance_score=1.0,
                    dedupe_hash=dedupe_hash,
                    raw_payload={"origin": item["source_name"], "seeded_at": "2026-09-24"},
                )
            )
            added_industry += 1
        print(f"[新增] 行业事件 {added_industry} 条（已有记录自动跳过）")

        db.commit()

        total_events = (
            db.query(RiskEvent).filter(RiskEvent.company_id == company.id).count()
        )
        total_industry = db.query(MacroIndustryEvent).count()
        print(f"[完成] 公司风险事件共 {total_events} 条，行业事件共 {total_industry} 条")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
