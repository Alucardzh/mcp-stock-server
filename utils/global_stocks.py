#!/usr/bin/env python3
"""
全球个股快照：光模块/存储链等海外对标组股价（Yahoo 经代理，唯一源）。

preset 服务端内置；美股附盘后/盘前涨跌(after_hours_pct)；单只失败该行 error 占位。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging

from .global_common import fetch_yahoo_quote, yahoo_proxy
from .review_common import json_err, json_ok

logger = logging.getLogger(__name__)

PRESETS: dict[str, list[str]] = {
    "ai_chain": [  # AI算力链对标(新易盛/中际旭创映射) + 台积电
        "NVDA", "AVGO", "MRVL", "COHR", "LITE", "APH", "ANET", "2330.TW",
    ],
    "storage": [  # 存储链对标(东芯映射)
        "000660.KS", "005930.KS", "285A.T", "MU", "SNDK",
    ],
    "etf": ["SMH", "AIQ", "BOTZ"],  # AI 情绪代理 ETF
}
PRESETS["all"] = sum((PRESETS[k] for k in ("ai_chain", "storage", "etf")), [])


def _market_of(symbol: str) -> str:
    if symbol.endswith(".KS"):
        return "KR"
    if symbol.endswith(".T"):
        return "JP"
    if symbol.endswith(".TW"):
        return "TW"
    return "US"


def get_stock_global_snapshot(symbols: str = "", preset: str = "ai_chain") -> str:
    """查询全球个股快照（Yahoo 经代理，逐只并行）

    Args:
        symbols: 显式 Yahoo 代码(逗号分隔)，优先于 preset
        preset: ai_chain / storage / etf / all，默认 ai_chain
    """
    try:
        if yahoo_proxy() is None:
            return json_err("未配置 YAHOO_PROXY，本工具不可用(海外个股唯一数据源)")
        s = (symbols or "").strip().replace("，", ",")
        sym_list: list[str] = []
        if s:
            sym_list = [x.strip() for x in s.split(",") if x.strip()]
        else:
            p = (preset or "").strip()
            if p not in PRESETS:
                return json_err(f"preset 仅支持 {'/'.join(k for k in PRESETS if k != 'all')} 或 all，当前: {preset}")
            sym_list = PRESETS[p]

        def fetch(sym: str) -> dict:
            market = _market_of(sym)
            q = fetch_yahoo_quote(sym, include_pre_post=(market == "US"))
            if q is None or q.get("close") is None:
                return {"symbol": sym, "name": "", "market": market,
                        "close": None, "chg_pct": None, "error": "获取失败"}
            return {
                "symbol": sym,
                "name": q["name"],
                "market": market,
                "currency": q["currency"],
                "close": q["close"],
                "chg_pct": q["chg_pct"],
                **({"after_hours_pct": q["after_hours_pct"]}
                   if q.get("after_hours_pct") is not None else {}),
            }

        with ThreadPoolExecutor(max_workers=8) as pool:
            quotes = list(pool.map(fetch, sym_list))
        failed = [q["symbol"] for q in quotes if q.get("close") is None]
        data = {
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quotes": quotes,
            "notes": (["以下代码获取失败(可能非交易时段或代码无效): " + ",".join(failed)]
                      if failed else []),
        }
        return json_ok(data)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_global_snapshot: %s", e)
        return json_err(f"查询全球个股失败: {e}")
