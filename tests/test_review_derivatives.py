from datetime import date

import pandas as pd
import pytest

from utils import review_derivatives as rd


def _cffex_df(day="20260902"):
    # IF2609 成交量大于 IF2612 -> 主力为 IF2609
    return pd.DataFrame(
        {
            "symbol": ["IF2609", "IF2612"],
            "date": [day, day],
            "open": [4566.2, 4550.0],
            "high": [4567.0, 4560.0],
            "low": [4497.6, 4510.0],
            "close": [4532.2, 4520.0],
            "volume": [71487, 20000],
            "open_interest": [139367, 50000],
            "turnover": [3.2e11, 9.0e10],
            "settle": [4530.0, 4515.0],
            "pre_settle": [4590.0, 4555.0],
            "variety": ["IF", "IF"],
        }
    )


def _index_df(day="2026-09-02"):
    return pd.DataFrame(
        {"date": [day], "open": [4618.7], "high": [4640.1],
         "low": [4604.8], "close": [4611.4], "volume": [2.1e10]}
    )


def test_contract_expiry():
    assert rd._contract_expiry("IF2609") == date(2026, 9, 18)  # 9月第三个周五
    assert rd._contract_expiry("IM2612") == date(2026, 12, 18)


def test_basis_section(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    out = rd.basis_section(date(2026, 9, 2))
    assert len(out["items"]) == 1  # 只有 IF 一个品种
    item = out["items"][0]
    assert item["contract"] == "IF2609"
    assert item["settle"] == 4530.0 and item["spot_close"] == 4611.4
    assert item["basis"] == round(4611.4 - 4530.0, 2)  # 现货-期货
    # 年化: basis/spot*365/16天*100（IF2609 到期 2026-09-18，距 09-02 为 16 天）
    assert item["basis_ann_pct"] == round(
        (4611.4 - 4530.0) / 4611.4 * 365 / 16 * 100, 2
    )


def test_pcr_section(monkeypatch):
    sse = pd.DataFrame(
        {
            "合约标的代码": ["510050", "510300"],
            "合约标的名称": ["上证50ETF华夏", "沪深300ETF华泰柏瑞"],
            "合约数量": [92, 96],
            "总成交额": [25156, 43614],
            "总成交量": [627474, 651847],
            "认购成交量": [335528, 348787],
            "认沽成交量": [291946, 303060],
            "认沽/认购": [87.01, 86.89],
            "未平仓合约总数": [1182953, 1123354],
            "未平仓认购合约数": [691541, 628365],
            "未平仓认沽合约数": [491412, 494989],
            "交易日": ["2026-09-02", "2026-09-02"],
        }
    )
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: sse)
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    out = rd.pcr_section(date(2026, 9, 2))
    assert len(out["exchanges"]) == 1
    ex = out["exchanges"][0]
    assert ex["exchange"] == "SSE"
    assert ex["vol_pcr"] == round((291946 + 303060) / (335528 + 348787) * 100, 2)
    assert ex["oi_pcr"] == round((491412 + 494989) / (691541 + 628365) * 100, 2)
    assert len(ex["underlyings"]) == 2


def test_pcr_section_no_data(monkeypatch):
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: pd.DataFrame())
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    with pytest.raises(ValueError):
        rd.pcr_section(date(2026, 9, 2))


def test_get_index_derivatives(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    sse = pd.DataFrame(
        {
            "合约标的代码": ["510050"],
            "合约标的名称": ["上证50ETF华夏"],
            "合约数量": [92],
            "总成交额": [25156],
            "总成交量": [627474],
            "认购成交量": [335528],
            "认沽成交量": [291946],
            "认沽/认购": [87.01],
            "未平仓合约总数": [1182953],
            "未平仓认购合约数": [691541],
            "未平仓认沽合约数": [491412],
            "交易日": ["2026-09-02"],
        }
    )
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: sse)
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    out = rd.get_index_derivatives(date="2026-09-02")
    assert '"success": true' in out and '"basis"' in out and '"pcr"' in out
