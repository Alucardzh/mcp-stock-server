#!/usr/bin/env python3
"""复盘模块共享小工具：数值安全转换、JSON 信封、日期解析与交易日回退。"""

from datetime import date as date_type, datetime, timedelta
import json


def safe_num(value, ndigits: int = 2):
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


def json_ok(data) -> str:
    return json.dumps({"success": True, "data": data}, ensure_ascii=False, indent=2)


def json_err(msg: str) -> str:
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False, indent=2)


def parse_day(date_str: str) -> date_type | None:
    """解析 YYYY-MM-DD；空串=今天；格式非法返回 None（不判断未来，由调用方检查）"""
    s = (date_str or "").strip()
    if not s:
        return datetime.now().date()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def norm_date(s) -> str:
    """日期归一化为 YYYY-MM-DD：兼容 '2026-09-02' / '20260902' / int"""
    t = str(s).strip()
    if "-" in t:
        return t[:10]
    if len(t) >= 8 and t[:8].isdigit():
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    return t


def prev_trading_days(day: date_type, n: int = 6) -> list[date_type]:
    """返回 day 之前最近的 n 个工作日（跳过周末，不含节假日判断）"""
    out, d = [], day - timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def col_like(df, keyword: str) -> str | None:
    """按子串找列名（各数据源指标前缀随 indicator 变化，不能精确匹配）"""
    for c in df.columns:
        if keyword in str(c):
            return c
    return None
