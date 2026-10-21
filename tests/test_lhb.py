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


def _seats_df(flag):
    amt_col = "买入金额" if flag == "买入" else "卖出金额"
    return pd.DataFrame(
        {
            "营业部名称": ["机构专用", "沪股通专用", "中信证券上海分公司", "华鑫上海分公司", "东财拉萨团结路"],
            amt_col: [5.0e7, 3.0e7, 2.0e7, 1.0e7, 0.5e7],
        }
    )


def test_get_lhb_seats(monkeypatch):
    monkeypatch.setattr(
        lhb, "stock_lhb_stock_detail_em", lambda symbol, date, flag: _seats_df(flag)
    )
    out = json.loads(lhb.get_lhb_seats("600825", "2026-09-02"))
    assert out["success"]
    d = out["data"]
    assert d["buy"][0]["seat"].startswith("[机构]")
    assert d["buy"][0]["amount_wan"] == 5000.0
    assert d["buy"][1]["seat"].startswith("[外资]")
    assert d["sell"][0]["amount_wan"] == 5000.0


def test_get_lhb_seats_not_on_board(monkeypatch):
    monkeypatch.setattr(
        lhb, "stock_lhb_stock_detail_em", lambda symbol, date, flag: pd.DataFrame()
    )
    out = json.loads(lhb.get_lhb_seats("600825", "2026-09-02"))
    assert not out["success"]
    assert "可能未上榜" in out["error"]


def test_get_lhb_seats_invalid_symbol(monkeypatch):
    """非法代码在进入数据源前即被校验拦截。"""
    from utils import validate

    monkeypatch.setattr(validate, "get_name_code_mapping", lambda: {})
    out = json.loads(lhb.get_lhb_seats("600825x", "2026-09-02"))
    assert not out["success"]
    assert "查询席位明细失败" in out["error"]
    assert "Invalid stock symbol" in out["error"]


def test_get_lhb_seats_both_sides_raise_diagnosed(monkeypatch):
    """买卖两侧都拉取异常时应报拉取失败，而非误诊为未上榜。"""
    def _raise(symbol, date, flag):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(lhb, "stock_lhb_stock_detail_em", _raise)
    out = json.loads(lhb.get_lhb_seats("600825", "2026-09-02"))
    assert not out["success"]
    assert "拉取失败" in out["error"]
    assert "可能未上榜" not in out["error"]
