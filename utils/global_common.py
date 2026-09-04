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
