#!/usr/bin/env python3
"""
复盘·资金模块：大盘/板块主力资金 + 两融余额。

数据源（akshare）：
- stock_market_fund_flow       东财大盘资金流历史序列（含当日）
- stock_sector_fund_flow_rank  东财板块资金流排名（仅 今日/5日/10日 口径）
- stock_margin_sse/szse        沪深交易所两融（T-1 披露）

口径说明：
- 主力净流入为东方财富口径（超大单+大单净额），推断口径非真实机构。
- 两融余额为交易所披露口径，隔日发布（T-1）。
"""

from datetime import date as date_type, datetime, timedelta
import logging

import pandas as pd

from akshare import (
    stock_margin_sse,
    stock_margin_szse,
    stock_market_fund_flow,
    stock_sector_fund_flow_rank,
)

from .review_common import (
    col_like,
    json_err,
    json_ok,
    norm_date,
    parse_day,
    prev_trading_days,
    safe_num,
)
from .tools import RateLimiter, with_retry

logger = logging.getLogger(__name__)

INDICATORS = ("今日", "5日", "10日")


def _pick(df: pd.DataFrame, r: pd.Series, keyword: str, ndigits: int = 2, div: float = 1.0):
    """按子串取列并安全转数值，列缺失返回 None"""
    # 精确列名优先："大单净流入-净额"是"超大单净流入-净额"的子串，子串匹配会误中前列
    c = keyword if keyword in df.columns else col_like(df, keyword)
    if c is None:
        return None
    try:
        return safe_num(float(r[c]) / div, ndigits)
    except (TypeError, ValueError):
        return None


def market_fund_flow_section(day: date_type) -> dict:
    """③ 大盘主力资金（取历史序列中目标日行，单位亿元）"""
    df = stock_market_fund_flow()
    if df is None or df.empty:
        raise ValueError("大盘资金流接口无数据")
    col_date = col_like(df, "日期")
    hit = df[df[col_date].astype(str).str[:10] == str(day)]
    if hit.empty:
        raise ValueError(f"{day} 无大盘资金流数据(可能非交易日)")
    r = hit.iloc[0]
    return {
        "date": str(day),
        "main_net_yi": _pick(df, r, "主力净流入-净额", div=1e8),
        "super_large_net_yi": _pick(df, r, "超大单净流入-净额", div=1e8),
        "large_net_yi": _pick(df, r, "大单净流入-净额", div=1e8),
        "sh_chg_pct": _pick(df, r, "上证-涨跌幅"),
        "sz_chg_pct": _pick(df, r, "深证-涨跌幅"),
        "notes": ["主力口径: 东财超大单+大单净额(推断口径, 非真实机构)"],
    }


def sector_fund_flow_section(day: date_type, indicator: str = "今日") -> dict:
    """③ 板块主力资金排名（行业口径；"今日"仅当日，5日/10日为滚动口径）"""
    if indicator not in INDICATORS:
        raise ValueError(f"indicator 仅支持 {'/'.join(INDICATORS)}，当前: {indicator}")
    if indicator == "今日" and day != datetime.now().date():
        return {
            "date": str(day),
            "items": None,
            "notes": ["板块资金流仅支持当日(今日口径)，历史日期已省略"],
        }
    df = stock_sector_fund_flow_rank(indicator=indicator, sector_type="行业资金流")
    col_main = col_like(df, "主力净流入-净额")
    col_chg = col_like(df, "涨跌幅")
    d = df.copy()
    d["_v"] = pd.to_numeric(d[col_main], errors="coerce")
    d = d.dropna(subset=["_v"]).sort_values("_v", ascending=False)

    def slim(r):
        return {
            "name": str(r["名称"]),
            "main_net_yi": safe_num(float(r[col_main]) / 1e8, 2),
            "chg_pct": safe_num(r[col_chg], 2) if col_chg else None,
        }

    return {
        "date": str(day),
        "indicator": indicator,
        "count": len(d),
        "top5": [slim(r) for _, r in d.head(5).iterrows()],
        "bottom5": [slim(r) for _, r in d.tail(5).iloc[::-1].iterrows()],
        "notes": [],
    }


def fund_flow_section(day: date_type) -> dict:
    """聚合用：大盘 + 板块（各自独立降级）"""
    out, notes = {}, []
    try:
        out["market"] = market_fund_flow_section(day)
        notes += [f"[market] {n}" for n in out["market"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["market"] = None
        notes.append(f"[market] 失败: {e}")
    try:
        out["sector"] = sector_fund_flow_section(day, "今日")
        notes += [f"[sector] {n}" for n in out["sector"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["sector"] = None
        notes.append(f"[sector] 失败: {e}")
    if out["market"] is None and out["sector"] is None:
        raise ValueError("大盘与板块资金流均失败")
    return {"date": str(day), **out, "notes": notes}


def margin_section(day: date_type, days: int = 10, market: str = "沪深") -> dict:
    """⑦ 两融余额（融资余额，T-1 披露；沪深合计，单位亿元）"""
    if market not in ("沪", "深", "沪深"):
        raise ValueError(f"market 仅支持 沪/深/沪深，当前: {market}")
    notes = []
    per_day: dict[str, dict[str, float]] = {}
    if market in ("沪深", "沪"):
        try:
            start = (day - timedelta(days=days * 2 + 6)).strftime("%Y%m%d")
            sse = stock_margin_sse(start_date=start, end_date=day.strftime("%Y%m%d"))
            c_d, c_r = col_like(sse, "日期"), col_like(sse, "融资余额")
            for _, r in sse.iterrows():
                try:
                    per_day.setdefault(norm_date(r[c_d]), {})["sse"] = float(r[c_r])
                except (TypeError, ValueError, KeyError):
                    continue
        except Exception as e:  # noqa: BLE001
            notes.append(f"沪市两融获取失败: {e}")
    if market in ("沪深", "深"):
        got = 0
        for d in [day, *prev_trading_days(day, days + 4)]:
            if got >= days:
                break
            try:
                sz = stock_margin_szse(date=d.strftime("%Y%m%d"))
            except Exception:  # noqa: BLE001 非交易日/未发布
                continue
            if sz is None or sz.empty:
                continue
            c_r = col_like(sz, "融资余额")
            c_d = col_like(sz, "日期")
            key = norm_date(sz.iloc[0][c_d]) if c_d else str(d)
            try:
                per_day.setdefault(key, {})["szse"] = float(sz.iloc[0][c_r])
                got += 1
            except (TypeError, ValueError, KeyError):
                continue
    if not per_day:
        raise ValueError(f"{day} 前后无两融数据(交易所T+1披露)")
    series = []
    for k in sorted(per_day):
        entry = per_day[k]
        parts = [v for v in (entry.get("sse"), entry.get("szse")) if v is not None]
        if not parts:
            continue
        series.append(
            {
                "date": k,
                "rzye_yi": round(sum(parts) / 1e8, 2),
                "sse_yi": round(entry["sse"] / 1e8, 2) if "sse" in entry else None,
                "szse_yi": round(entry["szse"] / 1e8, 2) if "szse" in entry else None,
            }
        )
    series = series[-days:]
    latest = series[-1]
    prev = series[-2] if len(series) > 1 else None
    return {
        "date": latest["date"],
        "rzye_yi": latest["rzye_yi"],
        "rzye_chg_yi": round(latest["rzye_yi"] - prev["rzye_yi"], 2) if prev else None,
        "series": series,
        "notes": notes + ["两融为T-1披露口径(交易所隔日发布)"],
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_fund_flow(scope: str = "全部", indicator: str = "今日", date: str = "") -> str:
    """查询主力资金：大盘（沪深两市净流入）与/或行业板块排名

    Args:
        scope: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
        indicator: "今日" / "5日" / "10日"（仅板块口径使用），默认"今日"
        date: 查询日期 YYYY-MM-DD，默认今天（板块"今日"口径仅支持当日）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        s = (scope or "").strip()
        if s not in ("全部", "大盘", "板块"):
            return json_err(f"scope 仅支持 全部/大盘/板块，当前: {scope}")
        if s == "大盘":
            return json_ok(market_fund_flow_section(day))
        if s == "板块":
            return json_ok(sector_fund_flow_section(day, indicator))
        return json_ok(fund_flow_section(day))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_fund_flow: %s", e)
        return json_err(f"查询主力资金失败: {e}")


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_margin(market: str = "沪深", days: int = 10, date: str = "") -> str:
    """查询两融（融资余额，T-1 披露）

    Args:
        market: "沪深"(合计) / "沪" / "深"，默认"沪深"
        days: 返回最近 N 个交易日的余额序列（1-30，默认10）
        date: 截止日期 YYYY-MM-DD，默认今天（实际取该日前最近披露日）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        if not 1 <= int(days) <= 30:
            return json_err(f"days 需在 1-30 之间，当前: {days}")
        return json_ok(margin_section(day, days=int(days), market=market))
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_margin: %s", e)
        return json_err(f"查询两融失败: {e}")
