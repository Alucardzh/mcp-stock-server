#!/usr/bin/env python3
"""个股日K指标摘要（不复权）：MA/区间涨幅/涨跌停价——fupan_v1 klines 段口径。

口径（spec §4.2）：ma5/10/20=不复权收盘均线；pct60=(close/close[-60]-1)*100；
chg5/chg10=(close/close[-5|-10]-1)*100，bars 不足为 None；high/low=当日；
next_limit_up/down=close*(1±limit_pct)。
"""

from concurrent.futures import ThreadPoolExecutor
import logging

import pandas as pd

from .gtimg_kline import fetch_kline  # noqa: F401  (测试 monkeypatch 点)

logger = logging.getLogger(__name__)

_BATCH_WORKERS = 8


def limit_pct(symbol: str, name: str = "") -> float:
    """涨跌停幅度：北交 0.30（含北交ST）> ST 0.05 > 科创创业 0.20 > 主板 0.10。"""
    if symbol.startswith(("8", "4", "92")):
        return 0.30
    if "ST" in (name or "").upper():
        return 0.05
    if symbol.startswith(("30", "68")):
        return 0.20
    return 0.10


def _ma(closes: list[float], n: int):
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 3)


def _chg(closes: list[float], n: int):
    if len(closes) <= n or not closes[-n - 1]:
        return None
    return round((closes[-1] / closes[-n - 1] - 1) * 100, 3)


def compute_metrics(df: pd.DataFrame, name: str = "") -> dict:
    """DataFrame（gtimg_kline 列名）→ 指标 dict。bars 不足的指标为 None。

    symbol/name 优先取显式入参，其次 df.attrs（fetch_kline 写入，只读）。
    """
    closes = [float(x) for x in df["收盘"].tolist()]
    last = df.iloc[-1]
    symbol = str(df.attrs.get("symbol", ""))
    name = name or str(df.attrs.get("name", ""))
    lp = limit_pct(symbol, name)
    close = closes[-1]
    return {
        "close": close,
        "ma5": _ma(closes, 5),
        "ma10": _ma(closes, 10),
        "ma20": _ma(closes, 20),
        "pct60": _chg(closes, 60),
        "chg5": _chg(closes, 5),
        "chg10": _chg(closes, 10),
        "high": float(last["最高"]),
        "low": float(last["最低"]),
        "limit_pct": lp,
        "next_limit_up": round(close * (1 + lp), 3),
        "next_limit_down": round(close * (1 - lp), 3),
    }


def _one(symbol: str, end_date: str, bars: int):
    """单票取数：透传 fetch_kline 的 EmResult（含 ok/as_of/stale/cached 标志）。"""
    return symbol, fetch_kline(symbol, end_date, bars)


def batch_klines(symbols: list[str], end_date: str, bars: int = 130) -> dict:
    """并发取数并算指标（无 JSON 信封）。

    返回 {items: {code: dict|None}, warnings: [...], as_of, stale, cached}：
    as_of/stale/cached 仅对 ok 结果聚合（spec §4.3 信封字段）——as_of 取最大值
    （无 ok 结果为 ""），stale 任一为真，cached 须全部命中（无 ok 结果为 False）。
    """
    symbols = sorted({s.strip() for s in symbols if s and s.strip()})
    with ThreadPoolExecutor(max_workers=_BATCH_WORKERS) as pool:
        results = list(pool.map(lambda s: _one(s, end_date, bars), symbols))
    items, warnings, ok_results = {}, [], []
    for symbol, res in results:
        if not res.ok or res.data is None or res.data.empty:
            items[symbol] = None
            warnings.append(f"{symbol} 日K获取失败")
            continue
        items[symbol] = compute_metrics(res.data)
        ok_results.append(res)
    return {
        "items": items,
        "warnings": warnings,
        "as_of": max((r.as_of for r in ok_results), default=""),
        "stale": any(r.stale for r in ok_results),
        "cached": bool(ok_results) and all(r.cached for r in ok_results),
    }
