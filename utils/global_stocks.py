#!/usr/bin/env python3
"""
全球个股快照：光模块/存储链等海外对标组股价。

主源 = 腾讯批量（美股 us+代码 / 日股 jp+代码 / 韩股 kr+6位代码，一次 HTTP 拉全部，
直连零积分）；Yahoo(经代理)仅兜底腾讯不可映射(.TW)或返回空的代码；
after_hours_pct 仅 Yahoo 路径提供（腾讯美股无盘后字段）。
"""

import logging
import time
from datetime import datetime

from .global_common import fetch_tencent_quotes, fetch_yahoo_quote, yahoo_proxies
from .review_common import json_err, json_ok
from .tools import CachedData

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

_CURRENCY = {"US": "USD", "KR": "KRW", "JP": "JPY", "TW": "TWD"}

_result_cache: dict[str, CachedData] = {}


def _market_of(symbol: str) -> str:
    if symbol.endswith(".KS"):
        return "KR"
    if symbol.endswith(".T"):
        return "JP"
    if symbol.endswith(".TW"):
        return "TW"
    return "US"


def _tencent_code(symbol: str, market: str) -> str | None:
    """Yahoo语法 -> 腾讯代码；台湾等不支持的市场返回 None"""
    if market == "US":
        return f"us{symbol}"
    if market == "KR":
        return f"kr{symbol.split('.')[0].zfill(6)}"
    if market == "JP":
        return f"jp{symbol.split('.')[0]}"
    return None


def get_stock_global_snapshot(symbols: str = "", preset: str = "ai_chain") -> str:
    """查询全球个股快照（腾讯批量为主源，Yahoo经代理兜底）

    Args:
        symbols: 显式代码(逗号分隔，Yahoo语法如 "NVDA,000660.KS")，优先于 preset
        preset: ai_chain / storage / etf / all，默认 ai_chain
    """
    try:
        s = (symbols or "").strip().replace("，", ",")
        if s:
            sym_list = [x.strip() for x in s.split(",") if x.strip()]
        else:
            p = (preset or "").strip()
            if p not in PRESETS:
                return json_err(
                    f"preset 仅支持 {'/'.join(k for k in PRESETS if k != 'all')} 或 all，当前: {preset}"
                )
            sym_list = PRESETS[p]
        if not sym_list:
            return json_err("未指定任何有效代码")
        key = "|".join(sym_list)
        cached = _result_cache.get(key)
        if cached is not None and not cached.is_expired():
            return cached.data

        markets = {sym: _market_of(sym) for sym in sym_list}
        tc_map = {sym: _tencent_code(sym, markets[sym]) for sym in sym_list}
        tencent_syms = [sym for sym in sym_list if tc_map[sym]]
        batch = fetch_tencent_quotes([tc_map[sym] for sym in tencent_syms]) if tencent_syms else {}

        quotes, notes = [], []
        yahoo_needed = []
        for sym in sym_list:
            tq = batch.get(tc_map[sym]) if tc_map[sym] else None
            if tq and tq.get("price"):
                quotes.append(
                    {
                        "symbol": sym,
                        "name": tq["name"],
                        "market": markets[sym],
                        "currency": _CURRENCY[markets[sym]],
                        "close": tq["price"],
                        "chg_pct": tq["chg_pct"],
                    }
                )
            else:
                yahoo_needed.append(sym)

        if yahoo_needed:
            has_proxy = yahoo_proxies() is not None
            if not has_proxy:
                notes.append(
                    "无可用Yahoo通道(YAHOO_PROXY未配置且akproxy授权失败), "
                    "以下代码无法走Yahoo兜底: " + ",".join(yahoo_needed)
                )
            for i, sym in enumerate(yahoo_needed):
                row = {
                    "symbol": sym, "name": "", "market": markets[sym],
                    "currency": _CURRENCY[markets[sym]],
                    "close": None, "chg_pct": None,
                }
                if has_proxy:
                    if i > 0:
                        time.sleep(0.4)  # 兜底串行限速, 降低429概率
                    q = fetch_yahoo_quote(sym, include_pre_post=(markets[sym] == "US"))
                    if q and q.get("close") is not None:
                        row.update(
                            {
                                "name": q["name"], "close": q["close"],
                                "chg_pct": q["chg_pct"],
                            }
                        )
                        if q.get("after_hours_pct") is not None:
                            row["after_hours_pct"] = q["after_hours_pct"]
                    else:
                        row["error"] = "获取失败(腾讯与Yahoo均不可用)"
                else:
                    row["error"] = "腾讯不支持且无可用Yahoo通道"
                quotes.append(row)

        failed = [q["symbol"] for q in quotes if q.get("close") is None]
        if failed:
            notes.append("以下代码获取失败: " + ",".join(failed))
        data = {
            "as_of": datetime.now().astimezone().isoformat(timespec="seconds"),
            "quotes": quotes,
            "notes": notes,
        }
        out = json_ok(data)
        _result_cache[key] = CachedData(out, ttl=300)
        return out
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_global_snapshot: %s", e)
        return json_err(f"查询全球个股失败: {e}")
