#!/usr/bin/env python3
"""探测腾讯日K接口（本地运行，不走 MCP）：可达性 / 不复权参数 / 北交支持性。

用法：uv run python scripts/probe_gtimg_kline.py
"""
# ruff: noqa: T201
import json
from datetime import datetime, timedelta

import requests

URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
end = "2026-11-11"
start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")


def probe(sym: str, fq: str = ""):
    r = requests.get(
        URL, params={"param": f"{sym},day,{start},{end},10,{fq}"}, timeout=8
    )
    d = (r.json().get("data") or {}).get(sym) or {}
    keys = list(d)
    rows = d.get("day") or d.get("qfqday") or []
    print(
        f"{sym} fq={fq or '空(不复权)'}: keys={keys[:3]} rows={len(rows)} 首末={rows[0][:3] if rows else None}/{rows[-1][:3] if rows else None}"
    )


if __name__ == "__main__":
    probe("sz002384")
    probe("sz002384", "qfq")  # 对照：前复权收盘应与不复权不同（除非近期无除权）
    probe("sh600519")
    probe("bj430047")  # 北交支持性
