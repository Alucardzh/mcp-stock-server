#!/usr/bin/env python3
"""fupan_v1：晚班复盘一站式接口（十段 sections，每段独立降级）。

- 段并行 ThreadPoolExecutor；失败段 → {"status":"unavailable","reason"}，其余照常；
- NOT_TRADE_DAY：严格日期下 指数+涨停池 全空；NOT_READY：当日 <15:02 调用；
- klines 默认取当日涨停池全量代码 ∪ 显式传入；chains 由 chain_buckets(hybk 集合)
  从 zt/zb 池聚合；
- 整包缓存 TTL 300（当日收盘后数据小变；lhb 18:00 定稿由其自身缓存控制）。
- v1 冻结：加字段/改口径 → fupan_v2（spec §6.4）。

限流说明：RateLimiter 挂在取数路径（校验/缓存命中不计额）——参数校验错误与
缓存命中是廉价路径，不应消耗配额；brief 参考实现挂外层会把自身测试打穿。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date as date_type, datetime
import json
import logging

from .board_quotes import fupan_boards_section
from .kline_metrics import batch_klines
from .lhb_aggregate import lhb_aggregate
from .push2_client import now_iso
from .review_common import json_err, json_err_code, json_not_ready, json_ok, parse_day
from .review_derivatives import derivatives_section
from .review_funds import fund_flow_section
from .review_market import (
    breadth_section,
    indices_section,
    industry_counts,
    pool_full_snapshot,
)
from .tools import CachedData, RateLimiter

logger = logging.getLogger(__name__)

_fupan_cache: dict[str, CachedData] = {}
_READY_HOUR, _READY_MINUTE = 15, 2

_DEFAULT_OPTIONS = {
    "with_pools": True,
    "with_boards": True,
    "with_klines": True,
    "with_lhb": True,
    "with_breadth": True,
    "with_fund_flow": True,
    "with_derivatives": False,
}


def _is_not_ready(day: date_type) -> bool:
    if day != datetime.now().date():
        return False
    now = datetime.now()
    return (now.hour, now.minute) < (_READY_HOUR, _READY_MINUTE)


def _chain_section(chain_buckets: dict, zt_items: list, zb_items: list) -> dict:
    """链强度：{链名: {buckets, limitup, zhaban, total, rate, members}}。"""
    out = {}
    for chain, buckets in (chain_buckets or {}).items():
        bset = set(buckets)
        zt = [i for i in zt_items if i.get("hybk") in bset]
        zb = [i for i in zb_items if i.get("hybk") in bset]
        limitup, zhaban = len(zt), len(zb)
        total = limitup + zhaban
        out[chain] = {
            "buckets": sorted(bset),
            "limitup": limitup,
            "zhaban": zhaban,
            "total": total,
            "rate": round(zhaban / total * 100, 2) if total else 0.0,
            "members": [
                {
                    "code": i.get("code"),
                    "name": i.get("name"),
                    "lianban": i.get("lianban"),
                }
                for i in zt
            ],
        }
    return out


def _bucket_section(day: date_type) -> dict:
    """每个 hybk 桶的涨停/炸板/合计/炸板率（涨停+炸板分桶合并）。"""
    zt = industry_counts("涨停", day) or {}
    zb = industry_counts("炸板", day) or {}
    out = {}
    for hybk in set(zt) | set(zb):
        limitup, zhaban = zt.get(hybk, 0), zb.get(hybk, 0)
        total = limitup + zhaban
        out[hybk] = {
            "limitup": limitup,
            "zhaban": zhaban,
            "total": total,
            "rate": round(zhaban / total * 100, 2) if total else 0.0,
        }
    return out


def _merge_options(options: dict | None) -> dict:
    allowed = set(_DEFAULT_OPTIONS) | {"chain_buckets", "kline_bars"}
    return {
        **_DEFAULT_OPTIONS,
        **{k: v for k, v in (options or {}).items() if k in allowed},
    }


def _cache_key(day: date_type, symbols, opts: dict) -> str:
    return (
        f"{day}|{','.join(sorted(symbols))}|"
        f"{json.dumps(opts, sort_keys=True, ensure_ascii=False)}"
    )


def _unavailable(reason) -> dict:
    return {
        "status": "unavailable",
        "reason": (str(reason) if reason else "upstream_error")[:120],
    }


@RateLimiter(max_calls=6, time_window=60)
def _fupan_v1_fetch(day: date_type, symbols: list[str], opts: dict) -> str:
    """取数路径（校验与缓存命中已在外层完成，此处才计限流额度）。"""
    # 先取涨停池（klines 默认集 + NOT_TRADE_DAY 判定都要它）
    zt_snap = pool_full_snapshot("涨停", day)
    zb_snap = pool_full_snapshot("炸板", day) if opts["with_pools"] else None
    try:
        indices = indices_section(day)
        indices_err = None
    except Exception as e:  # noqa: BLE001
        indices = None
        indices_err = e
    if zt_snap is None and indices is None:
        return json_err_code("NOT_TRADE_DAY", f"{day} 无涨停池与指数数据(可能非交易日)")

    bad = {str(s) for s in symbols if not (str(s).isdigit() and len(str(s)) == 6)}
    warnings = [f"代码格式异常: {s}" for s in sorted(bad)]
    kline_symbols = sorted(
        (set(symbols) - bad)
        | {i["code"] for i in (zt_snap or {}).get("items", []) if i.get("code")}
    )

    def run(name, fn):
        try:
            val = fn()
            return name, val if not isinstance(val, str) else json.loads(val), None
        except Exception as e:  # noqa: BLE001
            logger.warning("fupan section %s failed: %s", name, e)
            return name, None, e

    # jobs 必须在函数体内引用模块全局名（review.py 模式），测试 monkeypatch 才生效
    jobs = []
    if opts["with_breadth"]:
        jobs.append(("breadth", lambda: breadth_section(day)))
    if opts["with_fund_flow"]:
        jobs.append(("fund_flow", lambda: fund_flow_section(day)))
    if opts["with_derivatives"]:
        jobs.append(("derivatives", lambda: derivatives_section(day)))
    if opts["with_boards"]:
        jobs.append(("boards", fupan_boards_section))
    if opts["with_lhb"]:
        jobs.append(("lhb", lambda: lhb_aggregate(str(day), [])))
    if opts["with_klines"]:
        bars = int(opts.get("kline_bars", 130))
        jobs.append(("klines", lambda: batch_klines(kline_symbols, str(day), bars)))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda nf: run(nf[0], nf[1]), jobs))

    sections = {
        "indices": indices if indices is not None else _unavailable(indices_err),
        "pools": (
            {
                "zt": zt_snap,
                "zb": zb_snap,
                "prev_zt": pool_full_snapshot("昨涨停", day),
                "dt": pool_full_snapshot("跌停", day),
            }
            if opts["with_pools"]
            else {"status": "skipped", "reason": "with_pools=false"}
        ),
        "buckets": _bucket_section(day) if opts["with_pools"] else {},
        "chains": _chain_section(
            opts.get("chain_buckets") or {},
            (zt_snap or {}).get("items", []),
            (zb_snap or {}).get("items", []),
        ),
    }
    any_stale = bool(zt_snap and zt_snap.get("stale")) or bool(
        zb_snap and zb_snap.get("stale")
    )
    any_cached = bool(zt_snap and zt_snap.get("cached")) and (
        zb_snap is None or zb_snap.get("cached")
    )
    for name, val, err in results:
        if err is not None or val is None:
            sections[name] = _unavailable(err or "upstream_error")
            continue
        if name == "klines":  # {code: 指标} 直出（v2 §6.3），warnings 上浮到顶层
            warnings.extend(val.get("warnings", []))
            val = val.get("items", {})
        sections[name] = val
        if isinstance(val, dict):
            any_stale = any_stale or bool(val.get("stale"))
            any_cached = any_cached and bool(val.get("cached", True))
    # 跌停池 >30 天上游限制 → 历史日期补降级标记（探测确认后保留）
    if (
        opts["with_pools"]
        and sections["pools"].get("dt") is None
        and day < datetime.now().date()
    ):
        sections["pools"]["dt"] = {
            "status": "unavailable",
            "reason": "dt_pool_30d_limit",
        }

    data = {
        "schema_version": "v1",
        "date": str(day),
        "as_of": now_iso(),
        "stale": any_stale,
        "cached": False,
        "sections": sections,
    }
    if warnings:
        data["warnings"] = warnings
    out = json_ok(data)
    _fupan_cache[_cache_key(day, symbols, opts)] = CachedData(out, ttl=300)
    return out


def fupan_v1(date: str = "", symbols: list[str] = [], options: dict = {}) -> str:
    """fupan_v1：晚班复盘一站式（指数/宽度/资金流/三池/桶/板块/链/个股指标/龙虎榜/衍生品）

    Args:
        date: 复盘日 YYYY-MM-DD（收盘口径；非交易日报 NOT_TRADE_DAY）
        symbols: 需要个股指标的代码（与当日涨停池全量合并去重）
        options: with_* 开关 / chain_buckets {链名:[hybk...]} / kline_bars(默认130)
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err_code(
                "INVALID_ARG", f"日期格式错误: {date}，请使用 YYYY-MM-DD"
            )
        if day > datetime.now().date():
            return json_err_code("INVALID_ARG", f"查询日期 {day} 晚于今天")
        if _is_not_ready(day):
            return json_not_ready("15:05")

        sym = [str(s) for s in (symbols or [])]
        opts = _merge_options(options)
        key = _cache_key(day, sym, opts)
        cached = _fupan_cache.get(key)
        if cached is not None and not cached.is_expired():
            payload = json.loads(cached.data)
            payload["data"]["cached"] = True
            return json.dumps(payload, ensure_ascii=False, indent=2)
        return _fupan_v1_fetch(day, sym, opts)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in fupan_v1: %s", e)
        return json_err(f"fupan_v1 失败: {e}")
