"""板块工具真实接口冒烟（-m live 显式运行，需 AKPROXY_TOKEN；交易日盘中跑最有意义）。"""

import json
import time

import pytest

from utils import (
    get_board_members,
    get_board_quote,
    get_boards,
    get_zt_pool,
    warmup_board_cache,
)


@pytest.mark.live
def test_board_quote_live():
    out = json.loads(get_board_quote(board_name="元件"))
    assert out["success"]
    if not out["data"]:  # 限流时 data=null + status=unavailable，属预期形态
        pytest.skip(f"上游不可用: {out.get('status')}/{out.get('reason')}")
    d = out["data"]
    assert d["board_code"] == "BK0459" and d["board_name"] == "元件"
    assert "open_pct" in d and "as_of" in d and "source" in d


@pytest.mark.live
def test_boards_live_verbatim_names():
    """A3：概念/行业清单返回原文（存在特殊字符名即透传成功）。"""
    out = json.loads(get_boards(scope="行业", sort="main_net", limit=50))
    assert out["success"]
    if not out["data"]:
        pytest.skip(f"上游不可用: {out.get('status')}/{out.get('reason')}")
    assert out["data"]["total"] > 50
    names = [b["board_name"] for b in out["data"]["boards"]]
    assert all(n for n in names)  # 无空名


@pytest.mark.live
def test_zt_pool_full_live():
    """A4：full=true 时 items 长度 == count（不截断）。"""
    out = json.loads(get_zt_pool("涨停", full=True))
    assert out["success"]
    if not out["data"]:
        pytest.skip(f"上游不可用: {out.get('error')}")
    assert len(out["data"]["items"]) == out["data"]["count"]


@pytest.mark.live
def test_rapid_calls_never_throw():
    """A2：连续高频 20 次不抛错（限流时返回 unavailable 信封也算通过）。"""
    for _ in range(20):
        payload = json.loads(get_board_quote(board_code="BK0459"))
        assert "success" in payload


@pytest.mark.live
def test_v3_gaps_live():
    from utils import get_board_members, get_board_quote, get_boards, warmup_board_cache

    w = json.loads(warmup_board_cache(["BK0459", "BK0481"]))
    assert w["success"] and w["data"]["lists"]["行业"] == "ok"
    t0 = time.time()
    q = json.loads(get_board_quote(board_code="BK0481"))
    dt = time.time() - t0
    if q.get("data"):
        assert q["data"]["board_name"] == "汽车零部件"
        assert dt <= 3.5, f"预热后 {dt:.1f}s"
    p = json.loads(get_board_quote(board_name="汽车零部"))
    if p.get("data"):
        assert p["data"].get("matched_by") == "prefix"
    b = json.loads(get_boards(scope="概念", sort="code", limit=600))
    if b.get("data"):
        assert b["data"]["total"] > 500 and len(b["data"]["boards"]) > 500
    m = json.loads(get_board_members("BK0459", limit=50))
    if m.get("data"):
        assert m["data"]["count"] > 20 and m["data"]["truncated"] is False
