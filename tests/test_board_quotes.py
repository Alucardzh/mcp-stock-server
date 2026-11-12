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


def test_board_quote_partial_scope_miss_unavailable(monkeypatch):
    """行业快照 ok + 概念快照失败：查概念侧板块名 → unavailable(rate_limited)
    而非 BOARD_NOT_FOUND（缺数据≠条件不达标；rate-limit 恰在概念侧生效的常见形态）。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=False, reason="rate_limited"),
    )
    out = json.loads(bq.get_board_quote(board_name="人工智能"))
    assert out["success"] is True and out["data"] is None
    assert out["status"] == "unavailable" and out["reason"] == "rate_limited"


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
    """R2：clist f17/f18 全齐 → 免 ulist；仅用其比值算 open_pct（刻度不变），
    原始 open/prev_close 非标刻度不外发（null）。"""
    row = dict(_ROW_YUANJIAN, f17=50.0)
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
    )
    out = json.loads(bq.get_boards(scope="行业", with_open=True))
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])
    b = out["data"]["boards"][0]
    assert b["open"] is None and b["prev_close"] is None
    assert abs(b["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01


def test_board_quote_open_via_stock_get(monkeypatch):
    """R1：板块 clist f17/f18 为非标刻度（×1000），开盘价一律走 fetch_board_open
    （ulist 对板块返回 '-' 空结果 → 自动退化 stock/get）；clist 原始值不得混入。"""
    row = dict(_ROW_YUANJIAN, f17=50000.0, f18=49485.34)  # 非标刻度形态
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),  # 板块真实形态：'-'
        stock_get=EmResult(ok=True, data={"f46": 50.0, "f60": 49.4854}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert "/api/qt/stock/get" in calls["paths"]
    d = out["data"]
    assert d["open"] == 50.0 and d["prev_close"] == 49.4854  # 非 clist 刻度
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


def _paginated_upstream(monkeypatch, pages, *, ulist=None, stock_get=None):
    """clist 按 params["pn"] 翻页的 mock：pages = [第1页rows, 第2页rows, ...]（概念），
    行业固定单页空；pn 超出提供的页数直接断言失败（暴露翻页 bug）。"""
    calls = {"paths": [], "pns": []}

    def fake(path, params):
        calls["paths"].append(path)
        if path == "/api/qt/clist/get":
            fs = params.get("fs", "")
            if "t:2" in fs:
                return EmResult(ok=True, data=_clist_payload([]), as_of="t")
            if "t:3" in fs:
                pn = int(params.get("pn", 1))
                calls["pns"].append(pn)
                assert pn <= len(pages), f"unexpected page {pn}"
                return EmResult(
                    ok=True,
                    data={"total": sum(len(p) for p in pages), "diff": pages[pn - 1]},
                    as_of="t",
                )
        if path == "/api/qt/ulist.np/get":
            return ulist or EmResult(ok=True, data={"diff": []}, as_of="t")
        if path == "/api/qt/stock/get":
            return stock_get or EmResult(ok=False, reason="upstream_error")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(bq, "em_push2_get", fake)
    return calls


def _concept_rows(page_no: int, n: int):
    return [
        dict(_ROW_YOUXI, f12=f"BK9{page_no}{i:03d}", f14=f"概念P{page_no}-{i}")
        for i in range(n)
    ]


def test_board_list_pagination_merges_pages(monkeypatch):
    """clist 单页上限 100：page1=100 行 + total=250，page2=50 行 → 合并 150，
    概念 scope 恰好 2 次 clist 调用（pn=1,2）。"""
    calls = _paginated_upstream(
        monkeypatch, [_concept_rows(1, 100), _concept_rows(2, 50)]
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="概念", with_open=False, limit=50))
    d = out["data"]
    assert d["total"] == 150 and len(d["boards"]) == 50  # total=合并行数≠上游250
    assert calls["pns"] == [1, 2]
    assert all(
        p == "/api/qt/clist/get" for p in calls["paths"]
    )  # with_open=false 无开盘请求


def test_board_quote_beyond_first_page_found(monkeypatch):
    """翻页后第二页的板块可按名查询（旧行为：top-100 之外误报 BOARD_NOT_FOUND）。"""
    calls = _paginated_upstream(
        monkeypatch,
        [_concept_rows(1, 100), _concept_rows(2, 50)],
        stock_get=EmResult(ok=True, data={"f46": 10.5, "f60": 10.0}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="概念P2-49"))
    assert out["success"], out
    d = out["data"]
    assert d["board_code"] == "BK92049" and d["scope"] == "概念"
    assert d["open"] == 10.5 and d["prev_close"] == 10.0  # 来自 stock/get
    assert calls["pns"] == [1, 2] and "/api/qt/stock/get" in calls["paths"]


def test_boards_open_gap_too_large_skips_batch(monkeypatch):
    """盘前形态：>20 板块缺 f17/f18 → 不发起批量开盘请求（免逐板块 stock/get
    风暴），open_pct 走 clist 比值（缺者 None），open/prev_close 为 null，note 说明。"""
    rows = [
        dict(_ROW_YUANJIAN, f12=f"BK7{i:03d}", f14=f"行业{i:03d}", f17=50.0)
        for i in range(3)
    ]
    rows += [
        dict(_ROW_YUANJIAN, f12=f"BK8{i:03d}", f14=f"缺失{i:03d}") for i in range(22)
    ]
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload(rows), as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", with_open=True, limit=50))
    assert out["success"]
    d = out["data"]
    assert all(p == "/api/qt/clist/get" for p in calls["paths"])  # 零开盘上游请求
    assert d["notes"] == [
        "开盘价缺口过大(缺22/总25)，跳过批量获取，open_pct 为 clist 比值口径"
    ]
    by_code = {b["board_code"]: b for b in d["boards"]}
    assert by_code["BK7000"]["open"] is None and by_code["BK7000"]["prev_close"] is None
    assert (
        abs(by_code["BK7000"]["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01
    )  # clist 比值仍在
    assert by_code["BK8000"]["open_pct"] is None and by_code["BK8000"]["open"] is None


def test_boards_open_batch_targets_missing_only(monkeypatch):
    """≤20 缺口：批量开盘请求只打缺失板块（不全量；覆盖板免 ulist/stock/get，
    且不会被 stock/get 的 20 只截断挤掉真正缺口的板块）。"""
    covered = [
        dict(_ROW_YUANJIAN, f12=f"BK6{i:02d}", f14=f"覆盖{i}", f17=50.0)
        for i in range(2)
    ]
    missing_rows = [
        dict(_ROW_YUANJIAN, f12=f"BK5{i:02d}", f14=f"缺失{i}") for i in range(3)
    ]
    calls = {"paths": [], "secids": []}

    def fake(path, params):
        calls["paths"].append(path)
        if path == "/api/qt/clist/get":
            if "t:2" in params.get("fs", ""):
                return EmResult(
                    ok=True, data=_clist_payload(covered + missing_rows), as_of="t"
                )
            return EmResult(ok=True, data=_clist_payload([]), as_of="t")
        if path == "/api/qt/ulist.np/get":
            return EmResult(ok=True, data={"diff": []}, as_of="t")  # 板块形态：'-'
        if path == "/api/qt/stock/get":
            calls["secids"].append(params.get("secid"))
            return EmResult(ok=True, data={"f46": 7.7, "f60": 7.0}, as_of="t")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(bq, "em_push2_get", fake)
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", with_open=True, limit=50))
    d = out["data"]
    assert sorted(calls["secids"]) == ["90.BK500", "90.BK501", "90.BK502"]  # 仅缺失板块
    by_code = {b["board_code"]: b for b in d["boards"]}
    assert by_code["BK500"]["open"] == 7.7 and by_code["BK500"]["prev_close"] == 7.0
    assert abs(by_code["BK500"]["open_pct"] - (7.7 - 7.0) / 7.0 * 100) < 0.01
    assert by_code["BK600"]["open"] is None  # 覆盖板原始值仍 null（clist 比值口径）
    assert abs(by_code["BK600"]["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01
    assert d["notes"] == []


def test_boards_open_fetch_failure_note_clist_ratio(monkeypatch):
    """≤20 缺口但批量开盘获取失败：note 口径对齐（open_pct 为 clist 比值，
    不再误称"为空"）；覆盖板 open_pct 仍在，缺板为 None。"""
    rows = [
        dict(_ROW_YUANJIAN, f12="BK601", f14="覆盖一", f17=50.0),
        dict(_ROW_YUANJIAN, f12="BK501", f14="缺失一"),
    ]
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload(rows), as_of="t"),
        ulist=EmResult(ok=False, reason="rate_limited"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", with_open=True))
    d = out["data"]
    assert d["notes"] == [
        "开盘涨跌幅获取失败(rate_limited)，open_pct 为 clist 比值口径"
    ]
    by_code = {b["board_code"]: b for b in d["boards"]}
    assert by_code["BK501"]["open_pct"] is None and by_code["BK501"]["open"] is None
    assert abs(by_code["BK601"]["open_pct"] - (50.0 - 49.4854) / 49.4854 * 100) < 0.01


def test_board_quote_scale_block(monkeypatch):
    """R1 补丁：输出带 current 点位与 scale 块（raw=value×10^f59 无损重建）。"""
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        # ulist 对板块 secid 返回 '-' 空结果（ok 但 0 行）→ 退化 stock/get 通道
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(
            ok=True,
            data={"f43": 51000.0, "f46": 49405.61, "f60": 49485.34, "f59": 2},
            as_of="t",
        ),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="元件"))
    d = out["data"]
    assert d["current"] == 51000.0
    assert d["scale"] == {
        "raw_f46": 4940561,
        "raw_f60": 4948534,
        "divisor": 100,
        "f59": 2,
    }


def test_board_list_exact_page_count_stops(monkeypatch):
    """total=200 恰为 2 整页：第 2 页满页后按 upstream_total 停止，
    不请求必空的第 3 页（旧行为：空页→rate_limited→整个快照失败）。"""
    calls = _paginated_upstream(
        monkeypatch, [_concept_rows(1, 100), _concept_rows(2, 100)]
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="概念", with_open=False, limit=5))
    assert out["success"], out
    assert out["data"]["total"] == 200
    assert calls["pns"] == [1, 2]  # 无第 3 次请求


# ---- Task5 fupan_v1: boards 全量段（行业+概念，无 limit 截断） ----


def test_fupan_boards_section(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([_ROW_YOUXI]), as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"元件": 5})
    out = bq.fupan_boards_section()
    assert out["industry"][0]["board_code"] == "BK0459"
    assert out["industry"][0]["limit_up"] == 5
    assert "open_pct" in out["concept"][0]  # clist 比值口径（f17='-' → None）


def test_fupan_boards_section_partial_degrade(monkeypatch):
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=False, reason="rate_limited"),
    )
    # 本文件契约"无网络"：真实 zt_industry_counts 会打 akshare 且污染共享
    # PUSH2_CACHE（pool:涨停:今日 会串进 test_review_market 的当日用例）
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = bq.fupan_boards_section()
    assert out["concept"] == {"status": "unavailable", "reason": "rate_limited"}
    assert out["industry"][0]["board_code"] == "BK0459"


def _row(code, name, main_e8):
    return {
        "f12": code,
        "f14": name,
        "f2": 10.0,
        "f3": 1.0,
        "f17": "-",
        "f18": 10.0,
        "f62": main_e8,
        "f104": 1,
        "f105": 1,
        "f128": "领涨",
        "f140": "000001",
        "f141": 9.9,
    }


def test_boards_offset_pagination(monkeypatch):
    """limit 上限 200；offset 按 sort 排序后跳过前 N 条（分页取尾部数据）。"""
    rows = [
        _row("BK0459", "元件", 81.79e8),  # main_net 降序第 1
        _row("BK1036", "半导体", 50.0e8),  # 第 2
        _row("BK1044", "游戏Ⅱ", -5.0e8),  # 第 3
    ]
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload(rows), as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", sort="main_net", limit=1, offset=1))
    d = out["data"]
    assert d["offset"] == 1 and d["total"] == 3
    assert [b["board_code"] for b in d["boards"]] == ["BK1036"]

    # offset 越界 → 空列表（不报错），total 仍为全量
    out2 = json.loads(bq.get_boards(scope="行业", offset=10))
    assert out2["data"]["boards"] == [] and out2["data"]["total"] == 3

    # limit>200 被钳制为 200（返回全部 3 条），offset<0 钳制为 0
    out3 = json.loads(bq.get_boards(scope="行业", limit=300, offset=-5))
    assert len(out3["data"]["boards"]) == 3 and out3["data"]["offset"] == 0


def test_boards_sort_code_name_and_paging_meta(monkeypatch):
    """G1：sort=code/name 字典序（升序）；limit 钳 600；has_more/next_offset。"""
    rows = [
        _row("BK0002", "乙板块", 1e8),
        _row("BK0001", "甲板块", 2e8),
        _row("BK0003", "丙板块", 3e8),
    ]
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload(rows), as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_boards(scope="行业", sort="code", limit=2, offset=1))
    d = out["data"]
    assert [b["board_code"] for b in d["boards"]] == ["BK0002", "BK0003"]  # 升序+分页
    assert d["has_more"] is False and d["next_offset"] is None and d["total"] == 3
    out2 = json.loads(bq.get_boards(scope="行业", sort="code", limit=2))
    assert out2["data"]["has_more"] is True and out2["data"]["next_offset"] == 2
    out3 = json.loads(bq.get_boards(scope="行业", sort="name", limit=1))
    assert out3["data"]["boards"][0]["board_name"] == "丙板块"  # name 字典序
    out4 = json.loads(bq.get_boards(scope="行业", limit=1000))
    assert len(out4["data"]["boards"]) == 3  # limit 钳 600 不报错


def test_board_quote_prefix_unique_hit(monkeypatch):
    """G2：截断名前缀唯一命中（汽车零部→汽车零部件）。"""
    row = _row("BK0481", "汽车零部件", 1e8)
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([row]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 42192.07, "f60": 42144.03}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="汽车零部"))
    d = out["data"]
    assert d["board_code"] == "BK0481" and d["matched_by"] == "prefix"
    assert d["board_name"] == "汽车零部件"


def test_board_quote_prefix_ambiguous(monkeypatch):
    """G2：前缀多命中 → status=ambiguous + 候选列表（不误报 BOARD_NOT_FOUND）。"""
    rows = [_row("BK0481", "汽车零部件", 1e8), _row("BK0482", "汽车整车", 1e8)]
    _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload(rows), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
    )
    out = json.loads(bq.get_board_quote(board_name="汽车"))
    assert out["success"] is True and out["data"] is None
    assert out["status"] == "ambiguous"
    assert {c["board_name"] for c in out["candidates"]} == {"汽车零部件", "汽车整车"}
    assert all(c["scope"] == "行业" for c in out["candidates"])


# ---- Task2 G5: lazy scope / fields 轻量 / warmup ----


def test_board_quote_lazy_scope_skips_concept(monkeypatch):
    """G5：行业命中不拉概念清单（省 6 页 × pacing）。"""
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 49.4, "f60": 49.4}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": None)
    out = json.loads(bq.get_board_quote(board_name="元件"))
    assert out["success"]
    fs_params = [p for p in calls["paths"] if p == "/api/qt/clist/get"]
    assert len(fs_params) == 1  # 只有行业一页，未拉概念


def test_board_quote_light_fields(monkeypatch):
    """G5：fields 轻量路径——仅 board_code，直连开盘价通道，不打清单。"""
    calls = _mock_upstream(
        monkeypatch,
        stock_get=EmResult(
            ok=True,
            data={"f46": 42192.07, "f60": 42144.03, "f43": 43000.0, "f59": 2},
            as_of="t",
        ),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
    )
    out = json.loads(
        bq.get_board_quote(board_code="BK0481", fields=["open", "open_pct"])
    )
    d = out["data"]
    assert d["open"] == 42192.07 and abs(d["open_pct"] - 0.114) < 0.01
    assert "main_net_yi" not in d and "limit_up" not in d
    assert calls["paths"] == ["/api/qt/ulist.np/get", "/api/qt/stock/get"]


def test_board_quote_light_fields_invalid(monkeypatch):
    out = json.loads(bq.get_board_quote(board_code="BK0481", fields=["main_net_yi"]))
    assert out["success"] is False and out["error_code"] == "INVALID_ARG"
    out2 = json.loads(
        bq.get_board_quote(board_name="元件", fields=["open"])  # 轻量仅支持 code
    )
    assert out2["error_code"] == "INVALID_ARG"


def test_warmup_board_cache(monkeypatch):
    calls = _mock_upstream(
        monkeypatch,
        industry=EmResult(ok=True, data=_clist_payload([_ROW_YUANJIAN]), as_of="t"),
        concept=EmResult(ok=True, data=_clist_payload([]), as_of="t"),
        ulist=EmResult(ok=True, data={"diff": []}, as_of="t"),
        stock_get=EmResult(ok=True, data={"f46": 1.0, "f60": 1.0}, as_of="t"),
    )
    monkeypatch.setattr(bq, "zt_industry_counts", lambda date="": {"元件": 5})
    out = json.loads(bq.warmup_board_cache(["BK0459"]))
    d = out["data"]
    assert d["lists"] == {"行业": "ok", "概念": "ok"}
    assert d["opens"] == {"requested": 1, "ok": 1}
    assert d["pool"] == "ok"
    assert "/api/qt/stock/get" in calls["paths"]
