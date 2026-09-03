"""真实接口冒烟测试（默认跳过；显式运行: -m live）。需要 AKPROXY_TOKEN。"""

import json

import pytest

from utils import (
    get_cffex_rank,
    get_daily_review,
    get_index_derivatives,
    get_market_breadth,
)


@pytest.mark.live
def test_daily_review_live():
    out = get_daily_review()  # 今天；非交易日时部分模块降级属预期
    payload = json.loads(out)
    assert payload["success"] in (True, False)  # 只验证不崩、结构是 JSON 信封
    if payload["success"]:
        assert "indices" in payload["data"]


@pytest.mark.live
def test_breadth_and_derivatives_live():
    assert "success" in json.loads(get_market_breadth())
    assert "success" in json.loads(get_index_derivatives())
    assert "success" in json.loads(get_cffex_rank(var="IO", member="中信"))
