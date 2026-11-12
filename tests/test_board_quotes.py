"""板块行情单测：全部 mock 上游（em_push2_get / zt_industry_counts），无网络。"""

import json

import pytest

from utils import board_quotes as bq
from utils.push2_client import EmResult


@pytest.fixture(autouse=True)
def _clean():
    bq.CACHE.clear()
    bq.ulist_ok = None
    yield
    bq.CACHE.clear()
    bq.ulist_ok = None


def _clist_payload(rows):
    """clist EmResult.data 形态（push2_client 已解包 payload["data"]）：
    rows = [{f12,f14,f2,f3,f17,f18,f62,f104,f105,f128,f140,f141}]"""
    return {"total": len(rows), "diff": rows}


_ROW_YUANJIAN = {
    "f12": "BK0459",
    "f14": "元件",
    "f2": 51.83,
    "f3": 4.90,
    "f17": "-",
    "f18": 49.4854,
    "f62": 81.79e8,
    "f104": 12,
    "f105": 3,
    "f128": "东山精密",
    "f140": "002384",
    "f141": 10.0,
}
_ROW_YOUXI = {
    "f12": "BK1044",
    "f14": "游戏Ⅱ",
    "f2": 20.1,
    "f3": 1.2,
    "f17": "-",
    "f18": 19.9,
    "f62": -5e8,
    "f104": 5,
    "f105": 8,
    "f128": "某股",
    "f140": "002384",
    "f141": 5.0,
}


def _mock_upstream(
    monkeypatch, *, industry=None, concept=None, ulist=None, stock_get=None
):
    calls = {"paths": []}

    def fake(path, params):
        calls["paths"].append(path)
        if path == "/api/qt/clist/get":
            fs = params.get("fs", "")
            if "t:2" in fs:
                return industry or EmResult(ok=False, reason="upstream_error")
            if "t:3" in fs:
                return concept or EmResult(ok=False, reason="upstream_error")
        if path == "/api/qt/ulist.np/get":
            return ulist or EmResult(ok=False, reason="upstream_error")
        if path == "/api/qt/stock/get":
            return stock_get or EmResult(ok=False, reason="upstream_error")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(bq, "em_push2_get", fake)
    return calls


def test_board_quote_by_name_with_ulist(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(
            ok=True,
            data=_clist_payload([_ROW_YUANJIAN]),
            as_of="2026-11-12T09:30:00+08:00",
        ),
        concept=EmResult(
            ok=True, data=_clist_payload([]), as_of="2026-11-12T09:30:00+08:00"
        ),
        ulist=EmResult(
            ok=True,
            data={"diff": [{"f12": "BK0459", "f46": 49.4056, "f60": 49.4854}]},
            as_of="2026-11-12T09:30:00+08:00",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"元件": 5})
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert out["success"]
    d = out["data"]
    assert d["board_code"] == "BK0459" and d["board_name"] == "元件"
    assert d["scope"] == "行业"
    assert d["open"] == 49.4056 and d["prev_close"] == 49.4854
    assert abs(d["open_pct"] - (-0.161)) < 0.01  # A1 口径：(f46-f60)/f60*100
    assert d["main_net_yi"] == 81.79 and d["up"] == 12 and d["down"] == 3
    assert d["leader_name"] == "东山精密" and d["limit_up"] == 5
    assert d["as_of"] and d["source"] == "eastmoney_push2_clist"


def test_board_quote_name_verbatim_passthrough(monkeypatch):
    """A3：board_name 原文透传（罗马数字 Ⅱ 原样，不做规范化）。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YOUXI]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(
            ok=True,
            data={"diff": [{"f12": "BK1044", "f46": None, "f60": 19.9}]},
            as_of="t",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="游戏Ⅱ"))
    d = out["data"]
    assert d["board_name"] == "游戏Ⅱ" and d["open_pct"] is None
    assert d["status"] == "no_open" and "limit_up" not in d


def test_board_quote_not_found(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
    )
    out = json.loads(bq.get_board_quote(board_name="不存在的板块"))
    assert out["success"] is False and out["error_code"] == "BOARD_NOT_FOUND"


def test_board_quote_unavailable(monkeypatch):
    """A2：上游限流 → success:true + data:null + status:unavailable，不抛错。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=False, reason="rate_limited"),
        concept=EmResult(ok=False, reason="rate_limited"),
    )
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert out["success"] is True and out["data"] is None
    assert out["status"] == "unavailable" and out["reason"] == "rate_limited"


def test_boards_sort_and_with_open_false(monkeypatch):
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(
            ok=True,
            data=_clist_payload([_ROW_YOUXI, _ROW_YUANJIAN]),
            as_of="t",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(
        bq.get_boards(scope="行业", sort="main_net", limit=10, with_open=False)
    )
    assert out["success"]
    d = out["data"]
    assert d["total"] == 2 and len(d["boards"]) == 2
    assert d["boards"][0]["board_code"] == "BK0459"  # 主力净流入降序
    assert all(
        p == "/api/qt/clist/get" for p in calls["paths"]
    )  # with_open=false 不打 ulist


def test_boards_open_from_clist_f17(monkeypatch):
    """clist 自带 f17/f18 时免 ulist 请求（开盘价零成本路径）。"""
    row = dict(_ROW_YUANJIAN, f17=50.0)
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
    )
    out = json.loads(bq.get_boards(scope="行业", with_open=True))
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])
    b = out["data"]["boards"][0]
    assert (
        b["open"] == 50.0
        and abs(b["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01
    )


def test_board_quote_open_from_clist_f17(monkeypatch):
    """R1：clist 行自带 f17/f18 全齐 → 免 ulist/stock/get 请求，开盘价零成本路径。"""
    row = dict(_ROW_YUANJIAN, f17=50.0)
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])
    d = out["data"]
    assert d["open"] == 50.0 and d["prev_close"] == 49.4854
    assert abs(d["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01


def test_boards_open_fallback_stock_get(monkeypatch):
    """ulist 返回 ok 但 0 行 → 判定不支持板块 secid，退化逐板块 stock/get。"""
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 49.4056, "f60": 49.4854}, as_of="t"),
    )
    out = json.loads(bq.get_boards(scope="行业"))
    assert "/api/qt/stock/get" in calls["paths"] and bq.ulist_ok is False
    b = out["data"]["boards"][0]
    assert b["open"] == 49.4056


def test_boards_digest_when_no_args(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([_ROW_YOUXI]), as_of="t"),
    )
    out = json.loads(bq.get_board_quote())
    d = out["data"]
    assert d["total"] == 2 and {b["scope"] for b in d["boards"]} == {"行业", "概念"}


def test_limit_up_prefix_match(monkeypatch):
    """池内行业名 4 字截断（自动化设）与完整板块名（自动化设备）前缀匹配。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(
            ok=True,
            data=_clist_payload([dict(_ROW_YUANJIAN, f12="BK1036", f14="自动化设备")]),
            as_of="t",
        ),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 1.0, "f60": 1.0}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"自动化设": 3})
    out = json.loads(bq.get_board_quote(board_name="自动化设备"))
    assert out["data"]["limit_up"] == 3
