from types import SimpleNamespace

from utils import global_common as gc


def _tencent_body():
    f = [""] * 40
    f[1] = "纳斯达克"
    f[3] = "26584.06"
    f[4] = "26217.83"
    f[32] = "1.40"
    return f'v_usIXIC="{"~".join(f)}";v_usINX="";'


def test_yahoo_proxy(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    assert gc.yahoo_proxy() == {"http": "http://p:7890", "https": "http://p:7890"}
    monkeypatch.delenv("YAHOO_PROXY")
    assert gc.yahoo_proxy() is None


def test_fetch_tencent_quotes(monkeypatch):
    fake = SimpleNamespace(
        get=lambda url, timeout: SimpleNamespace(
            status_code=200, content=_tencent_body().encode("gbk")
        )
    )
    monkeypatch.setattr(gc, "std_requests", fake)
    out = gc.fetch_tencent_quotes(["usIXIC", "usINX"])
    assert out["usIXIC"]["name"] == "纳斯达克"
    assert out["usIXIC"]["price"] == 26584.06
    assert out["usIXIC"]["prev_close"] == 26217.83
    assert out["usIXIC"]["chg_pct"] == 1.4
    assert out["usINX"] is None  # 空响应


def test_fetch_tencent_quotes_http_fail(monkeypatch):
    fake = SimpleNamespace(
        get=lambda url, timeout: SimpleNamespace(status_code=502, content=b"")
    )
    monkeypatch.setattr(gc, "std_requests", fake)
    assert gc.fetch_tencent_quotes(["usIXIC"]) == {"usIXIC": None}


def _yahoo_meta():
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "shortName": "NVIDIA Corporation",
                        "regularMarketPrice": 228.45,
                        "chartPreviousClose": 227.97,
                        "postMarketPrice": 229.0,
                    }
                }
            ],
            "error": None,
        }
    }


def test_fetch_yahoo_quote(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    fake = SimpleNamespace(
        get=lambda url, params, impersonate, timeout, proxies: SimpleNamespace(
            status_code=200, json=lambda: _yahoo_meta()
        )
    )
    monkeypatch.setattr(gc, "cr_requests", fake)
    q = gc.fetch_yahoo_quote("NVDA", include_pre_post=True)
    assert q["close"] == 228.45
    assert q["chg_pct"] == round((228.45 / 227.97 - 1) * 100, 2)
    assert q["after_hours_pct"] == round((229.0 / 228.45 - 1) * 100, 2)
    assert q["currency"] == "USD"


def test_fetch_yahoo_quote_no_proxy(monkeypatch):
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
    assert gc.fetch_yahoo_quote("NVDA") is None


def test_fetch_yahoo_quote_http_fail(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    fake = SimpleNamespace(
        get=lambda url, **kw: SimpleNamespace(status_code=403)
    )
    monkeypatch.setattr(gc, "cr_requests", fake)
    assert gc.fetch_yahoo_quote("NVDA") is None
