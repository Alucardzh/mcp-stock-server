#!/usr/bin/env python3
"""
复盘·市场概况模块：指数概览 + 涨跌结构/涨停生态。

数据源（akshare）：
- index_zh_a_hist          东财指数日行情（收盘后含当日）
- stock_zt_pool_*          东财涨停/炸板/跌停/强势/昨涨停股池
- stock_zh_a_spot_em       东财全市场快照（当日涨跌家数；乐咕接口已失效后的替代）

口径说明：
- 涨跌家数仅当日可算（全市场快照无历史），历史日期在 notes 降级说明。
"""

from datetime import date as date_type, datetime, timedelta
import logging

import pandas as pd

from akshare import (
    index_zh_a_hist,
    stock_zh_a_spot_em,
    stock_zt_pool_dtgc_em,
    stock_zt_pool_em,
    stock_zt_pool_previous_em,
    stock_zt_pool_strong_em,
    stock_zt_pool_zbgc_em,
)

from .review_common import json_err, json_ok, parse_day, prev_trading_days, safe_num
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

REVIEW_INDEXES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000688", "科创50"),
    ("899050", "北证50"),
]

MAX_POOL_ROWS = 60

_spot_cache: CachedData | None = None


def _get_spot() -> pd.DataFrame:
    """全市场快照（缓存 300 秒）"""
    global _spot_cache
    if _spot_cache is not None and not _spot_cache.is_expired():
        return _spot_cache.data
    df = stock_zh_a_spot_em()
    _spot_cache = CachedData(df, ttl=300)
    return df


def indices_section(day: date_type) -> dict:
    """① 指数概览：五大指数收盘/涨跌幅/成交额（亿元）"""
    notes, items = [], []
    start = (day - timedelta(days=14)).strftime("%Y%m%d")
    end = day.strftime("%Y%m%d")
    for code, name in REVIEW_INDEXES:
        try:
            df = index_zh_a_hist(
                symbol=code, period="daily", start_date=start, end_date=end
            )
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}({code}) 行情获取失败: {e}")
            continue
        if df is None or df.empty or str(df["日期"].iloc[-1])[:10] != str(day):
            notes.append(f"{name}({code}) 在 {day} 无数据(可能非交易日)")
            continue
        r = df.iloc[-1]
        items.append(
            {
                "code": code,
                "name": name,
                "close": safe_num(r["收盘"], 2),
                "chg_pct": safe_num(r["涨跌幅"], 2),
                "amount_yi": safe_num(float(r["成交额"]) / 1e8, 0),
            }
        )
    if not items:
        raise ValueError(f"{day} 未获取到任何指数行情(可能非交易日)")
    return {"date": str(day), "items": items, "notes": notes}
