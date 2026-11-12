"""fupan_v1 live 验收（-m live；交易日收盘后运行最有意义）。

锚点日期 2026-11-11（周三，交易日）：池 15:00 定稿、龙虎榜席位 ~17-18:00 定稿。
验收日 2026-11-12 盘中(<15:05)运行时 11-12 当日不可用，11-11 为最近完整交易日。
"""

import json
import time

import pytest

from utils import fupan_v1, get_board_quote, get_daily_review  # noqa: F401

_ACC_DATE = "2026-11-11"
_CHAINS = {"电子链": ["半导体", "光学光电", "元件", "消费电子"]}


@pytest.mark.live
def test_fupan_v1_live_recent_trading_day():
    out = json.loads(
        fupan_v1(_ACC_DATE, symbols=["002384"], options={"chain_buckets": _CHAINS})
    )
    assert out["success"] in (True, False)
    if not out["success"]:
        pytest.skip(f"{out.get('error_code')}: {out.get('error')}")
    s = out["data"]["sections"]
    assert s["pools"]["zt"]["count"] == len(s["pools"]["zt"]["items"])
    k = s["klines"].get("002384")
    if k:
        assert k["ma5"] and k["next_limit_down"] == round(k["close"] * 0.9, 3)
    # 验收锚点实录（F2/F3/F5/F6）——不 assert 具体值（上游数据会变），打印供回填
    print(f"F2 zt/zb/dt: {s['pools']['zt']['count']}/{s['pools']['zb']['count'] if s['pools']['zb'] else 0}"
          f"/{s['pools']['dt']['count'] if s['pools'].get('dt') else 0}")
    print(f"F3 chains: {json.dumps(s.get('chains', {}), ensure_ascii=False)}")
    if k:
        print(f"F5 002384 ma5/10/20: {k['ma5']}/{k['ma10']}/{k['ma20']}"
              f" close={k['close']} next_limit_down={k['next_limit_down']}")
    lhb = s.get("lhb") or {}
    for code in ("002384", "601595"):
        st = (lhb.get("stocks") or {}).get(code)
        if st:
            print(f"F6 {code} inst/foreign/hot: {st['inst']}/{st['foreign']}/{st['hot']}")


@pytest.mark.live
def test_fupan_v1_perf():
    t0 = time.time()
    out1 = json.loads(fupan_v1(_ACC_DATE))
    cold = time.time() - t0
    t1 = time.time()
    out2 = json.loads(fupan_v1(_ACC_DATE))
    warm = time.time() - t1
    if not out1["success"]:
        pytest.skip("上游不可用")
    print(f"F7 cold={cold:.1f}s warm={warm:.1f}s cached={out2['data']['cached']}")
    assert warm <= 3.5, f"缓存命中 {warm:.1f}s > 3s 目标"
    assert cold <= 20, f"冷启动 {cold:.1f}s（目标 15s，20s 为冒烟上限）"


@pytest.mark.live
def test_board_quote_scale_live():
    out = json.loads(get_board_quote(board_name="元件"))
    if not out.get("data"):
        pytest.skip("上游不可用")
    assert out["data"]["scale"]["divisor"] == 100  # A1 增量
    print(f"A1 scale: {out['data']['scale']}")


@pytest.mark.live
def test_f1_diff_daily_review():
    a = json.loads(fupan_v1(_ACC_DATE))
    b = json.loads(get_daily_review(_ACC_DATE))
    if not (a["success"] and b["success"]):
        pytest.skip("上游不可用")
    # F1 diff=0 落在共享段数据：get_daily_review 会把各段 notes 上浮到顶层
    # （[section] 前缀），fupan_v1 按段保留——排除 notes 键后比较才是同口径
    for sec in ("indices", "breadth", "fund_flow"):
        a_sec = {k: v for k, v in a["data"]["sections"][sec].items() if k != "notes"}
        same = a_sec == b["data"][sec]
        print(f"F1 {sec} same={same}")
        assert same, f"{sec} 段数据不一致（notes 除外）"
