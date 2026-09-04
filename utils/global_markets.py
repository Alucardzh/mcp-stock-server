#!/usr/bin/env python3
"""
全球市场快照模块：美股/美债/汇率/亚太/商品/恐慌 六组数据 section。

主源全部国内直连（腾讯批量/新浪SOX/东财全球指数走proxy1次/东财期货直连/
东财datacenter直连/MOF直连）；Yahoo(经代理)仅作备源；服务不依赖代理存活。
"""

import logging

import pandas as pd
from akshare import (
    fx_spot_quote,
    futures_global_spot_em,
    index_global_spot_em,
    index_us_stock_sina,
)

from .global_common import fetch_jgb_yields, fetch_tencent_quotes, fetch_yahoo_quote
from .global_rates import us_treasury_section
from .review_common import safe_num
from .tools import CachedData

logger = logging.getLogger(__name__)

# 腾讯一次批量拉满五码：美股三大 + VIX + 恒生科技（跨组共享，缓存60s）
_TENCENT_CODES = ["usIXIC", "usINX", "usDJI", "usVIX", "hkHSTECH"]
_tencent_cache: CachedData | None = None
_em_global_cache: CachedData | None = None
_futures_cache: CachedData | None = None


def _tencent_batch() -> dict[str, dict | None]:
    global _tencent_cache
    if _tencent_cache is not None and not _tencent_cache.is_expired():
        return _tencent_cache.data
    out = fetch_tencent_quotes(_TENCENT_CODES)
    _tencent_cache = CachedData(out, ttl=60)
    return out


def _em_global_indexes() -> pd.DataFrame | None:
    global _em_global_cache
    if _em_global_cache is not None and not _em_global_cache.is_expired():
        return _em_global_cache.data
    try:
        df = index_global_spot_em()
    except Exception as e:  # noqa: BLE001
        logger.warning("index_global_spot_em failed: %s", e)
        df = None
    _em_global_cache = CachedData(df, ttl=60)
    return df


def _em_index(kw: str, name: str) -> dict | None:
    df = _em_global_indexes()
    if df is None:
        return None
    hit = df[df["名称"].astype(str).str.contains(kw, na=False)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"name": name, "close": safe_num(r["最新价"], 2), "chg_pct": safe_num(r["涨跌幅"], 2)}


def _futures_df() -> pd.DataFrame | None:
    global _futures_cache
    if _futures_cache is not None and not _futures_cache.is_expired():
        return _futures_cache.data
    try:
        df = futures_global_spot_em()
    except Exception as e:  # noqa: BLE001
        logger.warning("futures_global_spot_em failed: %s", e)
        df = None
    _futures_cache = CachedData(df, ttl=60)
    return df


def _future_main(kw: str, name: str) -> dict | None:
    df = _futures_df()
    if df is None:
        return None
    hit = df[
        df["名称"].astype(str).str.contains(kw, na=False) & df["最新价"].notna()
    ]
    if hit.empty:
        return None
    r = hit.loc[pd.to_numeric(hit["成交量"], errors="coerce").idxmax()]
    return {"name": name, "price": safe_num(r["最新价"], 2), "chg_pct": safe_num(r["涨跌幅"], 2)}


def _tq_item(batch: dict, code: str, name: str, yahoo_symbol: str) -> dict:
    """腾讯条目 -> 统一格式；腾讯失败降级 Yahoo"""
    q = batch.get(code)
    if q:
        return {"name": name, "close": safe_num(q["price"], 2), "chg_pct": q["chg_pct"]}
    y = fetch_yahoo_quote(yahoo_symbol)
    if y and y.get("close") is not None:
        return {"name": name, "close": y["close"], "chg_pct": y["chg_pct"]}
    return {"name": name, "close": None, "chg_pct": None, "error": "腾讯与Yahoo均失败"}


def _sox() -> dict:
    """费城半导体：新浪日K主源 -> Yahoo 备源"""
    try:
        df = index_us_stock_sina(symbol=".SOX")
        if df is not None and len(df) >= 2:
            px, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
            return {
                "name": "费城半导体",
                "close": safe_num(px, 2),
                "chg_pct": round((px / prev - 1) * 100, 2),
                "note": f"新浪日K {df['date'].iloc[-1]}",
            }
    except Exception as e:  # noqa: BLE001
        logger.warning("sina SOX failed: %s", e)
    y = fetch_yahoo_quote("^SOX")
    if y and y.get("close") is not None:
        return {"name": "费城半导体", "close": y["close"], "chg_pct": y["chg_pct"]}
    return {"name": "费城半导体", "close": None, "chg_pct": None, "error": "新浪与Yahoo均失败"}


def us_equities_section() -> dict:
    """美股组：纳指/标普/道指(腾讯批量) + 费半(新浪)"""
    batch = _tencent_batch()
    indexes = [
        _tq_item(batch, "usIXIC", "纳斯达克", "^IXIC"),
        _tq_item(batch, "usINX", "标普500", "^GSPC"),
        _tq_item(batch, "usDJI", "道琼斯", "^DJI"),
        _sox(),
    ]
    notes = []
    if any(i.get("close") is None for i in indexes):
        notes.append("部分美股指数降级或失败, 见 error 字段")
    return {"indexes": indexes, "note": "隔夜美股收盘", "notes": notes}


def fx_section() -> dict:
    """汇率组：DXY(东财) + USDCNH(Yahoo; 降级在岸USD/CNY近似)"""
    notes = []
    dxy = _em_index("美元指数", "美元指数")
    y = fetch_yahoo_quote("USDCNH=X")
    usdcnh = chg = None
    if y and y.get("close") is not None:
        usdcnh, chg = y["close"], y["chg_pct"]
    else:
        try:
            fx = fx_spot_quote()
            row = fx[fx["货币对"].astype(str) == "USD/CNY"].iloc[0]
            usdcnh = safe_num(row["买报价"], 4)
            chg = None
            notes.append("离岸CNH不可用, 以在岸USD/CNY近似")
        except Exception as e:  # noqa: BLE001
            notes.append(f"USDCNH获取失败: {e}")
    return {
        "dxy": dxy["close"] if dxy else None,
        "dxy_chg_pct": dxy["chg_pct"] if dxy else None,
        "usdcnh": usdcnh,
        "usdcnh_chg_pct": chg,
        "notes": notes,
    }


def asia_section() -> dict:
    """亚太组：日经/KOSPI(东财) + 恒生科技(腾讯)"""
    batch = _tencent_batch()
    indexes, notes = [], []
    for kw, name, ysym in (("日经", "日经225", "^N225"), ("KOSPI", "KOSPI", "^KS11")):
        item = _em_index(kw, name)
        if item is None:
            y = fetch_yahoo_quote(ysym)
            if y and y.get("close") is not None:
                item = {"name": name, "close": y["close"], "chg_pct": y["chg_pct"]}
                notes.append(f"{name} 东财降级Yahoo")
            else:
                item = {"name": name, "close": None, "chg_pct": None, "error": "东财与Yahoo均失败"}
        indexes.append(item)
    indexes.append(_tq_item(batch, "hkHSTECH", "恒生科技", "^HSTECH"))
    return {"indexes": indexes, "note": "亚太盘中或收盘", "notes": notes}


def commodities_section() -> dict:
    """商品组：布伦特/WTI/COMEX黄金 主力合约(成交量最大)"""
    notes = []
    out = {}
    for key, kw, name, ysym in (
        ("brent", "布伦特", "布伦特原油", "BZ=F"),
        ("wti", "NYMEX原油", "WTI原油", "CL=F"),
        ("gold", "COMEX黄金", "COMEX黄金", "GC=F"),
    ):
        item = _future_main(kw, name)
        if item is None:
            y = fetch_yahoo_quote(ysym)
            if y and y.get("close") is not None:
                item = {"name": name, "price": y["close"], "chg_pct": y["chg_pct"]}
                notes.append(f"{name} 东财降级Yahoo")
            else:
                item = {"name": name, "price": None, "chg_pct": None, "error": "东财与Yahoo均失败"}
        out[key] = item
    out["notes"] = notes
    return out


def fear_section() -> dict:
    """恐慌组：VIX(腾讯) -> Yahoo 备源"""
    batch = _tencent_batch()
    q = batch.get("usVIX")
    if q:
        return {"vix": safe_num(q["price"], 2), "vix_chg_pct": q["chg_pct"], "notes": []}
    y = fetch_yahoo_quote("^VIX")
    if y and y.get("close") is not None:
        return {"vix": y["close"], "vix_chg_pct": y["chg_pct"], "notes": ["VIX降级Yahoo"]}
    return {"vix": None, "vix_chg_pct": None, "notes": ["VIX获取失败(腾讯与Yahoo)"]}


def bond_group_section() -> dict:
    """美债组：美债(东财datacenter) + 日债(MOF三级链)，各自独立降级"""
    out, notes = {}, []
    try:
        us = us_treasury_section()
        notes += [f"[us] {n}" for n in us.pop("notes", [])]
        out.update(us)
    except Exception as e:  # noqa: BLE001
        out.update({"us10y": None, "us10y_chg_bp": None, "us30y": None, "us30y_chg_bp": None})
        notes.append(f"[us] 失败: {e}")
    try:
        jp = fetch_jgb_yields()
        notes += [f"[japan] {n}" for n in jp.pop("notes", [])]
        out.update(jp)
    except Exception as e:  # noqa: BLE001
        out.update({"japan10y": None, "japan10y_chg_bp": None, "japan20y": None, "japan30y": None})
        notes.append(f"[japan] 失败: {e}")
    if out.get("us10y") is None and out.get("japan10y") is None:
        raise ValueError("美债与日债均失败")
    out["notes"] = notes
    return out
