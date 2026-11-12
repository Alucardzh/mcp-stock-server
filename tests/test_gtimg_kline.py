"""gtimg 日K单测：URL 构造 / 响应解析 / 不复权键名 / 缓存（全 mock，无网络）。"""

import json

import pandas as pd
import pytest

from utils import gtimg_kline as gk
from utils.push2_client import EmResult


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    gk.CACHE.clear()
    yield
    gk.CACHE.clear()


def _resp(payload):
    class R:
        def json(self):
            return payload

        def raise_for_status(self):
            return None

    return R()


def test_mkt_prefix():
    assert gk.MKT_PREFIX("600000") == "sh600000"
    assert gk.MKT_PREFIX("000001") == "sz000001"
    assert gk.MKT_PREFIX("300750") == "sz300750"
    assert gk.MKT_PREFIX("688981") == "sh688981"


def test_fetch_kline_parses_unadjusted(monkeypatch):
    calls = {}

    def fake_get(url, params=None, timeout=None):
        calls["param"] = params["param"]
        return _resp(
            {
                "code": 0,
                "data": {
                    "sz002384": {
                        "day": [
                            [
                                "2026-11-10",
                                "90.00",
                                "91.00",
                                "91.50",
                                "89.80",
                                "120000",
                            ],
                            ["2026-11-11", "91.10", "92.06", "92.06", "91.00", "98000"],
                        ]
                    }
                },
            }
        )

    # fetch 走模块级 KLINE_SESSION（不走 requests.get），patch 目标为该 Session
    monkeypatch.setattr(gk.KLINE_SESSION, "get", fake_get)
    res = gk.fetch_kline("002384", "2026-11-11", bars=130)
    assert calls["param"].startswith("sz002384,day,")
    assert calls["param"].endswith(",130,")  # 末段空 = 不复权
    assert res.ok and isinstance(res.data, pd.DataFrame)
    assert list(res.data.columns) == ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    assert res.data["收盘"].iloc[-1] == 92.06


def test_fetch_kline_error_payload(monkeypatch):
    monkeypatch.setattr(
        gk.KLINE_SESSION, "get", lambda *a, **kw: _resp({"code": 1, "data": {}})
    )
    res = gk.fetch_kline("002384", "2026-11-11", 130)
    assert not res.ok and res.reason == "upstream_error"


def test_fetch_kline_exception(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("net down")

    monkeypatch.setattr(gk.KLINE_SESSION, "get", boom)
    assert gk.fetch_kline("002384", "2026-11-11", 130).reason == "upstream_error"


def test_fetch_kline_cached(monkeypatch):
    counter = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        counter["n"] += 1
        return _resp(
            {
                "code": 0,
                "data": {
                    "sz002384": {
                        "day": [
                            ["2026-11-11", "91.10", "92.06", "92.06", "91.00", "98000"],
                        ]
                    }
                },
            }
        )

    monkeypatch.setattr(gk.KLINE_SESSION, "get", fake_get)
    r1 = gk.fetch_kline("002384", "2026-11-11", 130)
    r2 = gk.fetch_kline("002384", "2026-11-11", 130)
    assert counter["n"] == 1 and r2.cached and r1.as_of == r2.as_of
