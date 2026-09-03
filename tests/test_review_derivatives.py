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


def _rank_df():
    return pd.DataFrame(
        {
            "rank": [1, 2],
            "vol_party_name": ["中信期货(代客)", "国泰君安(代客)"],
            "vol": [26865, 23501],
            "vol_chg": [2688, 5715],
            "long_party_name": ["国泰君安(代客)", "中信期货(代客)"],
            "long_open_interest": [22066, 18937],
            "long_open_interest_chg": [-789, 1432],
            "short_party_name": ["中信期货(代客)", "华泰期货(代客)"],
            "short_open_interest": [24419, 16152],
            "short_open_interest_chg": [905, -574],
            "symbol": ["IF2609", "IF2609"],
            "variety": ["IF", "IF"],
        }
    )


def test_summarize_members():
    rows = rd._summarize_members(_rank_df())
    by_name = {r["member"]: r for r in rows}
    citic = by_name["中信期货(代客)"]
    # 中信: 多18937(+1432) 空24419(+905) -> net -5482, net_chg +527
    assert citic["net"] == 18937 - 24419
    assert citic["net_chg"] == 1432 - 905
    gtja = by_name["国泰君安(代客)"]
    assert gtja["net"] == 22066 and gtja["net_chg"] == -789
    # 排序不变量：按 net_chg 降序
    chgs = [r["net_chg"] for r in rows]
    assert chgs == sorted(chgs, reverse=True)


class _FakeResp:
    def __init__(self, content, status=200):
        self.content = content
        self.status_code = status


_CSV_LINES = [
    "交易日,合约系列,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,",
    ",,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减",
    "20260902,IO2609,1,中信期货(代客),26143,4357,中信期货(代客),14934,1298,中信期货(代客),15053,1733",
    "20260902,IO2609,2,华泰期货(代客),21702,5211,国泰君安(代客),12084,272,国泰君安(代客),12452,131",
]


def test_fetch_option_rank_csv(monkeypatch):
    monkeypatch.setattr(
        rd.requests,
        "get",
        lambda url, timeout: _FakeResp("\n".join(_CSV_LINES).encode("gbk")),
    )
    df = rd.fetch_option_rank_csv("IO", date(2026, 9, 2))
    assert df is not None and len(df) == 2
    assert df.iloc[0]["vol_party_name"] == "中信期货(代客)"
    assert int(df.iloc[0]["long_open_interest"]) == 14934
    assert int(df.iloc[0]["short_open_interest"]) == 15053


def test_fetch_option_rank_csv_404(monkeypatch):
    monkeypatch.setattr(
        rd.requests, "get", lambda url, timeout: _FakeResp(b"", status=404)
    )
    assert rd.fetch_option_rank_csv("IO", date(2026, 9, 2)) is None


def test_get_cffex_rank_futures(monkeypatch):
    monkeypatch.setattr(
        rd,
        "get_cffex_rank_table",
        lambda date, vars_list: {"IF2609": _rank_df()},
    )
    out = rd.get_cffex_rank(var="IF", date="2026-09-02", member="中信")
    assert '"success": true' in out and "IF2609" in out
    data = __import__("json").loads(out)["data"]
    assert any("中信" in s["member"] for s in data["member_summary"])


def test_get_cffex_rank_option(monkeypatch):
    monkeypatch.setattr(
        rd.requests,
        "get",
        lambda url, timeout: _FakeResp("\n".join(_CSV_LINES).encode("gbk")),
    )
    out = rd.get_cffex_rank(var="IO", date="2026-09-02", member="")
    assert '"success": true' in out
    data = __import__("json").loads(out)["data"]
    assert "IO2609" in data["contracts"]


def test_get_cffex_rank_bad_var():
    out = rd.get_cffex_rank(var="XX", date="2026-09-02")
    assert '"success": false' in out


def test_derivatives_section(monkeypatch):
    monkeypatch.setattr(rd, "get_cffex_daily", lambda date: _cffex_df())
    monkeypatch.setattr(rd, "stock_zh_index_daily", lambda symbol: _index_df())
    monkeypatch.setattr(rd, "option_daily_stats_sse", lambda date: pd.DataFrame())
    monkeypatch.setattr(rd, "option_daily_stats_szse", lambda date: pd.DataFrame())
    monkeypatch.setattr(
        rd, "get_cffex_rank_table", lambda date, vars_list: {"IF2609": _rank_df()}
    )
    monkeypatch.setattr(
        rd.requests, "get", lambda url, timeout: _FakeResp(b"", status=404)
    )
    out = rd.derivatives_section(date(2026, 9, 2))
    assert out["basis"] is not None and out["seats"]["IF"] is not None
    assert out["seats"]["IO"] is None  # CSV 404 -> 降级
