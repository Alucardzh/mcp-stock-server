import json

import pandas as pd

from utils import boards as bd


def _belong_df():
    return pd.DataFrame(
        {
            "股票名称": ["新华传媒"] * 5,
            "股票代码": ["600825"] * 5,
            "板块代码": ["BK0477", "BK0733", "BK0500", "BK0596", "BK0509"],
            "板块名称": ["出版", "AI语料", "上证50_", "融资融券", "上海板块"],
            "板块涨幅": [0.5, 1.2, 0.1, -0.3, 0.2],
        }
    )


def _concept_df():
    # 上海板块(BK0509)为地域板块，不在概念名单内，验证归属结果中的地域行被过滤
    return pd.DataFrame({"板块代码": ["BK0733"], "板块名称": ["AI语料"]})


def _industry_df():
    return pd.DataFrame({"板块代码": ["BK0477"], "板块名称": ["出版"]})


def test_get_stock_boards(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: _belong_df())
    monkeypatch.setattr(bd, "stock_board_concept_name_em", _concept_df)
    monkeypatch.setattr(bd, "stock_board_industry_name_em", _industry_df)
    bd._board_cache.update(date="", concept=set(), industry={})
    out = json.loads(bd.get_stock_boards("600825", with_raw=True))
    assert out["success"]
    d = out["data"]
    assert d["industry"] == "出版"
    assert d["concepts"] == ["AI语料"]
    assert len(d["raw"]) == 5


def test_get_stock_boards_cache_reuse(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: _belong_df())
    calls = []
    monkeypatch.setattr(bd, "stock_board_concept_name_em", lambda: calls.append(1) or _concept_df())
    monkeypatch.setattr(bd, "stock_board_industry_name_em", _industry_df)
    bd._board_cache.update(date="", concept=set(), industry={})
    json.loads(bd.get_stock_boards("600825"))
    json.loads(bd.get_stock_boards("600825"))
    assert len(calls) == 1  # 名单当日缓存，第二次不再拉


def test_get_stock_boards_empty(monkeypatch):
    monkeypatch.setattr(bd, "ef_belong_board", lambda s: pd.DataFrame())
    out = json.loads(bd.get_stock_boards("600825"))
    assert not out["success"]
