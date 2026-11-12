"""板块工具真实接口冒烟（-m live 显式运行，需 AKPROXY_TOKEN；交易日盘中跑最有意义）。"""

import json

import pytest

from utils import get_board_quote, get_boards, get_zt_pool


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
