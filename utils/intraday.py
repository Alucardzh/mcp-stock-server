#!/usr/bin/env python3
"""
个股分钟分时 + 竞价分时（含 CSV 归档）。

数据源：
- ef_stock_hist(period="1"/"5")  efinance 分钟K线（1分钟仅当日，5分钟可回溯）
- akshare stock_zh_a_hist_min_em  分钟线备用（同源东财，列名略异）
- push2 details/get 直连          当日分笔，含 9:15-9:25 竞价虚拟撮合过程
                                  （efinance 封装丢弃第5段方向字段，故直连）

竞价归档：{AUCTION_ARCHIVE_ROOT}/{code}/{date}.csv。当日首次拉取落盘；
历史日期只读归档（免费源无历史档，无法回补）。
"""

from datetime import date as date_type, datetime
import logging
import os
from pathlib import Path

import pandas as pd
import requests

from akshare import stock_zh_a_hist_min_em

from .ef import ef_stock_hist
from .review_common import json_err, json_ok, parse_day, safe_num
from .tools import RateLimiter, with_retry
from .validate import validate_stock_symbol

logger = logging.getLogger(__name__)

AUCTION_ARCHIVE_ROOT = Path(
    os.getenv("AUCTION_ARCHIVE_ROOT")
    or (Path(__file__).resolve().parent.parent / "data" / "auction")
)


def limit_price(prev_close, code: str, name: str):
    """按板块规则推算涨停价：北交±30%、创业/科创±20%、主板ST±5%、其余±10%。"""
    if not prev_close:
        return None
    if code.startswith(("83", "87", "88", "43", "92")):
        pct = 0.30
    elif code.startswith(("300", "301", "302", "688", "689")):
        pct = 0.20
    elif "ST" in (name or "").upper():
        pct = 0.05
    else:
        pct = 0.10
    return round(prev_close * (1 + pct), 2)


def _prev_close(symbol: str, day: date_type):
    """day 之前最近一根日K的收盘（竞价摘要的昨收来源）"""
    try:
        df = ef_stock_hist(symbol, period="daily", end_date=day.strftime("%Y%m%d"))
    except Exception as e:  # noqa: BLE001
        logger.warning("daily hist for prev_close failed %s: %s", symbol, e)
        return None
    if df is None or df.empty:
        return None
    hist = df[pd.to_datetime(df["日期"]).dt.date < day]
    if hist.empty:
        hist = df
    return safe_num(hist.iloc[-1]["收盘"])


def _minute_bars(symbol: str, day: date_type, period: str):
    """分钟bar（efinance 主 / akshare 备）；时间列统一为 日期（'YYYY-MM-DD HH:MM'）"""
    ymd = day.strftime("%Y%m%d")
    df = None
    try:
        df = ef_stock_hist(symbol, period=period, start_date=ymd, end_date=ymd)
    except Exception as e:  # noqa: BLE001
        logger.warning("efinance minute hist failed %s: %s", symbol, e)
    if df is None or df.empty:
        try:
            df = stock_zh_a_hist_min_em(
                symbol=symbol,
                period=period,
                start_date=f"{ymd} 09:00:00",
                end_date=f"{ymd} 15:30:00",
            )
            if df is not None and not df.empty and "时间" in df.columns:
                df = df.rename(columns={"时间": "日期"})
        except Exception as e:  # noqa: BLE001
            logger.warning("akshare minute hist failed %s: %s", symbol, e)
            return None
    if df is None or df.empty:
        return None
    return df


def _bar_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        t = str(r["日期"])
        rows.append(
            {
                "time": t[11:16] if len(t) >= 16 else t,
                "open": safe_num(r.get("开盘")),
                "close": safe_num(r.get("收盘")),
                "high": safe_num(r.get("最高")),
                "low": safe_num(r.get("最低")),
                "volume": safe_num(r.get("成交量"), 0),
                "amount": safe_num(r.get("成交额"), 0),
            }
        )
    return rows


def _seg(bars: list[dict]) -> dict:
    return {
        "high": max((b["high"] for b in bars if b["high"] is not None), default=None),
        "low": min((b["low"] for b in bars if b["low"] is not None), default=None),
        "close": bars[-1]["close"],
        "volume": safe_num(sum(b["volume"] or 0 for b in bars), 0),
        "amount_yi": safe_num(sum(b["amount"] or 0 for b in bars) / 1e8, 2),
    }


def intraday_summary(symbol: str, name: str, df: pd.DataFrame, zt_price) -> dict:
    bars = _bar_rows(df)
    first, last = bars[0], bars[-1]
    n30 = min(30, len(bars))
    his = [b for b in bars if b["high"] is not None]
    los = [b for b in bars if b["low"] is not None]
    hi = max(his, key=lambda b: b["high"]) if his else None
    lo = min(los, key=lambda b: b["low"]) if los else None
    if zt_price:
        touch = [b["time"] for b in bars if (b["high"] or 0) >= zt_price]
    else:
        touch = []
    return {
        "symbol": symbol,
        "name": name,
        "open": first["open"],  # 集合竞价撮合价
        "auction_vol_hand": first["volume"],
        "open_30min": _seg(bars[:n30]),
        "tail_30min": _seg(bars[-n30:]),
        "day_high": {"price": hi["high"], "time": hi["time"]} if hi else None,
        "day_low": {"price": lo["low"], "time": lo["time"]} if lo else None,
        "close": last["close"],
        "limit_price": zt_price,
        "limit_touch": {
            "first": touch[0] if touch else None,
            "last": touch[-1] if touch else None,
            "count": len(touch),
        },
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_stock_intraday(symbol: str, period: str = "1", mode: str = "summary", date: str = "") -> str:
    """查询个股分钟分时（1分钟仅当日；5分钟可回溯历史）

    Args:
        symbol: 6位股票代码
        period: "1"或"5"（分钟粒度）
        mode: summary=关键统计摘要 / full=全量分钟bar（1分钟约240行，token大慎用）
        date: YYYY-MM-DD，默认今天
    """
    try:
        symbol = validate_stock_symbol(symbol)
        if period not in ("1", "5"):
            return json_err('period 仅支持 "1"/"5"')
        if mode not in ("summary", "full"):
            return json_err('mode 仅支持 "summary"/"full"')
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        df = _minute_bars(symbol, day, period)
        if df is None or df.empty:
            if period == "1" and day < datetime.now().date():
                return json_err("1分钟分时仅支持当日；历史回溯请用 period=5")
            return json_err(f"{symbol} 在 {day} 无分钟数据(可能非交易日)")
        name = str(df.iloc[0].get("股票名称") or "") if "股票名称" in df.columns else ""
        zt = limit_price(_prev_close(symbol, day), symbol, name)
        if mode == "full":
            return json_ok(
                {"date": str(day), "period": f"{period}min", "limit_price": zt, "bars": _bar_rows(df)}
            )
        return json_ok({"date": str(day), **intraday_summary(symbol, name, df, zt)})
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_intraday: %s", e)
        return json_err(f"查询分钟分时失败: {e}")


def _secid(symbol: str) -> str:
    return f"{1 if symbol.startswith('6') else 0}.{symbol}"


def _fetch_deal_details(symbol: str):
    """当日全量分笔（push2 details/get 直连；requests 已被 patch 接管走代理）

    返回 (pre_price, details)；detail 为原始五段字符串 "时间,价格,成交量,单数,方向"。
    pos=-1000000 即从头返回（竞价在响应头部）；流动性极高的个股响应可达数 MB，
    仅按需调用可接受。
    """
    params = {
        "secid": _secid(symbol),
        "fields1": "f1,f2,f3,f4,f5",
        "fields2": "f51,f52,f53,f54,f55",
        "pos": "-1000000",
    }
    r = requests.get(
        "https://push2.eastmoney.com/api/qt/stock/details/get",
        params=params,
        timeout=20,
    )
    data = r.json().get("data") or {}
    return data.get("prePrice"), data.get("details") or []


_AUCTION_HEADER = ["时间", "价格", "成交量", "单数", "方向"]


def _auction_rows(details) -> list[list[str]]:
    """切出 9:15-9:25 竞价段（含 09:25:00 撮合行）"""
    return [d.split(",") for d in details if d[:5] <= "09:25"]


def _archive_path(symbol: str, day: date_type) -> Path:
    return AUCTION_ARCHIVE_ROOT / symbol / f"{day}.csv"


def _read_archive(symbol: str, day: date_type):
    p = _archive_path(symbol, day)
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str).fillna("").values.tolist()


def _write_archive(symbol: str, day: date_type, rows: list[list[str]]):
    p = _archive_path(symbol, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=_AUCTION_HEADER).to_csv(p, index=False, encoding="utf-8")


def _auction_summary(symbol: str, pre_close, rows: list[list[str]]) -> dict:
    seg1 = [r for r in rows if r[0] < "09:20:00"]
    seg2 = [r for r in rows if "09:20:00" <= r[0] < "09:25:00"]
    final = rows[-1] if rows else None
    match_price = safe_num(float(final[1])) if final else None
    match_vol = safe_num(float(final[2]), 0) if final else None

    def _end(seg):
        return (
            {"price": safe_num(float(seg[-1][1])), "cum_vol": safe_num(float(seg[-1][2]), 0)}
            if seg
            else {"price": None, "cum_vol": None}
        )

    return {
        "symbol": symbol,
        "pre_close": safe_num(float(pre_close)) if pre_close else None,
        "match_price": match_price,  # 09:25 撮合价 = 当日开盘价
        "match_vol_hand": match_vol,  # 竞价匹配量（手，累计口径）
        "est_match_amount_wan": safe_num(match_price * match_vol * 100 / 1e4)
        if match_price and match_vol
        else None,
        "open_gap_pct": safe_num((match_price / float(pre_close) - 1) * 100, 2)
        if match_price and pre_close
        else None,
        "track": {
            "0915_0920": {**_end(seg1), "note": "可撤单段，假单噪音大"},
            "0920_0925": {**_end(seg2), "note": "不可撤单，真实意愿段"},
        },
        "rows": len(rows),
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=2, delay=1.0)
def get_stock_auction(symbol: str, date: str = "", mode: str = "summary") -> str:
    """查询个股集合竞价分时（9:15-9:25 虚拟撮合过程）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD 默认今天。当日实时拉取并落盘归档；历史日期读 CSV 归档
              （免费源仅当日可拉，无归档的历史无法回补）
        mode: summary=三段轨迹+撮合统计 / detail=竞价段逐笔快照
    """
    try:
        symbol = validate_stock_symbol(symbol)
        if mode not in ("summary", "detail"):
            return json_err('mode 仅支持 "summary"/"detail"')
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        pre_close, rows = None, _read_archive(symbol, day)
        source = "archive"
        if rows is None:
            if day != datetime.now().date():
                return json_err(f"{day} 无竞价归档（免费源仅当日可拉，历史无法回补）")
            pre_close, details = _fetch_deal_details(symbol)
            rows = _auction_rows(details)
            if not rows:
                return json_err(f"{symbol} 当日无竞价分笔数据(可能非交易日)")
            _write_archive(symbol, day, rows)
            source = "live"
        out: dict = {"date": str(day), "source": source}
        if mode == "detail":
            out["items"] = [dict(zip(_AUCTION_HEADER, r)) for r in rows]
            return json_ok(out)
        if pre_close is None:
            pre_close = _prev_close(symbol, day)  # 归档不含昨收，从日K补
        out.update(_auction_summary(symbol, pre_close, rows))
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_auction: %s", e)
        return json_err(f"查询竞价分时失败: {e}")
