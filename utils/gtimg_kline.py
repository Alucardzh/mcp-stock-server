#!/usr/bin/env python3
"""腾讯日K（web.ifzq.gtimg.cn，不复权）——fupan_v1 klines 段数据源。

- param 末段为空 = 不复权（响应键 "day"；qfq 时为 "qfqday"）；
- gtimg 非东财限流族，不经 push2 pacing，仅复用 Push2Cache 做缓存；
- 历史 end_date 数据定稿 → TTL 86400；当日 bar 盘中会变 → TTL 120，收盘后
  （≥15:05）当日 bar 已定稿按历史长期缓存——否则整包（TTL 300）先于当日
  kline（TTL 120）过期，每次过期都重打全量批次，易触发 gtimg 反爬 501
  （fupan_v1 的 NOT_READY 闸门在组装层，本层不管盘中语义）。
"""

from datetime import datetime, timedelta
import logging

import pandas as pd
import requests

from .push2_client import CACHE, EmResult, now_iso

logger = logging.getLogger(__name__)

KLINE_SESSION = requests.Session()
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_TODAY_TTL = 120
_HISTORY_TTL = 86400


def MKT_PREFIX(symbol: str) -> str:
    """6 位代码 → 腾讯市场前缀（sh/sz）。"9" 已先于北交分支匹配 → 92 开头实际返回 sh；
    北交 8/4 开头返回 'bj'（支持性以探测为准）。"""
    if symbol.startswith(("6", "68", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("8", "4")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _ttl(end_date: str) -> int:
    if end_date != str(datetime.now().date()):
        return _HISTORY_TTL
    # 收盘后当日 bar 已定稿（fupan 的 NOT_READY 闸门挡住 15:02 前调用）；
    # 盘中保持短 TTL，收盘后按历史长期缓存，避免整包缓存过期引发全量重拉
    now = datetime.now()
    return _HISTORY_TTL if (now.hour, now.minute) >= (15, 5) else _TODAY_TTL


def fetch_kline(symbol: str, end_date: str, bars: int) -> EmResult:
    """单票不复权日K（缓存；data = DataFrame[日期,开盘,收盘,最高,最低,成交量] 升序，
    attrs 带 symbol/name——进缓存前一次性写入，消费方只读）。"""
    key = f"kline:{symbol}:{end_date}:{bars}"

    def _fetch() -> EmResult:
        sym = MKT_PREFIX(symbol)
        start = (
            datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=int(bars * 2))
        ).strftime("%Y-%m-%d")
        try:
            r = KLINE_SESSION.get(
                _KLINE_URL,
                params={"param": f"{sym},day,{start},{end_date},{bars},"},
                timeout=8,
            )
            payload = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("gtimg kline %s failed: %s", symbol, e)
            return EmResult(ok=False, reason="upstream_error")
        data = payload.get("data") if isinstance(payload, dict) else None
        node = (data or {}).get(sym) or {}
        rows = (
            node.get("day") or node.get("qfqday") or []
        )  # 兜底防御性：不复权主键恒为 "day"
        if not rows:
            return EmResult(ok=False, reason="upstream_error")
        df = pd.DataFrame(
            [
                [r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                for r in rows
            ],
            columns=["日期", "开盘", "收盘", "最高", "最低", "成交量"],
        )
        # attrs 在进缓存前一次性写入；消费方只读（缓存 DataFrame 引用共享，T2 审查 Important）
        qt = node.get("qt") or {}
        t = next((v for k, v in qt.items() if str(k).startswith("t__")), None)
        df.attrs["symbol"] = symbol
        df.attrs["name"] = (
            str(t[1]) if isinstance(t, (list, tuple)) and len(t) > 1 else ""
        )
        return EmResult(ok=True, data=df, as_of=now_iso())

    return CACHE.get(key, _ttl(end_date), _fetch)
