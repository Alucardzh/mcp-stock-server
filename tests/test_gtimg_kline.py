"""gtimg 日K单测：URL 构造 / 响应解析 / 不复权键名 / 缓存（全 mock，无网络）。"""

import json
from datetime import datetime as real_datetime

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


def test_fetch_kline_writes_attrs(monkeypatch):
    """T3 修订：attrs（symbol/name）在进缓存前一次性写入；name 取 qt 的 t__ 节点第 2 项。"""

    def fake_get(url, params=None, timeout=None):
        return _resp(
            {
                "code": 0,
                "data": {
                    "sz002384": {
                        "day": [
                            ["2026-11-11", "91.10", "92.06", "92.06", "91.00", "98000"],
                        ],
                        "qt": {"t__sz002384": ["1", "东山精密", "002384", "SZ"]},
                    }
                },
            }
        )

    monkeypatch.setattr(gk.KLINE_SESSION, "get", fake_get)
    res = gk.fetch_kline("002384", "2026-11-11", 130)
    assert res.ok
    assert res.data.attrs["symbol"] == "002384"
    assert res.data.attrs["name"] == "东山精密"
    # 缓存命中路径共享同一 DataFrame 引用 → attrs 仍在（消费方只读的前提）
    res2 = gk.fetch_kline("002384", "2026-11-11", 130)
    assert res2.cached and res2.data is res.data
    assert res2.data.attrs["symbol"] == "002384"


def test_fetch_kline_writes_attrs_no_qt(monkeypatch):
    """响应无 qt 节点 → name 为空串，不抛异常。"""
    monkeypatch.setattr(
        gk.KLINE_SESSION,
        "get",
        lambda *a, **kw: _resp(
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
        ),
    )
    res = gk.fetch_kline("002384", "2026-11-11", 130)
    assert (
        res.ok and res.data.attrs["name"] == "" and res.data.attrs["symbol"] == "002384"
    )


def test_fetch_kline_non_dict_payload(monkeypatch):
    """T3 修订：JSON 非 dict（如列表）不外抛 AttributeError，进 upstream_error。"""
    monkeypatch.setattr(gk.KLINE_SESSION, "get", lambda *a, **kw: _resp([1, 2, 3]))
    res = gk.fetch_kline("002384", "2026-11-11", 130)
    assert not res.ok and res.reason == "upstream_error"


def _fake_datetime(hour: int, minute: int):
    """pinned now：_ttl 只用 datetime.now()，无需其余类方法。"""

    class FakeDT:
        @staticmethod
        def now():
            return real_datetime(2026, 11, 12, hour, minute)

    return FakeDT


def test_ttl_post_close_bump(monkeypatch):
    """收盘后（≥15:05）当日 bar 已定稿 → 按历史 TTL；盘中（<15:05）保持短 TTL。

    TTL 反转修复：当日 kline TTL(120) < fupan 整包 TTL(300) 时，整包每次过期
    都重打全量 gtimg 批次，易触发反爬 501。
    """
    today = "2026-11-12"
    # 盘中 / 边界前一分钟 → 短 TTL
    for h, m in ((9, 30), (15, 4)):
        monkeypatch.setattr(gk, "datetime", _fake_datetime(h, m))
        assert gk._ttl(today) == gk._TODAY_TTL, f"{h}:{m:02d} 应为盘中短 TTL"
    # 收盘后（含 15:05 边界）→ 当日按历史长期缓存
    for h, m in ((15, 5), (23, 59)):
        monkeypatch.setattr(gk, "datetime", _fake_datetime(h, m))
        assert gk._ttl(today) == gk._HISTORY_TTL, f"{h}:{m:02d} 收盘后应为历史 TTL"
    # 过去日期无论何时查询恒为历史 TTL（monkeypatch 撤销前的最后状态也覆盖）
    monkeypatch.setattr(gk, "datetime", _fake_datetime(9, 30))
    assert gk._ttl("2026-11-11") == gk._HISTORY_TTL
