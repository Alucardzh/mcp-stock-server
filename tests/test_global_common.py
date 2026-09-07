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
    monkeypatch.delenv("YAHOO_PROXY", raising=False)
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


def test_fetch_yahoo_quote_429_cooldown(monkeypatch):
    monkeypatch.setenv("YAHOO_PROXY", "http://p:7890")
    gc._yahoo_429_until = 0.0
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        return SimpleNamespace(status_code=429, headers={"Retry-After": "120"})

    monkeypatch.setattr(gc, "cr_requests", SimpleNamespace(get=fake_get))
    assert gc.fetch_yahoo_quote("NVDA") is None
    assert gc._yahoo_429_until > 0  # 进入冷却
    assert gc.fetch_yahoo_quote("NVDA") is None
    assert calls["n"] == 1  # 冷却期内不再发请求
    gc._yahoo_429_until = 0.0  # 清理, 不影响其他测试


from datetime import date

MOF_TEXT = "\n".join(
    [
        "Interest Rate (September 2026),,,,,,,,,,,,,,,(Unit : %)",
        "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y",
        "2026/9/2,1.56,1.854,2.009,2.199,2.332,2.45,2.585,2.743,2.874,3.006,3.554,3.864,4.141,4.122,4.134",
        "2026/9/3,1.563,1.85,1.994,2.14,2.28,2.411,2.559,2.718,2.848,2.987,3.544,3.859,4.143,4.131,4.145",
        "※If you cannot download the latest csv data, please clear the browser's cache and download again.,",
    ]
)


def test_parse_jgb_csv():
    df = gc._parse_jgb_csv(MOF_TEXT)
    assert len(df) == 2  # 注释行/表头行被过滤
    assert str(df["date"].iloc[-1]) == "2026-09-03"
    assert df["10Y"].iloc[-1] == 2.987
    assert df["40Y"].iloc[-1] == 4.145


def test_fetch_jgb_yields_direct(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", tmp_path / "global")
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: MOF_TEXT)
    gc._monthly_cache = None
    out = gc.fetch_jgb_yields()
    assert out["date"] == "2026-09-03"
    assert out["japan10y"] == 2.987
    assert out["japan10y_chg_bp"] == round((2.987 - 3.006) * 100, 1)
    assert out["japan20y"] == 3.859 and out["japan30y"] == 4.131


def test_fetch_jgb_yields_fallback_local(monkeypatch, tmp_path):
    d = tmp_path / "global"
    d.mkdir()
    (d / "jgbcme_all.csv").write_text(MOF_TEXT, encoding="cp932")
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", d)
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    gc._monthly_cache = None
    out = gc.fetch_jgb_yields()
    assert out["japan10y"] == 2.987
    assert any("本地缓存" in n for n in out["notes"])


def test_fetch_jgb_yields_all_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", tmp_path / "empty")
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    monkeypatch.setattr(gc, "_load_jgb_local", lambda: None)
    gc._monthly_cache = None
    try:
        gc.fetch_jgb_yields()
        assert False
    except ValueError:
        pass


def test_jgb_yield_on_from_local_history(monkeypatch, tmp_path):
    d = tmp_path / "global"
    d.mkdir()
    (d / "jgbcme_all.csv").write_text(MOF_TEXT, encoding="cp932")
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", d)
    monkeypatch.setattr(gc, "_mof_get_text", lambda url, use_proxy=False: None)
    gc._monthly_cache = None
    out = gc.jgb_yield_on(date(2026, 9, 2))
    assert out == {"date": "2026-09-02", "y10": 3.006, "y20": 3.864, "y30": 4.122}
    assert gc.jgb_yield_on(date(2026, 8, 31)) is None


def test_load_jgb_local_rejects_non_csv(monkeypatch, tmp_path):
    """非 CSV 的 200 响应(如代理错误页)不得落盘污染本地缓存"""
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", tmp_path / "global")
    monkeypatch.setattr(
        gc, "_mof_get_text",
        lambda url, use_proxy=False: "<html>gateway error page 502</html>\n" * 10,
    )
    assert gc._load_jgb_local() is None
    assert not (tmp_path / "global" / "jgbcme_all.csv").exists()


def test_jgb_yield_on_refreshes_stale_local(monkeypatch, tmp_path):
    """本地全历史落后于目标日(月边界)时, 一次性刷新后重试"""
    d = tmp_path / "global"
    d.mkdir()
    stale = "\n".join(
        [
            "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y",
            "2026/8/31,1.5,1.7,1.9,2.0,2.1,2.2,2.3,2.4,2.5,2.9,3.5,3.8,4.1,4.09,4.09",
        ]
    )
    (d / "jgbcme_all.csv").write_text(stale, encoding="cp932")
    monkeypatch.setattr(gc, "MOF_CACHE_DIR", d)
    monkeypatch.setattr(
        gc, "_mof_get_text",
        lambda url, use_proxy=False: None if "jgbcme.csv" in url else MOF_TEXT,
    )
    gc._monthly_cache = None
    out = gc.jgb_yield_on(date(2026, 9, 2))  # 9/2 在冻结文件之后、当月文件不可用
    assert out == {"date": "2026-09-02", "y10": 3.006, "y20": 3.864, "y30": 4.122}
    # 本地缓存已被刷新覆盖
    assert "2026/9/3" in (d / "jgbcme_all.csv").read_text(encoding="cp932")
    # 早于本地最后日期且不存在的日期仍返回 None(不触发刷新)
    assert gc.jgb_yield_on(date(2026, 8, 15)) is None
