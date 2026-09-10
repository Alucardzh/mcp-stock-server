"""真实接口冒烟测试（默认跳过；显式运行: -m live）。需要 AKPROXY_TOKEN。"""

import json

import pytest

from utils import (
    get_cffex_rank,
    get_daily_review,
    get_index_derivatives,
    get_market_breadth,
    get_stock_history,
    get_stock_realtime,
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


@pytest.mark.live
def test_stock_history_via_efinance_live():
    """个股K线走 efinance 通道：验证记录含 akshare 风格列名"""
    payload = json.loads(get_stock_history("600519", "2026-08-01", "2026-09-09"))
    assert payload["success"], payload.get("error")
    records = payload["data"]["records"]
    assert records, "应有K线记录"
    first = records[0]
    for col in ("日期", "股票代码", "开盘", "收盘", "最高", "最低", "成交量"):
        assert col in first, f"缺少列 {col}"


@pytest.mark.live
def test_stock_realtime_via_efinance_live():
    """实时行情走 efinance 快照：验证代码/名称/价格字段可解析"""
    payload = json.loads(get_stock_realtime("600519"))
    assert payload["success"], payload.get("error")
    data = payload["data"]
    assert data["symbol"] == "600519"
    assert data["name"] == "贵州茅台"
    assert data["current_price"] and data["current_price"] > 0
