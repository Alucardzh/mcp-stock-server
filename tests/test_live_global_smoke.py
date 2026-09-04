"""全球市场工具真实接口冒烟（默认跳过；-m live 运行）。需 YAHOO_PROXY 与 AKPROXY_TOKEN。"""

import json

import pytest

from utils import (
    get_fed_watch,
    get_global_linkage_review,
    get_global_markets,
    get_stock_global_snapshot,
)


@pytest.mark.live
def test_global_markets_live():
    payload = json.loads(get_global_markets())
    assert payload["success"] is True
    assert payload["data"]["美股"]["indexes"][0]["close"] is not None


@pytest.mark.live
def test_global_stocks_live():
    payload = json.loads(get_stock_global_snapshot(preset="ai_chain"))
    assert payload["success"] is True
    ok = [q for q in payload["data"]["quotes"] if q.get("close") is not None]
    assert len(ok) >= 5  # 允许个别失败


@pytest.mark.live
def test_fed_watch_live():
    assert json.loads(get_fed_watch())["success"] is True


@pytest.mark.live
def test_global_linkage_live():
    payload = json.loads(get_global_linkage_review())
    assert payload["success"] is True
