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


# 延迟绑定：lambda 在调用时从模块全局取函数，保证测试 monkeypatch 可生效
POOL_FETCHERS = {
    "涨停": lambda date: stock_zt_pool_em(date=date),
    "炸板": lambda date: stock_zt_pool_zbgc_em(date=date),
    "跌停": lambda date: stock_zt_pool_dtgc_em(date=date),
    "强势": lambda date: stock_zt_pool_strong_em(date=date),
    "昨涨停": lambda date: stock_zt_pool_previous_em(date=date),
}

MONEY_FIELDS = {"成交额", "封板资金", "封单资金", "板上成交额", "流通市值", "总市值"}

_POOL_FIELDS = {
    "涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封板资金",
             "炸板次数", "涨停统计", "连板数", "所属行业"],
    "炸板": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "炸板次数",
             "涨停统计", "所属行业"],
    "跌停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "封单资金",
             "板上成交额", "连续跌停", "开板次数", "所属行业"],
    "强势": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
             "连板数", "所属行业"],
    "昨涨停": ["代码", "名称", "涨跌幅", "最新价", "成交额", "换手率", "涨停统计",
               "连板数", "所属行业"],
}


def _pool_rows(pool: str, day: date_type) -> pd.DataFrame | None:
    fetcher = POOL_FETCHERS[pool]
    try:
        return fetcher(date=day.strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 股池(%s) failed: %s", pool, day, e)
        return None


def _pool_with_fallback(
    pool: str, day: date_type
) -> tuple[pd.DataFrame | None, date_type, list[str]]:
    notes = []
    for d in [day, *prev_trading_days(day, 5)]:
        df = _pool_rows(pool, d)
        if df is not None and not df.empty:
            if d != day:
                notes.append(f"{day} 无数据，返回最近交易日 {d} 的{pool}股池")
            return df, d, notes
    return None, day, notes


def breadth_section(day: date_type) -> dict:
    """② 涨跌结构：涨跌家数(仅当日) + 涨停/跌停/炸板家数 + 连板梯队"""
    notes = []
    out = {
        "date": str(day),
        "up": None,
        "down": None,
        "flat": None,
        "limit_up": None,
        "limit_down": None,
        "zhaban": None,
        "max_lianban": None,
        "lianban_dist": None,
    }
    zt = _pool_rows("涨停", day)
    if zt is None or zt.empty:
        notes.append(f"{day} 无涨停池数据(可能非交易日)")
    else:
        out["limit_up"] = len(zt)
        lianban = pd.to_numeric(zt.get("连板数"), errors="coerce")
        out["max_lianban"] = int(lianban.max()) if lianban.notna().any() else 0
        out["lianban_dist"] = {
            f"{int(k)}板": int(v)
            for k, v in lianban.value_counts().items()
            if k >= 2
        }
    for key, pool in (("zhaban", "炸板"), ("limit_down", "跌停")):
        df = _pool_rows(pool, day)
        if df is not None:
            out[key] = 0 if df.empty else len(df)
    if day == datetime.now().date():
        try:
            spot = _get_spot()
            chg = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            out["up"] = int((chg > 0).sum())
            out["down"] = int((chg < 0).sum())
            out["flat"] = int((chg == 0).sum())
        except Exception as e:  # noqa: BLE001
            notes.append(f"全市场快照获取失败，涨跌家数缺失: {e}")
    else:
        notes.append("涨跌家数仅支持当日(全市场快照无历史)")
    # 空数据(None=拉取失败或 0 行)视为无数据：非交易日三大池应全空
    if out["limit_up"] is None and not out["zhaban"] and not out["limit_down"]:
        raise ValueError(f"{day} 涨停/炸板/跌停池均无数据(可能非交易日)")
    return {**out, "notes": notes}


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_market_breadth(date: str = "") -> str:
    """查询市场涨跌结构：涨跌家数(仅当日)、涨停/跌停/炸板家数、连板梯队

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时涨跌家数不可用(降级说明)。
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        return json_ok(breadth_section(day))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_market_breadth: %s", e)
        return json_err(f"查询涨跌结构失败: {e}")


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_zt_pool(pool: str = "涨停", date: str = "") -> str:
    """查询涨停生态股池明细（金额字段单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        key = (pool or "").strip()
        if key not in POOL_FETCHERS:
            return json_err(f"pool 仅支持 {'/'.join(POOL_FETCHERS)}，当前: {pool}")
        df, actual_day, notes = _pool_with_fallback(key, day)
        if df is None or df.empty:
            return json_err(f"{day} 前后数个交易日内无{key}股池数据")
        fields = [c for c in _POOL_FIELDS[key] if c in df.columns]
        rows = []
        for _, r in df.head(MAX_POOL_ROWS).iterrows():
            row = {}
            for c in fields:
                if c in MONEY_FIELDS:
                    row[c] = safe_num(float(r[c]) / 1e8, 2)
                elif c in ("涨跌幅", "换手率"):
                    row[c] = safe_num(r[c], 2)
                else:
                    row[c] = r[c]
            rows.append(row)
        return json_ok(
            {
                "date": str(actual_day),
                "pool": key,
                "count": len(df),
                "items": rows,
                "notes": notes,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_zt_pool: %s", e)
        return json_err(f"查询{pool}股池失败: {e}")
