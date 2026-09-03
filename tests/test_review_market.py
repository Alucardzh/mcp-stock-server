from datetime import date

import pandas as pd
import pytest

from utils import review_market as rm


def _hist_df(day="2026-09-02"):
    return pd.DataFrame(
        {
            "日期": [day],
            "开盘": [3000.0],
            "收盘": [3050.0],
            "最高": [3060.0],
            "最低": [2990.0],
            "成交量": [1.5e9],
            "成交额": [3.5e11],
            "振幅": [2.3],
            "涨跌幅": [1.67],
            "涨跌额": [50.0],
            "换手率": [1.0],
        }
    )


def test_indices_section(monkeypatch):
    monkeypatch.setattr(rm, "index_zh_a_hist", lambda symbol, **kw: _hist_df())
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 5
    first = out["items"][0]
    assert first["code"] == "000001" and first["name"] == "上证指数"
    assert first["close"] == 3050.0 and first["chg_pct"] == 1.67
    assert first["amount_yi"] == 3500.0


def test_indices_section_mixed(monkeypatch):
    """2 个指数有数据、3 个缺失：返回成功的+notes"""

    def fake(symbol, **kw):
        return _hist_df("2026-09-02") if symbol in ("000001", "399001") else _hist_df("2026-09-01")

    monkeypatch.setattr(rm, "index_zh_a_hist", fake)
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 2
    assert len(out["notes"]) == 3


def test_indices_section_all_missing(monkeypatch):
    monkeypatch.setattr(rm, "index_zh_a_hist", lambda symbol, **kw: _hist_df("2026-09-01"))
    with pytest.raises(ValueError):
        rm.indices_section(date(2026, 9, 2))
