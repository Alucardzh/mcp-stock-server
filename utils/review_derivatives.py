#!/usr/bin/env python3
"""
复盘·衍生品模块：股指期货基差、中金所席位持仓、期权 PCR。

数据源（全部直连，不走 akshare-proxy）：
- get_cffex_daily             中金所官方日行情（全部品种合约，含结算价/持仓量）
- stock_zh_index_daily        新浪现货指数日行情（基差的现货腿）
- get_cffex_rank_table        中金所股指期货前20会员成交持仓排名
- 中金所股指期权席位 CSV       http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IO|MO|HO}_1.csv
- option_daily_stats_sse/szse 沪深交易所期权日统计（认购/认沽量与持仓）

口径说明：
- 基差 = 现货指数收盘价 - 期货主力合约(成交量最大)结算价；负值为贴水。
- 年化基差按合约到期日（当月第三个周五）折算。
- PCR = 认沽/认购 × 100（%），成交量口径与持仓量口径分别给出，分交易所不混算。
- 席位数据为经纪口径（"(代客)"后缀），仅前20会员可见，缺失方向按0计。
"""

import io
import logging
import re
from datetime import date as date_type, timedelta

import pandas as pd
import requests

from akshare import (
    get_cffex_daily,
    get_cffex_rank_table,
    option_daily_stats_sse,
    option_daily_stats_szse,
    stock_zh_index_daily,
)

from .review_common import (
    json_err,
    json_ok,
    parse_day,
    prev_trading_days,
    safe_num,
)
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

INDEX_FUTURES = {
    "IF": ("sh000300", "沪深300"),
    "IH": ("sh000016", "上证50"),
    "IC": ("sh000905", "中证500"),
    "IM": ("sh000852", "中证1000"),
}
OPTION_VARS = {"IO": "沪深300期权", "MO": "中证1000期权", "HO": "上证50期权"}
RANK_VARS = set(INDEX_FUTURES) | set(OPTION_VARS)

RANK_URL = "http://www.cffex.com.cn/sj/ccpm/{ym}/{d}/{var}_1.csv"

_index_daily_cache: dict[str, CachedData] = {}


def _spot_close(sina_symbol: str, day: date_type) -> float | None:
    """现货指数收盘价（新浪日K，缓存300秒）"""
    c = _index_daily_cache.get(sina_symbol)
    if c is None or c.is_expired():
        c = CachedData(stock_zh_index_daily(symbol=sina_symbol), ttl=300)
        _index_daily_cache[sina_symbol] = c
    df = c.data
    hit = df[df["date"].astype(str) == str(day)]
    return float(hit.iloc[0]["close"]) if not hit.empty else None


def _contract_expiry(symbol: str) -> date_type | None:
    """合约到期日 = 合约月份的第三个周五（IF2609 -> 2026-09-18）"""
    m = re.match(r"^[A-Z]{1,2}(\d{2})(\d{2})$", str(symbol))
    if not m:
        return None
    year, month = 2000 + int(m.group(1)), int(m.group(2))
    first = date_type(year, month, 1)
    fridays = [
        first + timedelta(days=i)
        for i in range(31)
        if (first + timedelta(days=i)).weekday() == 4
    ]
    return fridays[2] if len(fridays) >= 3 else None


def _cffex_daily_with_fallback(day: date_type) -> tuple[pd.DataFrame, date_type]:
    """中金所日行情（非交易日/未发布时回退最近交易日）"""
    for d in [day, *prev_trading_days(day, 6)]:
        try:
            df = get_cffex_daily(d.strftime("%Y%m%d"))
        except Exception as e:  # noqa: BLE001
            logger.warning("get_cffex_daily(%s) failed: %s", d, e)
            continue
        if df is not None and not df.empty:
            return df, d
    raise ValueError(f"{day} 前后数个交易日均无中金所日行情")


def basis_section(day: date_type) -> dict:
    """⑤ 期指基差：IF/IH/IC/IM 主力合约结算价 vs 现货收盘"""
    df, actual_day = _cffex_daily_with_fallback(day)
    notes = []
    if actual_day != day:
        notes.append(f"{day} 无中金所日行情，使用最近交易日 {actual_day}")
    df = df[df["variety"].isin(INDEX_FUTURES)]
    items = []
    for var, (sina_sym, cname) in INDEX_FUTURES.items():
        sub = df[df["variety"] == var]
        if sub.empty:
            notes.append(f"{var} 当日无合约数据")
            continue
        main = sub.loc[pd.to_numeric(sub["volume"]).idxmax()]
        sym = str(main["symbol"])
        settle = float(main["settle"])
        spot = _spot_close(sina_sym, actual_day)
        if spot is None:
            notes.append(f"{cname} 现货收盘缺失，{var} 基差无法计算")
            continue
        basis = spot - settle
        expiry = _contract_expiry(sym)
        ann = None
        if expiry and expiry > actual_day:
            ann = round(basis / spot * 365 / (expiry - actual_day).days * 100, 2)
        items.append(
            {
                "variety": var,
                "index": cname,
                "contract": sym,
                "settle": round(settle, 1),
                "spot_close": round(spot, 2),
                "basis": round(basis, 2),
                "basis_pct": round(basis / spot * 100, 3),
                "basis_ann_pct": ann,
                "volume": int(main["volume"]),
                "open_interest": int(main["open_interest"]),
            }
        )
    if not items:
        raise ValueError("四大期指均无基差数据")
    return {"date": str(actual_day), "items": items, "notes": notes}


def pcr_section(day: date_type) -> dict:
    """⑥ 期权PCR：分交易所给出认购/认沽的量比与持仓比（×100，%）"""
    compact = day.strftime("%Y%m%d")
    notes = []
    sse = szse = None
    try:
        sse = option_daily_stats_sse(date=compact)
    except Exception as e:  # noqa: BLE001
        notes.append(f"上交所期权统计获取失败: {e}")
    try:
        szse = option_daily_stats_szse(date=compact)
    except Exception as e:  # noqa: BLE001
        notes.append(f"深交所期权统计获取失败: {e}")
    if (sse is None or sse.empty) and (szse is None or szse.empty):
        raise ValueError(f"{day} 无交易所期权统计(可能非交易日或未发布)")
    exchanges = []
    if sse is not None and not sse.empty:
        under = []
        for _, r in sse.iterrows():
            c, p = float(r["认购成交量"]), float(r["认沽成交量"])
            oc = float(r["未平仓认购合约数"])
            op = float(r["未平仓认沽合约数"])
            under.append(
                {
                    "underlying": f"{r['合约标的代码']} {r['合约标的名称']}",
                    "vol": int(r["总成交量"]),
                    "vol_pcr": round(p / c * 100, 2) if c else None,
                    "oi_pcr": round(op / oc * 100, 2) if oc else None,
                }
            )
        tc = sse["认购成交量"].astype(float).sum()
        tp = sse["认沽成交量"].astype(float).sum()
        oc = sse["未平仓认购合约数"].astype(float).sum()
        op = sse["未平仓认沽合约数"].astype(float).sum()
        exchanges.append(
            {
                "exchange": "SSE",
                "vol_pcr": round(tp / tc * 100, 2) if tc else None,
                "oi_pcr": round(op / oc * 100, 2) if oc else None,
                "underlyings": under,
            }
        )
    if szse is not None and not szse.empty:
        under = []
        for _, r in szse.iterrows():
            c, p = float(r["认购成交量"]), float(r["认沽成交量"])
            oc = float(r["未平仓认购合约数"])
            op = float(r["未平仓认沽合约数"])
            under.append(
                {
                    "underlying": f"{r['合约标的代码']} {r['合约标的名称']}",
                    "vol": int(r["成交量"]),
                    "vol_pcr": round(p / c * 100, 2) if c else None,
                    "oi_pcr": round(op / oc * 100, 2) if oc else None,
                }
            )
        tc = szse["认购成交量"].astype(float).sum()
        tp = szse["认沽成交量"].astype(float).sum()
        oc = szse["未平仓认购合约数"].astype(float).sum()
        op = szse["未平仓认沽合约数"].astype(float).sum()
        exchanges.append(
            {
                "exchange": "SZSE",
                "vol_pcr": round(tp / tc * 100, 2) if tc else None,
                "oi_pcr": round(op / oc * 100, 2) if oc else None,
                "underlyings": under,
            }
        )
    return {"date": str(day), "exchanges": exchanges, "notes": notes}


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_index_derivatives(date: str = "") -> str:
    """查询股指衍生品指标：四大期指基差（含年化）+ 期权PCR（分交易所）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天（席位排名见 get_cffex_rank）
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > date_type.today():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        basis = pcr = None
        notes = []
        try:
            basis = basis_section(day)
            notes += [f"[basis] {n}" for n in basis.pop("notes", [])]
        except Exception as e:  # noqa: BLE001
            notes.append(f"[basis] 失败: {e}")
        try:
            pcr = pcr_section(day)
            notes += [f"[pcr] {n}" for n in pcr.pop("notes", [])]
        except Exception as e:  # noqa: BLE001
            notes.append(f"[pcr] 失败: {e}")
        if basis is None and pcr is None:
            return json_err(f"{day} 基差与PCR均无数据(可能非交易日)")
        return json_ok({"date": str(day), "basis": basis, "pcr": pcr, "notes": notes})
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_index_derivatives: %s", e)
        return json_err(f"查询衍生品指标失败: {e}")


_RANK_NAMES = [
    "交易日", "合约系列", "rank",
    "vol_party_name", "vol", "vol_chg",
    "long_party_name", "long_open_interest", "long_open_interest_chg",
    "short_party_name", "short_open_interest", "short_open_interest_chg",
]


def fetch_option_rank_csv(var: str, day: date_type) -> pd.DataFrame | None:
    """中金所股指期权成交持仓排名 CSV（官网直连, GBK; 无数据返回 None）"""
    url = RANK_URL.format(ym=day.strftime("%Y%m"), d=day.strftime("%d"), var=var)
    try:
        resp = requests.get(url, timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.warning("cffex option rank %s %s failed: %s", var, day, e)
        return None
    if resp.status_code != 200 or len(resp.content) < 60:
        return None
    try:
        return pd.read_csv(
            io.BytesIO(resp.content),
            encoding="gbk",
            header=None,
            skiprows=2,
            names=_RANK_NAMES,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("parse cffex option rank csv failed: %s", e)
        return None


def option_rank_with_fallback(
    var: str, day: date_type
) -> tuple[pd.DataFrame | None, date_type]:
    for d in [day, *prev_trading_days(day, 6)]:
        df = fetch_option_rank_csv(var, d)
        if df is not None and not df.empty:
            return df, d
    return None, day


def _summarize_members(rank_df: pd.DataFrame) -> list[dict]:
    """按会员聚合净持仓与净变化（跨合约求和；仅前20可见, 缺失方向按0计）"""
    agg: dict[str, dict[str, float]] = {}
    for _, r in rank_df.iterrows():
        for side, party_col, oi_col, chg_col in (
            ("long", "long_party_name", "long_open_interest", "long_open_interest_chg"),
            ("short", "short_party_name", "short_open_interest", "short_open_interest_chg"),
        ):
            name = str(r.get(party_col, "")).strip()
            if not name or name == "nan":
                continue
            oi = pd.to_numeric(r.get(oi_col), errors="coerce")
            chg = pd.to_numeric(r.get(chg_col), errors="coerce")
            slot = agg.setdefault(
                name, {"long": 0.0, "short": 0.0, "long_chg": 0.0, "short_chg": 0.0}
            )
            if pd.notna(oi):
                slot[side] += float(oi)
            if pd.notna(chg):
                slot[f"{side}_chg"] += float(chg)
    rows = [
        {
            "member": name,
            "net": int(s["long"] - s["short"]),
            "net_chg": int(s["long_chg"] - s["short_chg"]),
            "long_oi": int(s["long"]),
            "short_oi": int(s["short"]),
        }
        for name, s in agg.items()
    ]
    rows.sort(key=lambda x: x["net_chg"], reverse=True)
    return rows


def _slim_rank_rows(df: pd.DataFrame, member: str = "") -> list[dict]:
    """前20排名行瘦身（可按会员子串过滤）"""
    rows = []
    for _, r in df.head(20).iterrows():
        if member and not any(
            member in str(r.get(c, ""))
            for c in ("vol_party_name", "long_party_name", "short_party_name")
        ):
            continue
        rank = pd.to_numeric(r.get("rank"), errors="coerce")
        rows.append(
            {
                "rank": int(rank) if pd.notna(rank) else None,
                "vol_party": str(r.get("vol_party_name", "")),
                "vol": safe_num(r.get("vol"), 0),
                "vol_chg": safe_num(r.get("vol_chg"), 0),
                "long_party": str(r.get("long_party_name", "")),
                "long_oi": safe_num(r.get("long_open_interest"), 0),
                "long_chg": safe_num(r.get("long_open_interest_chg"), 0),
                "short_party": str(r.get("short_party_name", "")),
                "short_oi": safe_num(r.get("short_open_interest"), 0),
                "short_chg": safe_num(r.get("short_open_interest_chg"), 0),
            }
        )
    return rows


@RateLimiter(max_calls=10, time_window=60)
def get_cffex_rank(var: str = "IF", date: str = "", member: str = "") -> str:
    """查询中金所前20会员成交持仓排名（经纪席位口径, "(代客)"后缀）

    Args:
        var: IF/IH/IC/IM(股指期货) 或 IO/MO/HO(股指期权)，默认 IF
        date: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退最近交易日
        member: 会员名子串过滤（如 "中信"），默认不过滤
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        v = (var or "").strip().upper()
        if v not in RANK_VARS:
            return json_err(f"var 仅支持 {'/'.join(sorted(RANK_VARS))}，当前: {var}")
        notes = []
        if v in INDEX_FUTURES:
            df_map: dict = {}
            actual_day = day
            for d in [day, *prev_trading_days(day, 6)]:
                try:
                    df_map = get_cffex_rank_table(d.strftime("%Y%m%d"), vars_list=[v])
                except Exception as e:  # noqa: BLE001
                    logger.warning("get_cffex_rank_table(%s) failed: %s", d, e)
                    continue
                if isinstance(df_map, dict) and df_map:
                    actual_day = d
                    break
            if not df_map:
                return json_err(f"{day} 前后数个交易日无 {v} 席位数据")
            if actual_day != day:
                notes.append(f"{day} 无数据，返回最近交易日 {actual_day}")
            contracts = {
                sym: _slim_rank_rows(df, member) for sym, df in df_map.items()
            }
            summary = _summarize_members(
                pd.concat(list(df_map.values()), ignore_index=True)
            )
        else:
            df, actual_day = option_rank_with_fallback(v, day)
            if df is None or df.empty:
                return json_err(f"{day} 前后数个交易日无 {v} 期权席位数据")
            if actual_day != day:
                notes.append(f"{day} 无数据，返回最近交易日 {actual_day}")
            contracts = {
                str(sym): _slim_rank_rows(sub, member)
                for sym, sub in df.groupby("合约系列")
            }
            summary = _summarize_members(df)
        if member:
            summary = [s for s in summary if member in s["member"]]
        return json_ok(
            {
                "date": str(actual_day),
                "variety": v,
                "contracts": contracts,
                "member_summary": summary[:20],
                "notes": notes
                + ["席位为经纪口径(代客)，仅前20会员可见，缺失方向按0计"],
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_cffex_rank: %s", e)
        return json_err(f"查询席位排名失败: {e}")


def derivatives_section(day: date_type) -> dict:
    """⑤⑥聚合：基差 + PCR + 席位摘要(IF/IO 净持仓变化Top3)"""
    out, notes = {}, []
    try:
        out["basis"] = basis_section(day)
        notes += [f"[basis] {n}" for n in out["basis"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["basis"] = None
        notes.append(f"[basis] 失败: {e}")
    try:
        out["pcr"] = pcr_section(day)
        notes += [f"[pcr] {n}" for n in out["pcr"].pop("notes", [])]
    except Exception as e:  # noqa: BLE001
        out["pcr"] = None
        notes.append(f"[pcr] 失败: {e}")
    seats: dict[str, dict | None] = {}
    for v in ("IF", "IO"):
        try:
            if v == "IF":
                df_map = get_cffex_rank_table(day.strftime("%Y%m%d"), vars_list=["IF"])
                df = (
                    pd.concat(list(df_map.values()), ignore_index=True)
                    if df_map
                    else None
                )
                actual = day
            else:
                df, actual = option_rank_with_fallback("IO", day)
            if df is None or df.empty:
                raise ValueError("无席位数据")
            s = _summarize_members(df)
            seats[v] = {
                "date": str(actual),
                "top_net_long_chg": s[:3],
                "top_net_short_chg": list(reversed(s[-3:])),
            }
        except Exception as e:  # noqa: BLE001
            seats[v] = None
            notes.append(f"[seats:{v}] 失败: {e}")
    out["seats"] = seats
    if out["basis"] is None and out["pcr"] is None and not any(seats.values()):
        raise ValueError("基差/PCR/席位均失败")
    return {"date": str(day), **out, "notes": notes}
