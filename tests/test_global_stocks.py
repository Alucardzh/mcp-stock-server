import json

from utils import global_stocks as gs


def test_presets():
    assert "NVDA" in gs.PRESETS["ai_chain"] and "2330.TW" in gs.PRESETS["ai_chain"]
    assert "000660.KS" in gs.PRESETS["storage"] and "285A.T" in gs.PRESETS["storage"]
    assert set(gs.PRESETS["etf"]) == {"SMH", "AIQ", "BOTZ"}
    assert "SMH" in gs.PRESETS["all"]


def _tq(price, name="x"):
    return {"name": name, "price": price, "prev_close": price, "chg_pct": 1.0}


def test_tencent_primary(monkeypatch):
    gs._result_cache = {}
    monkeypatch.setattr(
        gs, "fetch_tencent_quotes",
        lambda codes: {
            "usNVDA": _tq(230.36, "英伟达"),
            "kr000660": _tq(1664000.0, "SK海力士"),
        },
    )
    yahoo_calls = []
    monkeypatch.setattr(
        gs, "fetch_yahoo_quote",
        lambda sym, include_pre_post=False: yahoo_calls.append(sym) or None,
    )
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,000660.KS"))
    assert out["success"] is True
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["close"] == 230.36 and q["NVDA"]["name"] == "英伟达"
    assert q["NVDA"]["currency"] == "USD" and q["000660.KS"]["currency"] == "KRW"
    assert yahoo_calls == []  # 主源命中, 不走 Yahoo


def test_yahoo_fallback_for_tw_and_miss(monkeypatch):
    gs._result_cache = {}
    monkeypatch.setattr(
        gs, "fetch_tencent_quotes",
        lambda codes: {"usNVDA": _tq(230.36, "英伟达")},  # usMU 缺失
    )
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")

    def fake_yahoo(sym, include_pre_post=False):
        if sym == "2330.TW":
            return {"symbol": sym, "name": "TSMC", "currency": "TWD",
                    "close": 2400.0, "chg_pct": 0.5}
        if sym == "MU":
            return {"symbol": sym, "name": "Micron", "currency": "USD",
                    "close": 958.16, "chg_pct": 6.1, "after_hours_pct": 0.3}
        return None

    monkeypatch.setattr(gs, "fetch_yahoo_quote", fake_yahoo)
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,MU,2330.TW"))
    assert out["success"] is True
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["close"] == 230.36
    assert q["MU"]["close"] == 958.16 and q["MU"]["after_hours_pct"] == 0.3
    assert q["2330.TW"]["close"] == 2400.0 and q["2330.TW"]["currency"] == "TWD"


def test_no_proxy_tencent_still_works(monkeypatch):
    gs._result_cache = {}
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
    monkeypatch.setattr(
        gs, "fetch_tencent_quotes",
        lambda codes: {"usNVDA": _tq(230.36, "英伟达")},
    )
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,2330.TW"))
    assert out["success"] is True
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["close"] == 230.36  # 腾讯主源不依赖代理
    assert q["2330.TW"]["close"] is None and "error" in q["2330.TW"]
    assert any("YAHOO_PROXY" in n for n in out["data"]["notes"])


def test_result_cache(monkeypatch):
    gs._result_cache = {}
    calls = {"n": 0}

    def fake_tencent(codes):
        calls["n"] += 1
        return {"usNVDA": _tq(230.36, "英伟达")}

    monkeypatch.setattr(gs, "fetch_tencent_quotes", fake_tencent)
    gs.get_stock_global_snapshot(symbols="NVDA")
    gs.get_stock_global_snapshot(symbols="NVDA")
    assert calls["n"] == 1
