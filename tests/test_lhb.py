import json

import pandas as pd

from utils import lhb


def _bill_df():
    return pd.DataFrame(
        {
            "股票代码": ["000788", "000788", "600825"],
            "股票名称": ["北新建材", "北新建材", "新华传媒"],
            "解读": ["机构买入", "机构买入", "游资卖出"],
            "龙虎榜净买额": [1.0e8, 0.5e8, -2.0e8],
            "龙虎榜买入额": [1.5e8, 0.6e8, 0.5e8],
            "龙虎榜卖出额": [0.5e8, 0.1e8, 2.5e8],
            "上榜原因": ["日涨幅偏离值达到7%", "日换手率达到20%", "日跌幅偏离值达到7%"],
        }
    )


def test_get_lhb_aggregates(monkeypatch):
    monkeypatch.setattr(lhb, "ef_daily_billboard", lambda s, e: _bill_df())
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert out["success"]
    d = out["data"]
    assert d["count"] == 2
    item = next(i for i in d["items"] if i["code"] == "000788")
    assert item["net"] == 1.5
    assert item["buy"] == 2.1
    assert len(item["reasons"]) == 2
    assert d["net_sell_top5"][0]["code"] == "600825"


def test_get_lhb_fallback_to_akshare(monkeypatch):
    def _raise(s, e):
        raise RuntimeError("ef down")

    monkeypatch.setattr(lhb, "ef_daily_billboard", _raise)
    ak_df = _bill_df().rename(
        columns={
            "股票代码": "SECURITY_CODE",
            "股票名称": "SECURITY_NAME_ABBR",
            "解读": "EXPLAIN",
            "龙虎榜净买额": "BILLBOARD_NET_AMT",
            "龙虎榜买入额": "BILLBOARD_BUY_AMT",
            "龙虎榜卖出额": "BILLBOARD_SELL_AMT",
            "上榜原因": "EXPLANATION",
        }
    )
    monkeypatch.setattr(lhb, "stock_lhb_detail_em", lambda start_date, end_date: ak_df)
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert out["success"]
    assert out["data"]["count"] == 2


def test_get_lhb_no_data(monkeypatch):
    monkeypatch.setattr(lhb, "ef_daily_billboard", lambda s, e: pd.DataFrame())
    monkeypatch.setattr(lhb, "stock_lhb_detail_em", lambda start_date, end_date: pd.DataFrame())
    out = json.loads(lhb.get_lhb("2026-09-02"))
    assert not out["success"]
