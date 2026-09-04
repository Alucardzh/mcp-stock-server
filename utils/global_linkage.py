#!/usr/bin/env python3
"""
共振复盘：隔夜全球(美股/美债/日债) vs 当日 A 股对照表（薄逻辑，判读留给 agent）。

隔夜腿 = 目标日前一交易日；A股腿复用现有 review sections；
板块资金流(电子/通信)依赖当日口径，历史日期自动降级为 null+note。
"""

from datetime import date as date_type
import logging

from akshare import index_us_stock_sina

from .global_common import jgb_yield_on
from .global_rates import us_treasury_section
from .review_common import json_err, json_ok, parse_day, prev_trading_days, safe_num
from .review_funds import sector_fund_flow_section
from .review_market import breadth_section, indices_section

logger = logging.getLogger(__name__)


def _us_pct(symbol: str, day: date_type) -> float | None:
    """新浪日K中目标日收盘涨跌幅（在完整序列里取目标行与其前一行）"""
    try:
        df = index_us_stock_sina(symbol=symbol)
        hit = df.index[df["date"].astype(str) == str(day)]
        if len(hit) == 0 or hit[-1] == df.index[0]:
            return None
        i = hit[-1]
        px, prev = float(df.loc[i, "close"]), float(df.loc[i - 1, "close"])
        return round((px / prev - 1) * 100, 2)
    except Exception as e:  # noqa: BLE001
        logger.warning("sina %s(%s) failed: %s", symbol, day, e)
        return None


def _sector_flow(day: date_type, keywords: tuple[str, ...]) -> list[dict]:
    """电子/通信板块主力净流入(亿元)；从 sector 全表 top5+bottom5 里找"""
    try:
        sec = sector_fund_flow_section(day, "今日")
        rows = (sec.get("top5") or []) + (sec.get("bottom5") or [])
        return [r for r in rows if any(k in str(r.get("name", "")) for k in keywords)]
    except Exception as e:  # noqa: BLE001
        logger.warning("sector flow failed: %s", e)
        return []


def get_global_linkage_review(date: str = "") -> str:
    """共振复盘：输入 A 股交易日，输出 隔夜全球 vs 当日 A 股对照

    Args:
        date: A股交易日 YYYY-MM-DD，默认今天
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        prev = prev_trading_days(day, 1)[0]
        prev2 = prev_trading_days(prev, 1)[0]

        overnight, notes = {}, []
        overnight["nasdaq_pct"] = _us_pct(".IXIC", prev)
        overnight["sox_pct"] = _us_pct(".SOX", prev)
        try:
            us = us_treasury_section(prev)
            overnight["us10y_chg_bp"] = us.get("us10y_chg_bp")
        except Exception as e:  # noqa: BLE001
            overnight["us10y_chg_bp"] = None
            notes.append(f"[us10y] 失败: {e}")
        j_prev = jgb_yield_on(prev)
        j_prev2 = jgb_yield_on(prev2)
        if j_prev:
            overnight["japan10y"] = j_prev["y10"]
            overnight["japan10y_chg_bp"] = (
                round((j_prev["y10"] - j_prev2["y10"]) * 100, 1)
                if j_prev2 and j_prev2.get("y10") is not None
                else None
            )
        else:
            overnight["japan10y"] = None
            notes.append("日债历史值不可用")
        overnight["vix"] = None
        notes.append("VIX无历史序列源, 隔夜值见 get_global_markets 恐慌组")

        a_share = {}
        try:
            idx = indices_section(day)
            items = {i["name"]: i["chg_pct"] for i in idx.get("items", [])}
            a_share["sse_pct"] = items.get("上证指数")
            a_share["chiNext_pct"] = items.get("创业板指")
        except Exception as e:  # noqa: BLE001
            a_share["sse_pct"] = a_share["chiNext_pct"] = None
            notes.append(f"[indices] 失败: {e}")
        try:
            br = breadth_section(day)
            a_share["up_down_ratio"] = (
                f"{br.get('up')}/{br.get('down')}"
                if br.get("up") is not None
                else None
            )
        except Exception as e:  # noqa: BLE001
            a_share["up_down_ratio"] = None
            notes.append(f"[breadth] 失败: {e}")
        elec = _sector_flow(day, ("电子",))
        comm = _sector_flow(day, ("通信",))
        a_share["elec_fund_flow_yi"] = elec[0]["main_net_yi"] if elec else None
        a_share["comm_fund_flow_yi"] = comm[0]["main_net_yi"] if comm else None
        if not elec and not comm:
            notes.append("电子/通信板块资金流仅当日口径可得, 历史日期降级为空")

        return json_ok(
            {
                "date": str(day),
                "overnight_prev_day": str(prev),
                "overnight": overnight,
                "a_share": a_share,
                "notes": notes,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_global_linkage_review: %s", e)
        return json_err(f"共振复盘失败: {e}")
