#!/usr/bin/env python3
"""
复盘·美债收益率模块：美债 2Y/10Y/30Y + 10Y-2Y 利差（FedWatch 简版数据源）。

数据源：bond_zh_us_rate（东财 datacenter 直连零积分，日频，19页分页约4s，
内部 tqdm 进度条需 redirect_stderr 静默）。
"""

import contextlib
import io
import logging

import pandas as pd
from akshare import bond_zh_us_rate

from .review_common import col_like, json_err, json_ok, safe_num
from .tools import CachedData, RateLimiter, with_retry

logger = logging.getLogger(__name__)

_us_treasury_cache: CachedData | None = None


def _us_rows() -> pd.DataFrame:
    """美债收益率全序列（缓存 3600s；akshare 内部 tqdm 进度条写 stderr，需静默）"""
    global _us_treasury_cache
    if _us_treasury_cache is not None and not _us_treasury_cache.is_expired():
        return _us_treasury_cache.data
    with contextlib.redirect_stderr(io.StringIO()):
        df = bond_zh_us_rate()
    if df is None or df.empty:
        raise ValueError("美债收益率接口无数据")
    df = df.copy()
    df["_d"] = df[col_like(df, "日期")].astype(str).str[:10]
    _us_treasury_cache = CachedData(df, ttl=3600)
    return df


def us_treasury_section(day=None) -> dict:
    """美债收益率：day=None 取最新；返回收益率%、bp 日变动"""
    df = _us_rows()
    if day is None:
        idx = df.index[-1]
    else:
        hit = df[df["_d"] == str(day)]
        if hit.empty:
            raise ValueError(f"{day} 无美债收益率数据(可能非交易日)")
        idx = hit.index[-1]
    r = df.loc[idx]
    prev = df.loc[idx - 1] if idx > df.index[0] else None

    def _get(row, keyword):
        c = col_like(df, keyword)
        try:
            return float(row[c]) if c else None
        except (TypeError, ValueError):
            return None

    def _bp(keyword):
        cur = _get(r, keyword)
        p = _get(prev, keyword) if prev is not None else None
        if cur is not None and p is not None:
            return round((cur - p) * 100, 1)
        return None

    return {
        "date": r["_d"],
        "us2y": safe_num(_get(r, "美国国债收益率2年"), 3),
        "us10y": safe_num(_get(r, "美国国债收益率10年"), 3),
        "us10y_chg_bp": _bp("美国国债收益率10年"),
        "us30y": safe_num(_get(r, "美国国债收益率30年"), 3),
        "us30y_chg_bp": _bp("美国国债收益率30年"),
        "spread_10y_2y": safe_num(_get(r, "美国国债收益率10年-2年"), 3),
        "notes": ["东财日频口径(日期=美东交易日)"],
    }


@RateLimiter(max_calls=10, time_window=60)
@with_retry(max_retries=3, delay=1.0, backoff=2.0)
def get_fed_watch() -> str:
    """美联储预期简版：美债 2Y 收益率 + 10Y-2Y 利差 + 2Y 周变动(bp)"""
    try:
        df = _us_rows()
        last = df.iloc[-1]
        first = df.index[0]
        week_ago = df.loc[max(df.index[-1] - 5, first)]
        c2 = col_like(df, "美国国债收益率2年")

        def _v(row):
            try:
                return float(row[c2])
            except (TypeError, ValueError):
                return None

        cur, prev = _v(last), _v(week_ago)
        sec = us_treasury_section()
        sec.pop("notes", None)
        data = {
            **sec,
            "us2y_chg_1w_bp": (
                round((cur - prev) * 100, 1)
                if cur is not None and prev is not None
                else None
            ),
            "note": "预期变化请结合 FOMC 日程解读(不做概率推算)",
        }
        return json_ok(data)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_fed_watch: %s", e)
        return json_err(f"查询美债预期失败: {e}")
