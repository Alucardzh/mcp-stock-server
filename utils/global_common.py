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
