"""kline 指标单测：构造 130 根已知序列，断言 MA/pct/chg/涨跌停价精确值。"""

import json

import pandas as pd

from utils import kline_metrics as km


def _df(closes, name="东山精密"):
    n = len(closes)
    return pd.DataFrame(
        {
            "日期": [f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)],
            "开盘": [c - 0.5 for c in closes],
            "收盘": closes,
            "最高": [c + 0.6 for c in closes],
            "最低": [c - 0.7 for c in closes],
            "成交量": [10000.0] * n,
        }
    )


def test_limit_pct():
    assert km.limit_pct("002384", "东山精密") == 0.10
    assert km.limit_pct("600519", "贵州茅台") == 0.10
    assert km.limit_pct("300750", "宁德时代") == 0.20
    assert km.limit_pct("688981", "中芯国际") == 0.20
    assert km.limit_pct("002384", "ST某某") == 0.05
    assert km.limit_pct("430047", "北交票") == 0.30
    assert km.limit_pct("430047", "ST北交") == 0.30  # 北交 ST 保持 0.30


def test_compute_metrics_ma():
    closes = [80.0 + i * 0.1 for i in range(130)]
    m = km.compute_metrics(_df(closes), "东山精密")
    assert abs(m["ma5"] - round(sum(closes[-5:]) / 5, 3)) < 1e-9
    assert m["close"] == closes[-1]
    # 序列线性递增 0.1/根：ma5 = close - 0.2
    assert abs(m["ma5"] - (closes[-1] - 0.2)) < 1e-6


def test_compute_metrics_pct_and_chg():
    closes = [100.0] * 130
    closes[-61] = 50.0  # 60 根前（_chg 取 closes[-n-1]）
    closes[-6] = 80.0  # 5 根前
    closes[-11] = 40.0  # 10 根前
    closes[-1] = 100.0
    m = km.compute_metrics(_df(closes))
    assert m["pct60"] == 100.0  # (100/50-1)*100
    assert m["chg5"] == 25.0  # (100/80-1)*100
    assert m["chg10"] == 150.0  # (100/40-1)*100


def test_compute_metrics_insufficient_bars():
    m = km.compute_metrics(_df([10.0] * 30))
    # brief 原断言 chg10 is None 与其 _chg 实现（closes[-n-1]，11 根即足）矛盾，此处修正：
    # 30 根足够 chg5/10（平坦序列 → 0.0），仅 pct60（需 61 根）为 None
    assert m["ma20"] is not None and m["pct60"] is None
    assert m["chg5"] == 0.0 and m["chg10"] == 0.0
    m2 = km.compute_metrics(_df([10.0] * 10))
    assert m2["ma20"] is None and m2["chg10"] is None  # 10 根不足 ma20/chg10


def test_compute_metrics_next_limit():
    closes = [90.0] * 129 + [92.06]
    m = km.compute_metrics(_df(closes), "东山精密")
    assert m["limit_pct"] == 0.10
    assert abs(m["next_limit_up"] - round(92.06 * 1.1, 3)) < 1e-9  # 101.266
    assert abs(m["next_limit_down"] - round(92.06 * 0.9, 3)) < 1e-9  # 82.854
    assert m["high"] == 92.06 + 0.6 and m["low"] == 92.06 - 0.7  # 当日高低


def test_batch_klines_concurrent(monkeypatch):
    from utils import gtimg_kline as gk  # noqa: F401
    from utils.push2_client import EmResult

    def fake_fetch(symbol, end_date, bars):
        assert end_date == "2026-11-11" and bars == 130
        if symbol == "999999":
            return EmResult(ok=False, reason="upstream_error")
        return EmResult(ok=True, data=_df([10.0] * 130), as_of="t")

    monkeypatch.setattr(km, "fetch_kline", fake_fetch)
    out = km.batch_klines(["002384", "601595", "999999"], "2026-11-11")
    assert out["items"]["002384"]["close"] == 10.0
    assert out["items"]["999999"] is None
    assert any("999999" in w for w in out["warnings"])


def test_batch_klines_envelope_flags(monkeypatch):
    """spec §4.3：批量输出带 as_of/stale/cached，仅对 ok 结果聚合。"""
    from utils.push2_client import EmResult

    def fake_fetch(symbol, end_date, bars):
        if symbol == "999999":
            return EmResult(ok=False, reason="upstream_error")
        return EmResult(
            ok=True,
            data=_df([10.0] * 130),
            as_of=f"2026-11-12T14:3{symbol[-1]}:00",
            cached=True,
        )

    monkeypatch.setattr(km, "fetch_kline", fake_fetch)
    out = km.batch_klines(["002384", "601595", "999999"], "2026-11-11")
    # as_of 取 ok 结果最大值；失败票不参与聚合
    assert out["as_of"] == "2026-11-12T14:35:00"
    assert out["stale"] is False
    assert out["cached"] is True  # 全部 ok 结果均命中缓存


def test_batch_klines_envelope_flags_stale_and_all_fail(monkeypatch):
    """任一 ok 结果 stale → stale=True；全部失败 → as_of=""/stale=False/cached=False。"""
    from utils.push2_client import EmResult

    monkeypatch.setattr(
        km,
        "fetch_kline",
        lambda symbol, end_date, bars: EmResult(
            ok=True, data=_df([10.0] * 130), as_of="t", stale=True
        ),
    )
    out = km.batch_klines(["002384"], "2026-11-11")
    assert out["as_of"] == "t" and out["stale"] is True and out["cached"] is False

    monkeypatch.setattr(
        km,
        "fetch_kline",
        lambda symbol, end_date, bars: EmResult(ok=False, reason="upstream_error"),
    )
    out = km.batch_klines(["002384"], "2026-11-11")
    assert out["as_of"] == "" and out["stale"] is False and out["cached"] is False


def test_get_klines_batch_invalid_bars():
    """helper 直调（绕开 mcp.tool 装饰链）：bars 非 20~1000 整数 → INVALID_ARG。"""
    from mcp_tools.fupan import get_klines_batch

    for bad_bars in ("abc", 10, 1001, None):
        out = json.loads(get_klines_batch(["002384"], bad_bars, "2026-11-11"))
        assert out["success"] is False and out["error_code"] == "INVALID_ARG", bad_bars
        assert "bars 非法" in out["error"]
