import pandas as pd

from utils import global_markets as gm


def _tq(price=100.0, chg=1.0):
    return {"name": "x", "price": price, "prev_close": price / (1 + chg / 100), "chg_pct": chg}


def _sina_sox():
    return pd.DataFrame(
        {
            "date": ["2026-09-02", "2026-09-03"],
            "close": [11882.17, 11352.13],
            "volume": [0, 0],
        }
    )


def _em_global_df():
    return pd.DataFrame(
        {
            "名称": ["日经225", "韩国KOSPI", "美元指数", "恒生指数"],
            "最新价": [65059.06, 6709.87, 99.17, 25737.6],
            "涨跌幅": [-1.89, -1.16, 0.25, 2.08],
        }
    )


def _futures_df():
    return pd.DataFrame(
        {
            "名称": ["布伦特原油2712", "布伦特原油2801", "NYMEX原油2712", "COMEX黄金2706"],
            "最新价": [75.86, 75.38, 70.22, 4627.5],
            "涨跌幅": [0.11, 0.0, 0.05, -0.44],
            "成交量": [614, 3, 621, 9],
        }
    )


def _setup_tencent(monkeypatch, **over):
    table = {
        "usIXIC": _tq(26584.06, 1.40),
        "usINX": _tq(7747.71, 1.06),
        "usDJI": _tq(53686.11, 1.18),
        "usVIX": _tq(21.67, 0.0),
        "hkHSTECH": _tq(4580.76, 2.51),
    }
    table.update(over)
    monkeypatch.setattr(gm, "fetch_tencent_quotes", lambda codes: {c: table.get(c) for c in codes})
    gm._tencent_cache = None


def test_us_equities_section(monkeypatch):
    _setup_tencent(monkeypatch)
    monkeypatch.setattr(gm, "index_us_stock_sina", lambda symbol: _sina_sox())
    out = gm.us_equities_section()
    assert len(out["indexes"]) == 4
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["纳斯达克"]["close"] == 26584.06
    assert by_name["费城半导体"]["close"] == 11352.13
    assert by_name["费城半导体"]["chg_pct"] == round((11352.13 / 11882.17 - 1) * 100, 2)


def test_us_equities_section_tencent_fallback_yahoo(monkeypatch):
    _setup_tencent(monkeypatch, usDJI=None)
    monkeypatch.setattr(gm, "index_us_stock_sina", lambda symbol: _sina_sox())
    monkeypatch.setattr(
        gm, "fetch_yahoo_quote",
        lambda symbol, include_pre_post=False: {"close": 53686.0, "chg_pct": 1.18, "name": "道琼斯"},
    )
    out = gm.us_equities_section()
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["道琼斯"]["close"] == 53686.0  # 腾讯失败 -> Yahoo 备源成功
    assert "error" not in by_name["道琼斯"]


def test_fx_section(monkeypatch):
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    monkeypatch.setattr(
        gm, "fetch_yahoo_quote",
        lambda symbol, include_pre_post=False: (
            {"close": 6.714, "chg_pct": -0.04} if symbol == "USDCNH=X" else None
        ),
    )
    out = gm.fx_section()
    assert out["dxy"] == 99.17 and out["dxy_chg_pct"] == 0.25
    assert out["usdcnh"] == 6.714


def test_fx_section_onshore_fallback(monkeypatch):
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    monkeypatch.setattr(gm, "fetch_yahoo_quote", lambda symbol, include_pre_post=False: None)
    monkeypatch.setattr(
        gm, "fx_spot_quote",
        lambda: pd.DataFrame({"货币对": ["USD/CNY"], "买报价": [6.7163], "卖报价": [6.7164]}),
    )
    out = gm.fx_section()
    assert out["usdcnh"] == 6.7163
    assert any("在岸" in n for n in out["notes"])


def test_asia_section(monkeypatch):
    _setup_tencent(monkeypatch)
    gm._em_global_cache = None
    monkeypatch.setattr(gm, "index_global_spot_em", lambda: _em_global_df())
    out = gm.asia_section()
    by_name = {i["name"]: i for i in out["indexes"]}
    assert by_name["日经225"]["close"] == 65059.06
    assert by_name["KOSPI"]["close"] == 6709.87
    assert by_name["恒生科技"]["close"] == 4580.76


def test_commodities_section(monkeypatch):
    gm._futures_cache = None
    monkeypatch.setattr(gm, "futures_global_spot_em", lambda: _futures_df())
    out = gm.commodities_section()
    assert out["brent"]["price"] == 75.86  # 成交量最大主力
    assert out["wti"]["price"] == 70.22
    assert out["gold"]["price"] == 4627.5


def test_fear_section(monkeypatch):
    _setup_tencent(monkeypatch)
    out = gm.fear_section()
    assert out["vix"] == 21.67


def test_bond_group_section(monkeypatch):
    monkeypatch.setattr(
        gm, "us_treasury_section",
        lambda: {"date": "2026-09-03", "us10y": 4.77, "us10y_chg_bp": -1.0,
                 "us30y": 5.25, "us30y_chg_bp": -1.0, "us2y": 4.34,
                 "spread_10y_2y": 0.43, "notes": []},
    )
    monkeypatch.setattr(
        gm, "fetch_jgb_yields",
        lambda: {"date": "2026-09-03", "japan10y": 2.987, "japan10y_chg_bp": -1.9,
                 "japan20y": 3.859, "japan30y": 4.131, "notes": ["x"]},
    )
    out = gm.bond_group_section()
    assert out["us10y"] == 4.77 and out["japan10y"] == 2.987
