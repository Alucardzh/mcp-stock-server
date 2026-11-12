#!/usr/bin/env python3
"""龙虎榜聚合口径（F1-b）：榜净并集 / 席位指纹去重 / 三分类净额（万元）。

口径（spec §5.1，v2 §6.4）：
- 榜净并集：一票多理由上榜不重复计——取**买额最大的那条上榜记录**的买/卖作为
  该票当日榜面 buy/sell，net = buy - sell（自洽；真实数据各记录两侧口径一致时
  等价于 spec 的 max(买)-max(卖)）；
- 席位指纹去重：合并键 = 席位名+买额+卖额（东财偶发整行重复）；每侧取前5；
- 三分类 = 去重后席位行净额（买-卖）按 [机构]/[外资]/[游资] 汇总；
- diff = net - seat_net（榜面 vs 席位前5，口径不同不必然 0，参考值）。
金额单位万元（席位口径与现有 seats 工具一致）；net/buy/sell 为榜面口径（元→万元）。
"""

from datetime import datetime
import logging

import pandas as pd
from akshare import stock_lhb_stock_detail_em

from .lhb import _classify_seat, _fetch_billboard, _norm_billboard
from .push2_client import CACHE, EmResult, now_iso
from .review_common import parse_day, safe_num

logger = logging.getLogger(__name__)

_EIGHTEEN = 18  # 当日 18:00 前席位未定稿


def _ttl(day_str: str) -> int:
    if day_str < str(datetime.now().date()):
        return 86400
    return 300 if datetime.now().hour < _EIGHTEEN else 86400


def _seat_rows(symbol: str, d_compact: str) -> list[tuple]:
    """买/卖两侧原始席位行 [(name, buy_wan|None, sell_wan|None)]，失败侧跳过。"""
    rows = []
    for flag in ("买入", "卖出"):
        try:
            df = stock_lhb_stock_detail_em(symbol=symbol, date=d_compact, flag=flag)
        except Exception as e:  # noqa: BLE001
            logger.warning("seats %s %s failed: %s", symbol, flag, e)
            continue
        if df is None or df.empty:
            continue
        exact = "买入金额" if flag == "买入" else "卖出金额"
        amt_col = (
            exact
            if exact in df.columns
            else next((c for c in df.columns if "金额" in str(c)), None)
        )
        name_col = (
            "营业部名称"
            if "营业部名称" in df.columns
            else next(
                (
                    c
                    for c in df.columns
                    if "营业部" in str(c) or "OPERATEDEPT" in str(c).upper()
                ),
                None,
            )
        )
        if amt_col is None or name_col is None:
            continue
        for _, r in df.head(5).iterrows():  # v2 diff 口径 = 席位前5
            amt = safe_num(pd.to_numeric(r[amt_col], errors="coerce") / 1e4, 4)
            name = str(r[name_col] or "?")
            rows.append((name, amt, None) if flag == "买入" else (name, None, amt))
    return rows


def _aggregate_stocks(day) -> tuple[dict, list[str]]:
    """榜单 → 按股聚合（万元）。返回 (stocks, notes)。"""
    df = _fetch_billboard(day)
    if df is None or df.empty:
        return {}, [f"{day} 无龙虎榜数据(可能非交易日或榜单未出)"]
    by_code: dict[str, dict] = {}
    for r in _norm_billboard(df):
        a = by_code.setdefault(
            r["code"], {"name": r["name"], "recs": [], "reasons": []}
        )
        a["recs"].append((safe_num(r["buy"], 4) or 0.0, safe_num(r["sell"], 4) or 0.0))
        if r["reason"] and r["reason"] not in a["reasons"]:
            a["reasons"].append(r["reason"])
    d_compact = day.strftime("%Y%m%d")
    stocks = {}
    for code, a in by_code.items():
        buy, sell = max(a["recs"], key=lambda p: p[0])  # 并集口径：买额最大的记录
        net = (buy - sell) / 1e4  # 元 → 万元
        rows = _seat_rows(code, d_compact)
        seen, inst, foreign, hot, buy_side_n, sell_side_n = set(), 0.0, 0.0, 0.0, 0, 0
        seat_net = 0.0
        for name, buy_wan, sell_wan in rows:
            fp = (name, buy_wan, sell_wan)
            if fp in seen:  # 指纹去重（席位名+买额+卖额 整行重复剔除）
                continue
            seen.add(fp)
            if buy_wan is not None:
                buy_side_n += 1
            else:
                sell_side_n += 1
            amt = (buy_wan or 0.0) - (sell_wan or 0.0)
            seat_net += amt
            cls = _classify_seat(name)
            if cls == "[机构]":
                inst += amt
            elif cls == "[外资]":
                foreign += amt
            else:
                hot += amt
        stocks[code] = {
            "name": a["name"],
            "net": safe_num(net, 4),
            "buy": safe_num(buy / 1e4, 4),
            "sell": safe_num(sell / 1e4, 4),
            "inst": safe_num(inst, 4),
            "foreign": safe_num(foreign, 4),
            "hot": safe_num(hot, 4),
            "seat_n": buy_side_n + sell_side_n,
            "seat_net": safe_num(seat_net, 4),
            "diff": safe_num(net - seat_net, 4),
            "reasons": a["reasons"],
        }
    return stocks, []


def lhb_aggregate(date: str, symbols: list[str] = []) -> dict:
    """聚合入口（缓存；无 JSON 信封）。symbols 为空 → 全部上榜票。

    cats/total_net 恒为全榜口径（不随 symbols 过滤缩水）；stocks 才按 symbols 过滤。
    """
    day = parse_day(date)
    day_str = str(day)

    def _fetch() -> EmResult:
        stocks, notes = _aggregate_stocks(day)
        if not stocks:
            # 空榜单不缓存（榜单可能稍后发布）；notes 经 data 透传，区分非交易日与上游失败
            return EmResult(ok=False, reason="no_data", data={"notes": notes})
        return EmResult(
            ok=True, data={"stocks": stocks, "notes": notes}, as_of=now_iso()
        )

    res = CACHE.get(f"lhb_agg:{day_str}", _ttl(day_str), _fetch)
    if res.ok:
        all_stocks = (res.data or {}).get("stocks", {})
        notes = (res.data or {}).get("notes", [])
    else:
        all_stocks = {}
        notes = (res.data or {}).get("notes") or [f"龙虎榜聚合失败: {res.reason}"]
    cats = {
        "inst": safe_num(sum(v["inst"] or 0 for v in all_stocks.values()), 4),
        "foreign": safe_num(sum(v["foreign"] or 0 for v in all_stocks.values()), 4),
        "hot": safe_num(sum(v["hot"] or 0 for v in all_stocks.values()), 4),
    }
    total_net = (
        safe_num(sum(v["net"] or 0 for v in all_stocks.values()), 4)
        if all_stocks
        else None
    )
    stocks = all_stocks
    if symbols:
        wanted = {s.strip() for s in symbols}
        stocks = {c: v for c, v in all_stocks.items() if c in wanted}
    return {
        "date": day_str,
        "stocks": stocks,
        "cats": cats,
        "total_net": total_net,
        "notes": notes,
        "as_of": res.as_of if res.ok else "",
        "cached": res.cached,
        "stale": res.stale,
        "source": "eastmoney_datacenter",
    }
