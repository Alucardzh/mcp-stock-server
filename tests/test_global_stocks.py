import json

from utils import global_stocks as gs


def test_presets():
    assert "NVDA" in gs.PRESETS["ai_chain"] and "2330.TW" in gs.PRESETS["ai_chain"]
    assert "000660.KS" in gs.PRESETS["storage"] and "285A.T" in gs.PRESETS["storage"]
    assert set(gs.PRESETS["etf"]) == {"SMH", "AIQ", "BOTZ"}
    assert "SMH" in gs.PRESETS["all"]


def test_get_stock_global_snapshot(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")

    def fake(symbol, include_pre_post=False):
        if symbol == "NVDA":
            return {
                "symbol": "NVDA", "name": "NVIDIA", "currency": "USD",
                "close": 228.45, "chg_pct": 0.21,
                "after_hours_pct": 0.3,
            }
        if symbol == "000660.KS":
            return {
                "symbol": "000660.KS", "name": "SK hynix", "currency": "KRW",
                "close": 1664000.0, "chg_pct": 0.67,
            }
        return None

    monkeypatch.setattr(gs, "fetch_yahoo_quote", fake)
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,000660.KS"))
    assert out["success"] is True
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["after_hours_pct"] == 0.3
    assert q["000660.KS"]["market"] == "KR"
    assert q["NVDA"]["market"] == "US"


def test_get_stock_global_snapshot_partial_fail(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")

    def fake(symbol, include_pre_post=False):
        return {"symbol": symbol, "name": symbol, "currency": "USD",
                "close": 1.0, "chg_pct": 0.0} if symbol == "NVDA" else None

    monkeypatch.setattr(gs, "fetch_yahoo_quote", fake)
    out = json.loads(gs.get_stock_global_snapshot(symbols="NVDA,SMH"))
    q = {i["symbol"]: i for i in out["data"]["quotes"]}
    assert q["NVDA"]["close"] == 1.0
    assert q["SMH"]["close"] is None and "error" in q["SMH"]


def test_get_stock_global_snapshot_no_proxy(monkeypatch):
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
    out = json.loads(gs.get_stock_global_snapshot())
    assert out["success"] is False
    assert "YAHOO_PROXY" in out["error"]
