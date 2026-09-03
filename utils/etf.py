#!/usr/bin/env python3
"""
ETF 单日数据统一查询模块。

一次调用返回指定日期的行情（价格/涨跌幅/成交额）、资金（主力净流入）、
份额及份额变化数据，并输出合并汇总。支持单只、多只、预设组合或全部 ETF。

数据源（akshare，经 akshare-proxy 代理）：
- fund_etf_spot_em   东方财富 ETF 实时行情快照（含主力净流入、最新份额、数据日期）
- fund_etf_hist_em   东方财富 ETF 历史日行情（历史日期模式，逐只查询）
- fund_etf_scale_sse 上交所每日 ETF 份额（计算沪市 ETF 的份额变化）

口径说明：
- 主力净流入为东方财富口径（超大单+大单净额），仅当日快照可提供；
- 份额变化 = 当日份额 - 上一交易日份额（交易所口径），深交所仅提供当前份额
  快照，无按日历史，因此深市 ETF 的份额变化无法计算。
"""

from datetime import date as date_type, datetime, timedelta
import json
import logging

import pandas as pd

from akshare import fund_etf_hist_em, fund_etf_scale_sse, fund_etf_spot_em

from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

# 国家队（中央汇金/证金重仓）核心宽基 ETF
NATIONAL_TEAM_ETFS = [
    "510300",  # 沪深300ETF华泰柏瑞
    "510310",  # 沪深300ETF易方达
    "159919",  # 沪深300ETF嘉实
    "510330",  # 沪深300ETF华夏
    "510050",  # 上证50ETF华夏
    "510500",  # 中证500ETF南方
    "512100",  # 中证1000ETF南方
    "159915",  # 创业板ETF易方达
]

# 默认与预设均指向国家队ETF；显式指定 "全市场"/"market" 才查询全市场
NATIONAL_TEAM_KEYWORDS = {"", "all", "全部", "国家队", "national_team", "guojiadui"}
MARKET_KEYWORDS = {"market", "全市场", "全部etf", "all_etf"}

MAX_DETAIL_ITEMS = 30  # 查询数量超过该值时省略明细，只输出汇总与榜单
MAX_HIST_SYMBOLS = 20  # 历史日期模式逐只查询上限
TOP_N = 5

_spot_cache: CachedData | None = None
_scale_sse_cache: dict[str, CachedData | None] = {}


def _num(value, ndigits: int = 2):
    """安全数值转换：NaN/None/异常 -> None，否则四舍五入（-0.0 归一为 0.0）"""
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        if v == 0:
            v = 0.0
        return round(v, ndigits)
    except (TypeError, ValueError):
        return None


def _get_spot() -> pd.DataFrame:
    """获取（带缓存）全市场 ETF 实时快照"""
    global _spot_cache
    if _spot_cache is not None and not _spot_cache.is_expired():
        return _spot_cache.data
    df = fund_etf_spot_em()
    _spot_cache = CachedData(df, ttl=60)
    return df


def _get_scale_sse(day_compact: str) -> pd.DataFrame | None:
    """获取沪市 ETF 某日份额表；无数据或出错返回 None（结果缓存1小时）"""
    if day_compact in _scale_sse_cache:
        cached = _scale_sse_cache[day_compact]
        if cached is not None and not cached.is_expired():
            return cached.data
    try:
        df = fund_etf_scale_sse(date=day_compact)
    except Exception as e:  # noqa: BLE001 非交易日/未发布时 akshare 内部会抛异常
        logger.warning("fund_etf_scale_sse(%s) failed: %s", day_compact, e)
        df = None
    if df is None or df.empty:
        _scale_sse_cache[day_compact] = None
        return None
    _scale_sse_cache[day_compact] = CachedData(df, ttl=3600)
    return df


def _prev_weekday(day: date_type) -> date_type:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六 6=周日
        d -= timedelta(days=1)
    return d


def _prev_scale_sse(day: date_type) -> tuple[str, dict] | None:
    """从 day 往前找最近一个有沪市份额数据的交易日，返回 (日期, {代码: 份额})"""
    d = _prev_weekday(day)
    for _ in range(12):
        df = _get_scale_sse(d.strftime("%Y%m%d"))
        if df is not None:
            shares = pd.to_numeric(df["基金份额"], errors="coerce")
            return d.strftime("%Y-%m-%d"), dict(
                zip(df["基金代码"].astype(str), shares)
            )
        d = _prev_weekday(d)
    return None


def _parse_symbols(symbols: str):
    """解析 symbols 参数 -> (codes 或 None 表示全市场, unmatched, mode_desc)

    默认(空)/"all"/"全部"/"国家队" = 全部国家队ETF；"全市场"/"market" = 全市场ETF。
    """
    s = (symbols or "").strip().lower().replace("，", ",").replace(" ", ",")
    s = ",".join(p for p in s.split(",") if p)
    if s in NATIONAL_TEAM_KEYWORDS:
        return (
            list(NATIONAL_TEAM_ETFS),
            [],
            f"国家队全部({len(NATIONAL_TEAM_ETFS)}只)",
        )
    if s in MARKET_KEYWORDS:
        return None, [], "全市场ETF"
    codes, unmatched = [], []
    for part in s.split(","):
        if part.isdigit() and len(part) == 6:
            if part not in codes:
                codes.append(part)
        else:
            unmatched.append(part)
    return codes, unmatched, f"指定{len(codes)}只"


def _items_from_spot(spot: pd.DataFrame, prev_map: dict | None) -> list[dict]:
    """从实时快照构建全部条目（内部使用，含全市场）"""
    prev_map = prev_map or {}
    items = []
    for _, row in spot.iterrows():
        code = str(row["代码"])
        shares = _num(row.get("最新份额"), 4)
        prev = prev_map.get(code)
        share_change = (
            _num((float(row["最新份额"]) - float(prev)) / 1e8, 4)
            if shares is not None and prev is not None and prev == prev
            else None
        )
        price = _num(row.get("最新价"), 3)
        items.append(
            {
                "code": code,
                "name": str(row.get("名称", "")),
                "price": price,
                "change_pct": _num(row.get("涨跌幅")),
                "amount_yi": _num(float(row["成交额"]) / 1e8) if _num(row.get("成交额")) is not None else None,
                "market_cap_yi": _num(float(row["总市值"]) / 1e8) if _num(row.get("总市值")) is not None else None,
                "main_inflow_yi": (
                    _num(float(row["主力净流入-净额"]) / 1e8)
                    if "主力净流入-净额" in row and _num(row.get("主力净流入-净额")) is not None
                    else None
                ),
                "shares_yi": _num(float(row["最新份额"]) / 1e8, 4) if shares is not None else None,
                "share_change_yi": share_change,
                "est_net_flow_yi": (
                    round(share_change * price, 4)
                    if share_change is not None and price is not None
                    else None
                ),
            }
        )
    return items


def _items_from_hist(
    codes: list[str], day: date_type, name_map: dict, q_map: dict, p_map: dict
) -> tuple[list[dict], list[str]]:
    """历史日期模式：逐只查询日行情构建条目"""
    compact = day.strftime("%Y%m%d")
    items, no_data = [], []
    for code in codes:
        try:
            h = fund_etf_hist_em(
                symbol=code, period="daily", start_date=compact, end_date=compact, adjust=""
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("fund_etf_hist_em(%s) failed: %s", code, e)
            no_data.append(code)
            continue
        h = h[h["日期"] == day.strftime("%Y-%m-%d")] if h is not None and not h.empty else h
        if h is None or h.empty:
            no_data.append(code)
            continue
        r = h.iloc[0]
        price = _num(r["收盘"], 3)
        shares_q = q_map.get(code)
        shares_p = p_map.get(code)
        shares_yi = _num(float(shares_q) / 1e8, 4) if shares_q is not None and shares_q == shares_q else None
        share_change = (
            _num((float(shares_q) - float(shares_p)) / 1e8, 4)
            if shares_q is not None and shares_p is not None and shares_q == shares_q and shares_p == shares_p
            else None
        )
        items.append(
            {
                "code": code,
                "name": name_map.get(code, ""),
                "price": price,
                "change_pct": _num(r["涨跌幅"]),
                "amount_yi": _num(float(r["成交额"]) / 1e8) if _num(r.get("成交额")) is not None else None,
                "market_cap_yi": (
                    round(shares_yi * price, 2) if shares_yi is not None and price else None
                ),
                "main_inflow_yi": None,
                "shares_yi": shares_yi,
                "share_change_yi": share_change,
                "est_net_flow_yi": (
                    round(share_change * price, 4)
                    if share_change is not None and price is not None
                    else None
                ),
            }
        )
    return items, no_data


def _merge(items: list[dict], has_main_inflow: bool) -> dict:
    """合并汇总：总量、涨跌结构、份额与资金变化、榜单"""
    chg = [i["change_pct"] for i in items if i["change_pct"] is not None]
    cap_pairs = [
        (i["market_cap_yi"], i["change_pct"])
        for i in items
        if i["market_cap_yi"] is not None and i["change_pct"] is not None
    ]
    total_cap = sum(c for c, _ in cap_pairs)
    merged = {
        "count": len(items),
        "total_market_cap_yi": round(total_cap, 2) if cap_pairs else None,
        "total_amount_yi": round(
            sum(i["amount_yi"] for i in items if i["amount_yi"] is not None), 2
        ) or None,
        "avg_change_pct": round(sum(chg) / len(chg), 2) if chg else None,
        "weighted_change_pct": (
            round(sum(c * p for c, p in cap_pairs) / total_cap, 2)
            if cap_pairs and total_cap
            else None
        ),
        "up": sum(1 for c in chg if c > 0),
        "down": sum(1 for c in chg if c < 0),
        "flat": sum(1 for c in chg if c == 0),
        "total_share_change_yi": None,
        "est_net_subscription_yi": None,
    }
    if has_main_inflow:
        merged["total_main_inflow_yi"] = round(
            sum(i["main_inflow_yi"] for i in items if i["main_inflow_yi"] is not None), 2
        )

    valid_sc = [i for i in items if i["share_change_yi"] is not None]
    if valid_sc:
        merged["total_share_change_yi"] = round(
            sum(i["share_change_yi"] for i in valid_sc), 4
        )
        merged["est_net_subscription_yi"] = round(
            sum(
                i["est_net_flow_yi"]
                for i in valid_sc
                if i["est_net_flow_yi"] is not None
            ),
            2,
        )
    by_chg = sorted(
        (i for i in items if i["change_pct"] is not None),
        key=lambda i: i["change_pct"],
        reverse=True,
    )
    # 净申购/净赎回榜仅收录有实际变化的（过滤盘中尚未更新导致的 0 值噪音）
    by_flow = sorted(
        (
            i
            for i in items
            if i["est_net_flow_yi"] is not None and abs(i["est_net_flow_yi"]) >= 0.01
        ),
        key=lambda i: i["est_net_flow_yi"],
        reverse=True,
    )
    slim = lambda i, k: {"code": i["code"], "name": i["name"], k: i[k]}  # noqa: E731
    merged["top_gainers"] = [slim(i, "change_pct") for i in by_chg[:TOP_N]]
    merged["top_losers"] = [slim(i, "change_pct") for i in by_chg[-TOP_N:][::-1]]
    if by_flow:
        merged["top_net_subscription"] = [
            slim(i, "est_net_flow_yi") for i in by_flow[:TOP_N]
        ]
        merged["top_net_redemption"] = [
            slim(i, "est_net_flow_yi") for i in by_flow[-TOP_N:][::-1]
        ]
    return merged


def _single_rank(spot: pd.DataFrame, code: str) -> dict:
    """单只查询时，给出该 ETF 在全市场的规模/成交额排名"""
    try:
        cap_rank = (
            spot["总市值"].astype(float).rank(ascending=False, method="min")[spot["代码"] == code]
        )
        amt_rank = (
            spot["成交额"].astype(float).rank(ascending=False, method="min")[spot["代码"] == code]
        )
        return {
            "market_cap_rank": int(cap_rank.iloc[0]) if not cap_rank.empty else None,
            "amount_rank": int(amt_rank.iloc[0]) if not amt_rank.empty else None,
            "total_etfs": len(spot),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("rank calc failed: %s", e)
        return {}


def _err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False, indent=2)


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_etf_daily(symbols: str = "", date: str = "") -> str:
    """查询 ETF 单日数据（行情、资金、份额）并返回合并汇总

    Args:
        symbols: 默认(空)/"all"/"全部"/"国家队" = 全部国家队ETF（8只核心宽基）；
                 "全市场"/"market" = 全市场ETF（约1600只，仅支持当日）；
                 或 6位ETF代码，逗号分隔
        date: 查询日期 YYYY-MM-DD，默认今天（盘中为实时快照）。
              历史日期需指定代码（上限20只），全市场仅支持当日。
    """
    try:
        today = datetime.now().date()
        if date:
            try:
                qday = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                return _err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        else:
            qday = today
        if qday > today:
            return _err(f"查询日期 {qday} 晚于今天，无法查询未来数据")

        codes, unmatched, mode_desc = _parse_symbols(symbols)
        notes = []
        if unmatched:
            notes.append(f"忽略无法识别的代码: {','.join(unmatched)}")

        # ---------- 当日模式：一次快照拿全部 ----------
        if qday == today:
            spot = _get_spot()
            if spot is None or spot.empty:
                return _err("未获取到 ETF 行情快照")
            spot_date = (
                str(spot["数据日期"].iloc[0])[:10]
                if "数据日期" in spot.columns
                else str(today)
            )
            if spot_date != str(today):
                notes.append(
                    f"今日({today})无快照（非交易日或未开盘），返回最近交易日 {spot_date} 数据"
                )
            eff_day = (
                datetime.strptime(spot_date, "%Y-%m-%d").date()
                if spot_date != str(today)
                else today
            )
            prev = _prev_scale_sse(eff_day)
            prev_map = prev[1] if prev else {}
            if not prev_map:
                notes.append("沪市上一交易日份额数据不可用，份额变化无法计算")
            all_items = _items_from_spot(spot, prev_map)
            name_map = {i["code"]: i["name"] for i in all_items}
            if codes:
                found = {i["code"] for i in all_items}
                missing = [c for c in codes if c not in found]
                if missing:
                    notes.append(f"快照中未找到: {','.join(missing)}")
                items = [i for i in all_items if i["code"] in set(codes)]
                if not items:
                    return _err(f"未找到任何匹配的ETF: {','.join(codes)}")
            else:
                items = all_items
            data = {
                "date": spot_date,
                "mode": mode_desc,
                "merged": _merge(items, has_main_inflow=True),
            }
            if not codes and len(items) == len(all_items):
                data["total_etfs"] = len(all_items)
            if len(codes or []) == 1:
                data["single_rank"] = _single_rank(spot, codes[0])
            intraday = datetime.now().hour < 15 and spot_date == str(today)
            if intraday:
                notes.append("盘中实时快照，收盘后数据会更新")
            # 深市份额变化的口径限制提示
            if any(i["share_change_yi"] is None and i["shares_yi"] is not None for i in items):
                notes.append(
                    "份额变化仅覆盖沪市ETF（深交所无按日份额历史），深市ETF仅提供最新份额"
                )

        # ---------- 历史模式：逐只查询 ----------
        else:
            if codes is None:
                return _err(
                    f"历史日期({qday})不支持查询全市场ETF（需逐只请求过多），"
                    f"请指定代码（最多{MAX_HIST_SYMBOLS}只），或使用默认的国家队ETF"
                )
            if len(codes) > MAX_HIST_SYMBOLS:
                return _err(
                    f"历史日期模式一次最多查询{MAX_HIST_SYMBOLS}只，当前{len(codes)}只"
                )
            if not codes:
                return _err("未指定任何有效的ETF代码")
            scale_q = _get_scale_sse(qday.strftime("%Y%m%d"))
            q_map = (
                dict(
                    zip(
                        scale_q["基金代码"].astype(str),
                        pd.to_numeric(scale_q["基金份额"], errors="coerce"),
                    )
                )
                if scale_q is not None
                else {}
            )
            prev = _prev_scale_sse(qday)
            p_map = prev[1] if prev else {}
            name_map = {
                str(r["代码"]): str(r["名称"]) for _, r in _get_spot().iterrows()
            }
            items, no_data = _items_from_hist(codes, qday, name_map, q_map, p_map)
            if no_data:
                notes.append(
                    f"以下代码在 {qday} 无数据(可能为非交易日或已退市): {','.join(no_data)}"
                )
            if not items:
                return _err(
                    f"{qday} 未查询到任何数据，请确认该日为交易日且代码正确"
                )
            data = {
                "date": str(qday),
                "mode": mode_desc,
                "merged": _merge(items, has_main_inflow=False),
            }
            notes.append("历史日期无主力净流入数据(东财仅提供当日快照)")
            if any(i["shares_yi"] is None for i in items):
                notes.append("深市ETF无历史份额数据，规模与份额变化仅覆盖沪市")

        # 明细输出控制
        if len(items) <= MAX_DETAIL_ITEMS:
            data["items"] = items
        else:
            notes.append(f"共{len(items)}只，超过{MAX_DETAIL_ITEMS}只，明细已省略，仅返回汇总与榜单")
        if notes:
            data["notes"] = notes
        return json.dumps(
            {"success": True, "data": data}, ensure_ascii=False, indent=2
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_etf_daily: %s", e)
        return _err(f"查询ETF数据失败: {e}")
