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
