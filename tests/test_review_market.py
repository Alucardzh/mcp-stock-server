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


def test_indices_section_bad_cell_degrades(monkeypatch):
    """单指数坏值(成交额 None)降级为 note，不杀整个 section"""
    good = _hist_df()
    bad = _hist_df().copy()
    bad["成交额"] = [None]

    def fake(symbol, **kw):
        return bad if symbol == "899050" else good

    monkeypatch.setattr(rm, "index_zh_a_hist", fake)
    out = rm.indices_section(date(2026, 9, 2))
    assert len(out["items"]) == 4
    assert any("899050" in n for n in out["notes"])


def _zt_df():
    return pd.DataFrame(
        {
            "代码": ["003005", "601086"],
            "名称": ["竞业达", "国芳集团"],
            "涨跌幅": [10.0, 10.0],
            "最新价": [20.0, 11.1],
            "成交额": [8.78e7, 6.44e7],
            "换手率": [3.27, 0.87],
            "封板资金": [1.8e8, 3.06e8],
            "炸板次数": [0, 0],
            "涨停统计": ["4/4", "4/4"],
            "连板数": [4, 4],
            "所属行业": ["IT服务Ⅱ", "一般零售"],
        }
    )


def test_breadth_section_history(monkeypatch):
    # 历史日期：三个池有数据、涨跌家数降级
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    monkeypatch.setattr(
        rm, "stock_zt_pool_zbgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm,
        "stock_zt_pool_dtgc_em",
        lambda date: pd.DataFrame({"代码": ["605179"], "连续跌停": [1]}),
    )
    out = rm.breadth_section(date(2026, 9, 2))
    assert out["limit_up"] == 2 and out["max_lianban"] == 4
    assert out["lianban_dist"] == {"4板": 2}
    assert out["zhaban"] == 0 and out["limit_down"] == 1
    assert out["up"] is None and out["down"] is None
    assert any("仅支持当日" in n for n in out["notes"])


def test_breadth_section_today(monkeypatch):
    class FakeDT:
        @staticmethod
        def now():
            class _T:
                @staticmethod
                def date():
                    return date(2026, 9, 3)

            return _T()

    monkeypatch.setattr(rm, "datetime", FakeDT)
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    monkeypatch.setattr(
        rm, "stock_zt_pool_zbgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm, "stock_zt_pool_dtgc_em", lambda date: pd.DataFrame(columns=["代码"])
    )
    monkeypatch.setattr(
        rm,
        "stock_zh_a_spot_em",
        lambda: pd.DataFrame({"代码": ["1", "2", "3"], "涨跌幅": [1.0, -2.0, 0.0]}),
    )
    out = rm.breadth_section(date(2026, 9, 3))
    assert out["up"] == 1 and out["down"] == 1 and out["flat"] == 1


def test_breadth_section_all_empty(monkeypatch):
    empty = pd.DataFrame(columns=["代码"])
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: empty)
    monkeypatch.setattr(rm, "stock_zt_pool_zbgc_em", lambda date: empty)
    monkeypatch.setattr(rm, "stock_zt_pool_dtgc_em", lambda date: empty)
    with pytest.raises(ValueError):
        rm.breadth_section(date(2026, 9, 2))


def test_get_zt_pool(monkeypatch):
    monkeypatch.setattr(rm, "stock_zt_pool_em", lambda date: _zt_df())
    out = rm.get_zt_pool(pool="涨停", date="2026-09-02")
    assert '"success": true' in out and "竞业达" in out


def test_get_zt_pool_bad_pool():
    out = rm.get_zt_pool(pool="飞天", date="2026-09-02")
    assert '"success": false' in out
