"""get_market_index 重写单测（需求v3 N4）：index_zh_a_hist 通道 + 任意指数代码。"""

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from utils import tools as t


def _idx_df(n=2):
    today = datetime.now().strftime("%Y-%m-%d")
    prev = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    days = [prev, today] if n >= 2 else [today]
    return pd.DataFrame(
        {
            "日期": days[-n:],
            "开盘": [10500.0] * len(days[-n:]),
            "收盘": [10600.0, 10638.52][-len(days[-n:]) :] if n >= 2 else [10638.52],
            "最高": [10650.0] * len(days[-n:]),
            "最低": [10480.0] * len(days[-n:]),
            "成交量": [1.2e8] * len(days[-n:]),
            "成交额": [7.6e11, 7.7e11][-len(days[-n:]) :] if n >= 2 else [7.7e11],
            "振幅": [1.6] * len(days[-n:]),
            "涨跌幅": [0.36, 0.36][-len(days[-n:]) :] if n >= 2 else [0.36],
            "涨跌额": [38.0] * len(days[-n:]),
            "换手率": [1.0] * len(days[-n:]),
        }
    )


@pytest.fixture(autouse=True)
def _mock_hist(monkeypatch):
    monkeypatch.setattr(t, "index_zh_a_hist", lambda symbol, **kw: _idx_df())


def test_market_index_by_code():
    out = json.loads(t.get_market_index("399001"))  # N4：深证成指可查
    assert out["success"]
    d = out["data"]
    assert d["code"] == "399001" and d["name"] == "深证成指"
    assert d["current_point"] == 10638.52 and d["prev_close"] == 10600.0
    assert d["open"] == 10500.0 and d["high"] == 10650.0 and d["low"] == 10480.0
    assert d["source"] == "akshare_index_hist"


def test_market_index_invalid_code():
    out = json.loads(t.get_market_index("3990"))
    assert out["success"] is False and "指数代码格式非法" in out["error"]


def test_market_index_no_data(monkeypatch):
    monkeypatch.setattr(t, "index_zh_a_hist", lambda symbol, **kw: pd.DataFrame())
    out = json.loads(t.get_market_index("999999"))
    assert out["success"] is False and "999999" in out["error"]
