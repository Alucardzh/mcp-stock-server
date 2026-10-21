#!/usr/bin/env python3
"""
龙虎榜：榜单汇总（efinance 主 / akshare 备）+ 席位明细（akshare）。
金额：榜单输出亿元，席位输出万元。当日榜单约 17:00 后陆续齐全。
"""

import logging

import pandas as pd

from akshare import stock_lhb_detail_em, stock_lhb_stock_detail_em

from .ef import ef_daily_billboard
from .review_common import json_err, json_ok, parse_day, safe_num
from .tools import RateLimiter, with_retry

logger = logging.getLogger(__name__)


def _norm_billboard(df: pd.DataFrame) -> list[dict]:
    """efinance(中文列) / akshare(英文列) 两种榜单归一。"""
    rows = []
    for _, r in df.iterrows():
        code = r.get("股票代码", r.get("SECURITY_CODE"))
        if code is None or str(code) == "nan":
            continue
        rows.append(
            {
                "code": str(code),
                "name": str(r.get("股票名称", r.get("SECURITY_NAME_ABBR")) or ""),
                "net": r.get("龙虎榜净买额", r.get("BILLBOARD_NET_AMT")),
                "buy": r.get("龙虎榜买入额", r.get("BILLBOARD_BUY_AMT")),
                "sell": r.get("龙虎榜卖出额", r.get("BILLBOARD_SELL_AMT")),
                "explain": str(r.get("解读", r.get("EXPLAIN")) or ""),
                "reason": str(r.get("上榜原因", r.get("EXPLANATION")) or ""),
            }
        )
    return rows


def _fetch_billboard(day):
    d_dash, d_compact = str(day), day.strftime("%Y%m%d")
    try:
        df = ef_daily_billboard(d_dash, d_dash)
        if df is not None and not df.empty:
            return df
    except Exception as e:  # noqa: BLE001
        logger.warning("efinance billboard failed: %s", e)
    try:
        return stock_lhb_detail_em(start_date=d_compact, end_date=d_compact)
    except Exception as e:  # noqa: BLE001
        logger.warning("akshare billboard failed: %s", e)
        return None


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_lhb(date: str = "") -> str:
    """查询龙虎榜榜单（按股聚合净买卖、上榜原因与解读；金额亿元）

    Args:
        date: YYYY-MM-DD 默认今天。当日榜单约17:00后陆续齐全
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        df = _fetch_billboard(day)
        if df is None or df.empty:
            return json_err(f"{day} 无龙虎榜数据(可能非交易日或榜单未出)")
        agg: dict[str, dict] = {}
        for r in _norm_billboard(df):
            a = agg.setdefault(
                r["code"],
                {
                    "code": r["code"],
                    "name": r["name"],
                    "net": 0.0,
                    "buy": 0.0,
                    "sell": 0.0,
                    "explain": r["explain"],
                    "reasons": [],
                },
            )
            a["net"] += safe_num(r["net"], 4) or 0.0
            a["buy"] += safe_num(r["buy"], 4) or 0.0
            a["sell"] += safe_num(r["sell"], 4) or 0.0
            if r["reason"] and r["reason"] not in a["reasons"]:
                a["reasons"].append(r["reason"])
        items = []
        for a in agg.values():
            a["net"] = safe_num(a["net"] / 1e8)
            a["buy"] = safe_num(a["buy"] / 1e8)
            a["sell"] = safe_num(a["sell"] / 1e8)
            items.append(a)
        ranked = sorted(items, key=lambda x: -(x["net"] if x["net"] is not None else 0))
        return json_ok(
            {
                "date": str(day),
                "count": len(items),
                "net_buy_top10": ranked[:10],
                "net_sell_top5": ranked[-5:][::-1],
                "items": items,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_lhb: %s", e)
        return json_err(f"查询龙虎榜失败: {e}")
