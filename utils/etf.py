#!/usr/bin/env python3
"""
ETF 单日数据统一查询模块。

一次调用返回指定日期的行情（价格/涨跌幅/成交额）、资金（主力净流入）、
份额及份额变化数据，并输出合并汇总。支持单只、多只、预设组合或全部国家队 ETF。

数据通道（efinance 为主、akshare 东财为备，与项目 ef.py 通道架构一致）：
- 行情快照: efinance ['ETF'] 并行通道 -> 失败回退 akshare.fund_etf_spot_em
- 历史日K : efinance get_quote_history -> 失败回退 akshare.fund_etf_hist_em
- 份额变化: 上交所 fund_etf_scale_sse（按日，交易所直连，非东财）
- 深市份额: 深交所 fund_etf_scale_szse（最新快照；深交所无按日历史，变化无法计算）

口径说明：
- 主力净流入为东方财富口径（超大单+大单净额），仅 akshare 备用通道生效时提供，
  efinance 主通道无此字段；
- 份额变化 = 当日份额 - 上一交易日份额（交易所口径）。efinance 主通道下，
  沪市份额取最近已发布交易日（收盘后为当日），深市仅提供最新快照份额。
"""

from datetime import date as date_type, datetime, timedelta
import json
import logging
import re

import pandas as pd

from akshare import (
    fund_etf_hist_em,
    fund_etf_scale_sse,
    fund_etf_scale_szse,
    fund_etf_spot_em,
)

from .ef import ef_etf_spot, ef_stock_hist
from .tools import (
    CachedData,
    RateLimiter,
    RETRYABLE_EXCEPTIONS,
    with_retry,
)

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

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_spot_cache: CachedData | None = None  # data = (DataFrame, source)
_szse_scale_cache: CachedData | None = None
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


def _yi(value, ndigits: int = 2):
    """元 -> 亿元"""
    v = _num(value, ndigits=6)
    return None if v is None else round(v / 1e8, ndigits)


def _get_spot() -> tuple[pd.DataFrame, str]:
    """获取（带缓存）全市场 ETF 快照：efinance 主通道，akshare 备用

    返回 (DataFrame, source)；source 为 "efinance" 或 "akshare"。
    efinance 快照无 主力净流入-净额/最新份额 列。
    """
    global _spot_cache
    if _spot_cache is not None and not _spot_cache.is_expired():
        return _spot_cache.data
    df, source = None, None
    try:
        df = ef_etf_spot()
        source = "efinance"
    except Exception as e:  # noqa: BLE001
        logger.warning("efinance ETF spot failed, fallback to akshare: %s", e)
    if df is None or df.empty:
        df = fund_etf_spot_em()
        source = "akshare"
    result = (df, source)
    _spot_cache = CachedData(result, ttl=60)
    return result


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


def _get_scale_szse() -> dict:
    """深市 ETF 最新份额快照 -> {代码: 份额(份)}；失败返回空 dict（缓存1小时）"""
    global _szse_scale_cache
    if _szse_scale_cache is not None and not _szse_scale_cache.is_expired():
        return _szse_scale_cache.data
    try:
        df = fund_etf_scale_szse()
        shares = pd.to_numeric(df["基金份额"], errors="coerce")
        m = {
            str(c): float(s)
            for c, s in zip(df["基金代码"].astype(str), shares)
            if s == s
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("fund_etf_scale_szse failed: %s", e)
        m = {}
    _szse_scale_cache = CachedData(m, ttl=3600)
    return m


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
            return d.strftime("%Y-%m-%d"), {
                str(c): float(s)
                for c, s in zip(df["基金代码"].astype(str), shares)
                if s == s
            }
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


def _items_from_spot(
    spot: pd.DataFrame,
    shares_now: dict,
    shares_prev: dict,
) -> list[dict]:
    """从实时快照构建全部条目（内部使用，含全市场）

    shares_now/shares_prev: {代码: 份额(份)}，prev 仅覆盖沪市（可算变化）。
    """
    has_main_inflow = "主力净流入-净额" in spot.columns
    items = []
    for _, row in spot.iterrows():
        code = str(row["代码"])
        price = _num(row.get("最新价"), 3)
        now_s = shares_now.get(code)
        prev_s = shares_prev.get(code)
        share_change = (
            _num((now_s - prev_s) / 1e8, 4)
            if now_s is not None and prev_s is not None
            else None
        )
        items.append(
            {
                "code": code,
                "name": str(row.get("名称", "")),
                "price": price,
                "change_pct": _num(row.get("涨跌幅")),
                "amount_yi": _yi(row.get("成交额")),
                "market_cap_yi": _yi(row.get("总市值")),
                "main_inflow_yi": _yi(row.get("主力净流入-净额")) if has_main_inflow else None,
                "shares_yi": _num(now_s / 1e8, 4) if now_s is not None else None,
                "share_change_yi": share_change,
                "est_net_flow_yi": (
                    round(share_change * price, 4)
                    if share_change is not None and price is not None
                    else None
                ),
            }
        )
    return items


def _hist_one(code: str, day: date_type) -> pd.DataFrame | None:
    """单只 ETF 单日K线：efinance 主通道，akshare 备用；无数据返回 None"""
    compact = day.strftime("%Y%m%d")
    want = day.strftime("%Y-%m-%d")
    for fetch in (
        lambda: ef_stock_hist(code, start_date=compact, end_date=compact, adjust=""),
        lambda: fund_etf_hist_em(
            symbol=code, period="daily", start_date=compact, end_date=compact, adjust=""
        ),
    ):
        try:
            h = fetch()
        except Exception as e:  # noqa: BLE001
            logger.warning("hist fetch failed for %s: %s", code, e)
            continue
        if h is not None and not h.empty and "日期" in h.columns:
            h = h[h["日期"].astype(str).str.startswith(want)]
            if not h.empty:
                return h
    return None


def _items_from_hist(
    codes: list[str], day: date_type, q_map: dict, p_map: dict
) -> tuple[list[dict], list[str]]:
    """历史日期模式：逐只查询日行情构建条目（份额仅沪市可算）"""
    items, no_data = [], []
    for code in codes:
        h = _hist_one(code, day)
        if h is None:
            no_data.append(code)
            continue
        r = h.iloc[0]
        price = _num(r["收盘"], 3)
        shares_q = q_map.get(code)
        shares_p = p_map.get(code)
        shares_yi = _num(shares_q / 1e8, 4) if shares_q is not None else None
        share_change = (
            _num((shares_q - shares_p) / 1e8, 4)
            if shares_q is not None and shares_p is not None
            else None
        )
        items.append(
            {
                "code": code,
                "name": str(r.get("股票名称", "") or ""),
                "price": price,
                "change_pct": _num(r["涨跌幅"]),
                "amount_yi": _yi(r.get("成交额")),
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


def _merge(items: list[dict]) -> dict:
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
    inflow = [i["main_inflow_yi"] for i in items if i["main_inflow_yi"] is not None]
    if inflow:
        merged["total_main_inflow_yi"] = round(sum(inflow), 2)

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
        cap = pd.to_numeric(spot["总市值"], errors="coerce")
        amt = pd.to_numeric(spot["成交额"], errors="coerce")
        cap_rank = cap.rank(ascending=False, method="min")[spot["代码"] == code]
        amt_rank = amt.rank(ascending=False, method="min")[spot["代码"] == code]
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


def _proxy_hint() -> str:
    """代理链路自检：东财数据经 101.201.173.125 代理访问，失败时给出可定位的原因"""
    import urllib.request
    from os import getenv

    token = getenv("AKPROXY_TOKEN", "")
    if not token:
        return "当前服务未配置 AKPROXY_TOKEN（检查部署机 .env/环境变量后重启容器）"
    try:
        with urllib.request.urlopen(
            f"http://101.201.173.125:47001/api/token/{token}", timeout=5
        ) as r:
            return f"代理token有效，余额 {r.read().decode('utf-8', 'ignore')[:100]}（问题可能在代理服务或网络）"
    except Exception as e:  # noqa: BLE001
        return f"代理token无效或代理服务不可达({e})，请核对部署机 AKPROXY_TOKEN"


def _spot_shares_for_today(spot: pd.DataFrame, source: str, eff_day: date_type, notes: list):
    """当日模式的份额口径组装 -> (shares_now, shares_prev)

    - akshare 通道: spot 最新份额列（两市最新已发布），沪市变化对比上一发布日
    - efinance 通道: 沪市取 scale_sse 最近两个发布日，深市取交易所最新快照（无变化）
    """
    if source == "akshare" and "最新份额" in spot.columns:
        shares_now = {
            str(r["代码"]): float(r["最新份额"])
            for _, r in spot.iterrows()
            if _num(r["最新份额"]) is not None
        }
        prev = _prev_scale_sse(eff_day)
        shares_prev = prev[1] if prev else {}
        if not shares_prev:
            notes.append("沪市上一交易日份额数据不可用，份额变化无法计算")
        return shares_now, shares_prev

    latest = _prev_scale_sse(eff_day)
    shares_prev: dict = {}
    shares_now: dict = {}
    if latest is None:
        notes.append("沪市份额数据不可用，份额与变化仅深市快照可提供")
    else:
        latest_day = datetime.strptime(latest[0], "%Y-%m-%d").date()
        prev2 = _prev_scale_sse(latest_day)
        shares_now.update(latest[1])
        shares_prev = prev2[1] if prev2 else {}
    sz_map = _get_scale_szse()
    if sz_map:
        shares_now.update(sz_map)
    else:
        notes.append("深市份额快照不可用")
    notes.append(
        f"份额为最近已发布数据(沪市截至{latest[0] if latest else 'N/A'})，深市为交易所最新快照"
    )
    return shares_now, shares_prev


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
            spot, source = _get_spot()
            spot_date_raw = (
                str(spot["数据日期"].iloc[0])[:10]
                if "数据日期" in spot.columns
                else ""
            )
            if not _DATE_RE.match(spot_date_raw):
                spot_date_raw = str(today)
            if spot_date_raw != str(today):
                notes.append(
                    f"今日({today})无快照（非交易日或未开盘），返回最近交易日 {spot_date_raw} 数据"
                )
            eff_day = (
                datetime.strptime(spot_date_raw, "%Y-%m-%d").date()
                if spot_date_raw != str(today)
                else today
            )
            shares_now, shares_prev = _spot_shares_for_today(spot, source, eff_day, notes)
            all_items = _items_from_spot(spot, shares_now, shares_prev)
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
            notes.append(f"数据源: {source}通道" + ("（主力净流入不可用）" if source == "efinance" else ""))
            data = {
                "date": spot_date_raw,
                "mode": mode_desc,
                "merged": _merge(items),
            }
            if not codes and len(items) == len(all_items):
                data["total_etfs"] = len(all_items)
            if len(codes or []) == 1:
                data["single_rank"] = _single_rank(spot, codes[0])
            intraday = datetime.now().hour < 15 and spot_date_raw == str(today)
            if intraday:
                notes.append("盘中实时快照，收盘后数据会更新")

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
                {
                    str(c): float(s)
                    for c, s in zip(
                        scale_q["基金代码"].astype(str),
                        pd.to_numeric(scale_q["基金份额"], errors="coerce"),
                    )
                    if s == s
                }
                if scale_q is not None
                else {}
            )
            prev = _prev_scale_sse(qday)
            p_map = prev[1] if prev else {}
            items, no_data = _items_from_hist(codes, qday, q_map, p_map)
            if no_data:
                notes.append(
                    f"以下代码在 {qday} 无数据(可能为非交易日或已退市): {','.join(no_data)}"
                )
            if not items:
                hint = f"；网络自检: {_proxy_hint()}" if len(no_data) == len(codes) else ""
                return _err(
                    f"{qday} 未查询到任何数据（{len(no_data)}/{len(codes)}只获取失败），"
                    f"请确认该日为交易日且代码正确{hint}"
                )
            data = {
                "date": str(qday),
                "mode": mode_desc,
                "merged": _merge(items),
            }
            notes.append("历史日期无主力净流入数据(仅当日东财快照提供)")
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
        msg = f"查询ETF数据失败: {e}"
        if isinstance(e, RETRYABLE_EXCEPTIONS) or "Disconnected" in str(e):
            msg += f"。东财数据经代理访问失败，{ _proxy_hint() }"
        return _err(msg)
