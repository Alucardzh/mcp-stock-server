#!/usr/bin/env python3
"""
全球市场模块共享 fetcher：腾讯批量行情、Yahoo(经代理)、MOF日债(三级链)。

代理原则：YAHOO_PROXY 未配置或不可达时，所有 Yahoo 路径返回 None，
由调用方降级到国内直连源——服务不依赖代理存活。
"""

import logging
import os
import re
from datetime import date as date_type
from pathlib import Path

import pandas as pd
import requests as std_requests
from curl_cffi import requests as cr_requests

from .review_common import safe_num
from .tools import CachedData

logger = logging.getLogger(__name__)

TENCENT_URL = "https://qt.gtimg.cn/q={codes}"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
MOF_MONTHLY_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
MOF_ALL_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)
MOF_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "global"


def yahoo_proxy() -> dict | None:
    """YAHOO_PROXY 环境变量 -> requests proxies dict；未配置返回 None"""
    p = (os.getenv("YAHOO_PROXY") or "").strip()
    return {"http": p, "https": p} if p else None


def fetch_tencent_quotes(codes: list[str]) -> dict[str, dict | None]:
    """腾讯批量行情(GBK)；单代码失败返回 None，不抛异常"""
    out = {c: None for c in codes}
    try:
        r = std_requests.get(TENCENT_URL.format(codes=",".join(codes)), timeout=8)
        if r.status_code != 200:
            return out
        text = r.content.decode("gbk", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("tencent quotes failed: %s", e)
        return out
    for code in codes:
        m = re.search(rf'v_{code}="([^"]*)"', text)
        if not m:
            continue
        f = m.group(1).split("~")
        if len(f) < 33:
            continue
        try:
            out[code] = {
                "name": f[1],
                "price": float(f[3]),
                "prev_close": float(f[4]),
                "chg_pct": safe_num(f[32], 2),
            }
        except (ValueError, IndexError):
            continue
    return out


def fetch_yahoo_quote(symbol: str, include_pre_post: bool = False) -> dict | None:
    """Yahoo 单只行情(必须经 YAHOO_PROXY，curl_cffi 浏览器指纹)；
    未配置代理/请求失败/非200 一律返回 None 由调用方降级"""
    proxies = yahoo_proxy()
    if proxies is None:
        return None
    params = {"range": "5d", "interval": "1d"}
    if include_pre_post:
        params["includePrePost"] = "true"
    try:
        r = cr_requests.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params=params,
            impersonate="chrome",
            timeout=8,
            proxies=proxies,
        )
        if r.status_code != 200:
            return None
        meta = r.json()["chart"]["result"][0]["meta"]
    except Exception as e:  # noqa: BLE001
        logger.warning("yahoo %s failed: %s", symbol, e)
        return None
    px = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    quote = {
        "symbol": symbol,
        "name": meta.get("shortName", ""),
        "currency": meta.get("currency", ""),
        "close": safe_num(px, 4),
        "chg_pct": round((px / prev - 1) * 100, 2) if px and prev else None,
    }
    if include_pre_post:
        post = meta.get("postMarketPrice")
        pre = meta.get("preMarketPrice")
        if post and px:
            quote["after_hours_pct"] = round((post / px - 1) * 100, 2)
        elif pre and px:
            quote["after_hours_pct"] = round((pre / px - 1) * 100, 2)
    return quote


_JGB_TENORS = [
    "1Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y",
    "10Y", "15Y", "20Y", "25Y", "30Y", "40Y",
]

_monthly_cache: CachedData | None = None  # 当月日债 DataFrame，TTL 3600s


def _mof_get_text(url: str, use_proxy: bool = False) -> str | None:
    """MOF CSV 文本；直连或经 YAHOO_PROXY；失败返回 None"""
    proxies = yahoo_proxy() if use_proxy else None
    if use_proxy and proxies is None:
        return None
    try:
        r = std_requests.get(
            url, timeout=15, proxies=proxies,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code == 200 and len(r.content) > 100:
            return r.content.decode("cp932", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.warning("MOF fetch failed(%s): %s", "proxy" if use_proxy else "direct", e)
    return None


def _parse_jgb_csv(text: str) -> pd.DataFrame | None:
    """解析 MOF 收益率 CSV：只取 `YYYY/M/D,...` 数据行（16 列），忽略表头/注释行"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})\s*,", s)
        if not m:
            continue
        vals = [v.strip() for v in s.split(",")]
        if len(vals) < 16:
            continue
        try:
            d = date_type(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        rows.append([d] + [pd.to_numeric(v, errors="coerce") for v in vals[1:16]])
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["date"] + _JGB_TENORS)


def _monthly_df() -> pd.DataFrame | None:
    """当月日债数据（直连→代理；缓存 3600s）"""
    global _monthly_cache
    if _monthly_cache is not None and not _monthly_cache.is_expired():
        return _monthly_cache.data
    text = _mof_get_text(MOF_MONTHLY_URL) or _mof_get_text(
        MOF_MONTHLY_URL, use_proxy=True
    )
    df = _parse_jgb_csv(text) if text else None
    _monthly_cache = CachedData(df, ttl=3600)
    return df


def _load_jgb_local() -> pd.DataFrame | None:
    """本地全历史缓存；文件缺失时按 直连→代理 下载并落盘（存在即不重复下载）"""
    p = MOF_CACHE_DIR / "jgbcme_all.csv"
    if not p.exists():
        text = _mof_get_text(MOF_ALL_URL) or _mof_get_text(MOF_ALL_URL, use_proxy=True)
        if text is None:
            return None
        df = _parse_jgb_csv(text)
        if df is None or df.empty:
            return None  # 非 CSV 内容(如代理错误页)不落盘, 避免永久污染本地缓存
        try:
            MOF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="cp932", errors="replace")
        except (OSError, UnicodeEncodeError) as e:  # noqa: BLE001 磁盘/编码问题不阻断返回
            logger.warning("MOF cache write failed: %s", e)
        return df
    try:
        return _parse_jgb_csv(p.read_text(encoding="cp932"))
    except Exception as e:  # noqa: BLE001
        logger.warning("MOF cache read failed: %s", e)
        return None


def fetch_jgb_yields() -> dict:
    """日本国债收益率最新值：月文件(直连→代理) → 本地全历史缓存 三级链"""
    df = _monthly_df()
    note = None
    if df is None or df.empty:
        df = _load_jgb_local()
        note = "使用本地缓存数据(数据日期见 date 字段)"
    if df is None or df.empty:
        raise ValueError("MOF日债数据不可用(直连/代理/本地缓存均失败)")

    last_day = df["date"].max()
    prev_series = df[df["date"] < last_day]["date"]
    prev_day = prev_series.max() if not prev_series.empty else None

    def _yield_on(d: date_type) -> pd.Series:
        return df[df["date"] == d].iloc[-1]

    r = _yield_on(last_day)
    p = _yield_on(prev_day) if prev_day is not None else None

    def _bp(tenor: str):
        cur, pre = r[tenor], (p[tenor] if p is not None else None)
        if pd.notna(cur) and pre is not None and pd.notna(pre):
            return round((cur - pre) * 100, 1)
        return None

    out = {
        "date": str(last_day),
        "japan10y": safe_num(r["10Y"], 3),
        "japan10y_chg_bp": _bp("10Y"),
        "japan20y": safe_num(r["20Y"], 3),
        "japan30y": safe_num(r["30Y"], 3),
        "notes": (
            ([note] if note else [])
            + ["日本财务省(MOF)基准收益率, T+0日频"]
        ),
    }
    return out


def jgb_yield_on(day: date_type) -> dict | None:
    """指定日期的日债 10Y/20Y/30Y：当月走月文件，更早走本地全历史"""
    df = _monthly_df()
    if df is not None and not df.empty:
        hit = df[df["date"] == day]
        if not hit.empty:
            r = hit.iloc[-1]
            return {
                "date": str(day), "y10": safe_num(r["10Y"], 3),
                "y20": safe_num(r["20Y"], 3), "y30": safe_num(r["30Y"], 3),
            }
    local = _load_jgb_local()
    if local is None or local.empty:
        return None
    hit = local[local["date"] == day]
    if hit.empty:
        return None
    r = hit.iloc[-1]
    return {
        "date": str(day), "y10": safe_num(r["10Y"], 3),
        "y20": safe_num(r["20Y"], 3), "y30": safe_num(r["30Y"], 3),
    }
