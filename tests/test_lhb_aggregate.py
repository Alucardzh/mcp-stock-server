"""LHB 聚合单测：榜净并集口径 / 席位指纹去重 / 三分类净额 / diff 参考值。"""

from datetime import date

import pandas as pd
import pytest

from utils import lhb_aggregate as la


@pytest.fixture(autouse=True)
def _clean():
    la.CACHE.clear()
    yield
    la.CACHE.clear()


def _billboard_df():
    # 002384 两条上榜记录（多理由）：买 max=8.0e8、卖 max=2.0e8 → net=6.0e8
    return pd.DataFrame(
        {
            "股票代码": ["002384", "002384", "601595"],
            "股票名称": ["东山精密", "东山精密", "沪电股份"],
            "龙虎榜净买额": [5.0e8, 1.0e8, -3.0e8],
            "龙虎榜买入额": [8.0e8, 3.0e8, 1.0e8],
            "龙虎榜卖出额": [2.0e8, 5.0e8, 4.0e8],
            "解读": ["", "", ""],
            "上榜原因": ["日涨幅偏离", "连续三日", "日跌幅偏离"],
        }
    )


def _seat_df(flag):
    rows = {
        "买入": [
            ("机构专用", 4.70e8),
            ("沪股通专用", 1.50e8),
            ("知名游资A", 1.00e8),
            ("机构专用", 4.70e8),  # 与第1行完全相同 → 指纹去重剔除
        ],
        "卖出": [
            ("机构专用", 0.00e8),
            ("深股通专用", 0.00e8),
        ],
    }[flag]
    col = "买入金额" if flag == "买入" else "卖出金额"
    return pd.DataFrame({"营业部名称": [r[0] for r in rows], col: [r[1] for r in rows]})


def test_lhb_aggregate_union_and_fingerprint(monkeypatch):
    monkeypatch.setattr(la, "_fetch_billboard", lambda day: _billboard_df())

    def fake_detail(symbol, date, flag):
        assert symbol == "002384"
        return _seat_df(flag)

    monkeypatch.setattr(la, "stock_lhb_stock_detail_em", fake_detail)
    out = la.lhb_aggregate("2026-11-11", symbols=["002384"])
    s = out["stocks"]["002384"]
    # 榜净并集口径：max(买)-max(卖) = (8.0-2.0)e8 = 6.0e8 = 60000万
    assert s["net"] == 60000.0
    assert s["buy"] == 80000.0 and s["sell"] == 20000.0
    # 指纹去重后：机构 = (4.70-0.00)e8 = 47000万；外资 = 沪股通1.50e8 + 深股通0 = 15000万；
    # 游资 = 1.00e8 = 10000万
    assert s["inst"] == 47000.0 and s["foreign"] == 15000.0 and s["hot"] == 10000.0
    # 去重后买入侧3行 + 卖出侧2行；seat_net = (4.7+1.5+1.0) - (0+0) = 7.2e8 = 72000万
    assert s["seat_n"] == 5 and s["seat_net"] == 72000.0
    assert s["diff"] == 60000.0 - 72000.0  # 参考值
    assert out["total_net"] == 60000.0 + (
        -30000.0
    )  # 601595: max(1e8)-max(4e8)=-3e8=-30000万


def test_lhb_aggregate_no_data(monkeypatch):
    monkeypatch.setattr(la, "_fetch_billboard", lambda day: None)
    out = la.lhb_aggregate("2026-11-16")  # 周日
    assert out["stocks"] == {} and out["total_net"] is None
    assert any("无龙虎榜" in n for n in out["notes"])


def test_seat_fetch_per_code_called(monkeypatch):
    # 3 只上榜票 → _seat_rows 每只恰好调用一次（并发池不重不漏）
    monkeypatch.setattr(
        la,
        "_fetch_billboard",
        lambda day: pd.DataFrame(
            {
                "股票代码": ["002384", "601595", "300750"],
                "股票名称": ["东山精密", "沪电股份", "宁德时代"],
                "龙虎榜净买额": [5.0e8, -3.0e8, 2.0e8],
                "龙虎榜买入额": [8.0e8, 1.0e8, 4.0e8],
                "龙虎榜卖出额": [2.0e8, 4.0e8, 2.0e8],
                "解读": ["", "", ""],
                "上榜原因": ["日涨幅偏离", "日跌幅偏离", "日涨幅偏离"],
            }
        ),
    )
    called: list[str] = []

    def fake_seat_rows(code, d_compact):
        called.append(code)
        return [("机构专用", 100.0, None)]

    monkeypatch.setattr(la, "_seat_rows", fake_seat_rows)
    out = la.lhb_aggregate("2026-11-11")
    assert sorted(called) == ["002384", "300750", "601595"]  # 每码恰好一次
    assert out["stocks"].keys() == {"002384", "601595", "300750"}
    for code in ("002384", "601595", "300750"):
        s = out["stocks"][code]
        assert "inst" in s and "seat_n" in s  # 席位字段齐全
        assert s["inst"] == 100.0 and s["seat_n"] == 1
