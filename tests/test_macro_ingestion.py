from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.base import Base
from app.models import Company, MacroIndicatorPoint, MacroIndustryEvent, MacroRefreshRun
from app.services import macro_ingestion


def test_macro_refresh_populates_metrics_and_industry(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    company = Company(
        name="比亚迪",
        company_profile={"akshare_profile": {"stock_code": "002594"}},
    )
    db.add(company)
    db.flush()
    run = MacroRefreshRun(company_id=company.id, progress_total=10)
    db.add(run)
    db.commit()

    fake_akshare = SimpleNamespace(
        macro_china_gdp=lambda: pd.DataFrame(
            [{"季度": "2026年第2季度", "国内生产总值-同比增长": 5.1}]
        ),
        macro_china_cpi=lambda: pd.DataFrame(
            [{"月份": "2026年08月份", "全国-同比增长": 0.7}]
        ),
        macro_china_ppi=lambda: pd.DataFrame(
            [{"月份": "2026年08月份", "当月同比增长": -1.2}]
        ),
        macro_china_pmi=lambda: pd.DataFrame(
            [{"月份": "2026年08月份", "制造业-指数": 50.2, "非制造业-指数": 50.5}]
        ),
        macro_china_lpr=lambda: pd.DataFrame(
            [{"TRADE_DATE": "2026-08-20", "LPR1Y": 3.0}]
        ),
        macro_china_money_supply=lambda: pd.DataFrame(
            [{"月份": "2026年08月份", "货币和准货币(M2)-同比增长": 8.8}]
        ),
        stock_profile_cninfo=lambda symbol: pd.DataFrame(
            [{"公司名称": "比亚迪股份有限公司", "所属行业": "汽车制造业", "注册地址": "广东省深圳市"}]
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr(
        macro_ingestion,
        "_collect_ndrc_policy",
        lambda db, industry: (0, 0, {"source": "ndrc_policy", "status": "success", "events": 0}),
    )

    completed = macro_ingestion.refresh_macro_data(db, run.id)
    assert completed.status == "completed"
    assert db.get(Company, company.id).industry == "汽车制造业"
    assert db.scalar(select(func.count(MacroIndicatorPoint.id))) == 7
    assert db.scalar(select(func.count(MacroIndustryEvent.id))) >= 7
    assert {
        item.source_results[0]["source"]
        for item in [completed]
    } == {"nbs_macro"}
