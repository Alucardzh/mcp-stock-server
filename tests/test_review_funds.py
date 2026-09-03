from datetime import date

import pandas as pd
import pytest

from utils import review_funds as rf


def _mff_df(day="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [day],
            "上证-涨跌幅": [1.2],
            "深证-涨跌幅": [1.5],
            "主力净流入-净额": [-3.5e10],
            "主力净流入-净占比": [-2.1],
            "超大单净流入-净额": [-1.0e10],
            "超大单净流入-净占比": [-0.6],
            "大单净流入-净额": [-2.5e10],
            "大单净流入-净占比": [-1.5],
            "中单净流入-净额": [1.0e10],
            "中单净流入-净占比": [0.6],
            "小单净流入-净额": [2.5e10],
            "小单净流入-净占比": [1.5],
        }
    )


def _sector_df():
    return pd.DataFrame(
        {
            "名称": ["计算机", "银行", "白酒"],
            "今日涨跌幅": [2.0, -0.5, -1.0],
            "今日主力净流入-净额": [5.0e10, -3.0e10, -4.0e10],
            "今日主力净流入-净占比": [3.0, -2.0, -3.0],
        }
    )


def _sse_margin_df():
    return pd.DataFrame(
        {
            "信用交易日期": ["2026-09-01", "2026-09-02"],
            "融资余额": [9.0e12, 9.1e12],
            "融券余额": [5.0e10, 5.2e10],
        }
    )


def _szse_margin_df(compact):
    data = {
        "20260901": 7.0e12,   # 周二
        "20260902": 7.05e12,  # 周三
    }
    return pd.DataFrame({"日期": [compact], "融资余额": [data[compact]]})


def test_market_fund_flow_section(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df())
    out = rf.market_fund_flow_section(date(2026, 9, 2))
    assert out["main_net_yi"] == -350.0
    assert out["super_large_net_yi"] == -100.0
    assert out["large_net_yi"] == -250.0


def test_market_fund_flow_section_missing_day(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df("2026-09-01"))
    with pytest.raises(ValueError):
        rf.market_fund_flow_section(date(2026, 9, 2))


def test_sector_fund_flow_history_degrades():
    out = rf.sector_fund_flow_section(date(2026, 9, 2), "今日")
    assert out["items"] is None
    assert any("仅支持当日" in n for n in out["notes"])


def test_sector_fund_flow_today(monkeypatch):
    class FakeDT:
        @staticmethod
        def now():
            class _T:
                @staticmethod
                def date():
                    return date(2026, 9, 3)

            return _T()

    monkeypatch.setattr(rf, "datetime", FakeDT)
    monkeypatch.setattr(
        rf, "stock_sector_fund_flow_rank", lambda indicator, sector_type: _sector_df()
    )
    out = rf.sector_fund_flow_section(date(2026, 9, 3), "今日")
    assert out["top5"][0]["name"] == "计算机"
    assert out["top5"][0]["main_net_yi"] == 500.0
    assert out["bottom5"][0]["name"] == "白酒"


def test_margin_section(monkeypatch):
    monkeypatch.setattr(
        rf, "stock_margin_sse", lambda start_date, end_date: _sse_margin_df()
    )
    monkeypatch.setattr(
        rf,
        "stock_margin_szse",
        lambda date: _szse_margin_df(date) if date in ("20260901", "20260902")
        else (_ for _ in ()).throw(ValueError("no data")),
    )
    out = rf.margin_section(date(2026, 9, 2), days=5, market="沪深")
    assert out["date"] == "2026-09-02"
    #最新日: 沪 9.1e12 + 深 7.05e12；前一日: 沪 9.0e12 + 深 7.0e12
    assert out["rzye_yi"] == round((9.1e12 + 7.05e12) / 1e8, 2)
    assert out["rzye_chg_yi"] == round((0.1e12 + 0.05e12) / 1e8, 2)
    assert len(out["series"]) == 2


def test_fund_flow_section_combined(monkeypatch):
    monkeypatch.setattr(rf, "stock_market_fund_flow", lambda: _mff_df())
    out = rf.fund_flow_section(date(2026, 9, 2))
    assert out["market"]["main_net_yi"] == -350.0
    assert out["sector"]["items"] is None  # 历史日期板块降级
    assert any("[sector]" in n for n in out["notes"])
