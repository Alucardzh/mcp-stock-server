#!/usr/bin/env python3
"""板块行情：R1 单板块开盘涨跌幅 + R2 板块清单排行。

上游 = 东财 push2（经 utils.push2_client → akproxy）：
- clist/get m:90 板块列表（f17/f18 若板块支持则免额外开盘请求）；
- ulist.np/get 批量开盘价 f46/f60，不支持板块 secid 时自动退化逐板块 stock/get
  （stock/get 的 fields 必须全集，精简会"假失败"返回空 data——需求文档坑1）；
- 开盘涨跌幅 = (f46-f60)/f60*100；f46 缺失（未开盘/停牌）→ open_pct=null + status=no_open。

board_name 一律东财原文透传（含截断名/罗马数字），禁止规范化。
limit_up（板块内涨停家数）来自涨停池所属行业分桶（review_market.zt_industry_counts，
4 字截断名前缀匹配），算不出时省略字段。
"""

import logging

from .push2_client import CACHE, EmResult, em_push2_get, now_iso
from .review_common import (
    json_err,
    json_err_code,
    json_ok,
    json_unavailable,
    parse_day,
)
from .review_market import zt_industry_counts
from .tools import RateLimiter

logger = logging.getLogger(__name__)

BOARD_LIST_TTL = 60.0  # 板块清单缓存（秒）
BOARD_OPEN_TTL = 60.0  # 开盘价缓存（秒）
_ULIST_CHUNK = 100  # GET URL 长度安全分块

ulist_ok: bool | None = (
    None  # None=未探测；False=ulist 不支持板块 secid，已退化 stock/get
)

_CLIST_FIELDS = "f12,f14,f2,f3,f17,f18,f62,f104,f105,f128,f140,f141"
_STOCK_GET_FIELDS = (
    "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f62,f104,f105,f107,f168,f169"
)
_SCOPE_FS = {"行业": "m:90+t:2", "概念": "m:90+t:3"}
_SOURCE = "eastmoney_push2_clist"


def _f(v) -> float | None:
    """fltt=2 字段安全转 float：'-' / None / '' → None。"""
    try:
        if v in (None, "-", ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> int | None:
    n = _f(v)
    return int(n) if n is not None else None


def _normalize_board_row(scope: str, raw: dict) -> dict:
    """clist 行 → 规范化行。_ 前缀键为内部缓存字段，输出前剥离。"""
    main = _f(raw.get("f62"))
    return {
        "_scope": scope,
        "board_code": str(raw.get("f12", "")),
        "board_name": str(raw.get("f14", "")),  # 原文透传
        "current_pct": _f(raw.get("f3")),
        "main_net_yi": round(main / 1e8, 2) if main is not None else None,
        "up": _int_or_none(raw.get("f104")),
        "down": _int_or_none(raw.get("f105")),
        "leader_name": str(raw.get("f128") or "") or None,
        "leader_code": str(raw.get("f140") or "") or None,
        "leader_pct": _f(raw.get("f141")),
        "_open": _f(raw.get("f17")),
        "_prev_close": _f(raw.get("f18")),
    }


def fetch_board_list(scope: str) -> EmResult:
    """板块清单快照（含主力资金/涨跌家数/领涨股；缓存 60s，stale 降级）。

    data = {"scope", "boards": [规范化行], "by_code", "by_name", "total"}
    """
    params = {
        "pn": 1,
        "pz": 600,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": _SCOPE_FS[scope],
        "fields": _CLIST_FIELDS,
    }

    def _fetch() -> EmResult:
        res = em_push2_get("/api/qt/clist/get", params)
        if not res.ok:
            return res
        diff = (res.data or {}).get("diff") or []
        boards = [
            _normalize_board_row(scope, r)
            for r in diff
            if str(r.get("f12", "")).startswith("BK")
        ]
        snapshot = {
            "scope": scope,
            "boards": boards,
            "by_code": {b["board_code"]: b for b in boards},
            "by_name": {b["board_name"]: b for b in boards},
            "total": len(boards),
        }
        return EmResult(ok=True, data=snapshot, as_of=res.as_of)

    return CACHE.get(f"board_list:{scope}", BOARD_LIST_TTL, _fetch)


def _fetch_open_ulist(codes: list[str]) -> EmResult:
    """ulist 批量开盘价（分块多请求合并）。data = {code: {open, prev_close}}。"""
    merged, as_of = {}, ""
    for i in range(0, len(codes), _ULIST_CHUNK):
        chunk = codes[i : i + _ULIST_CHUNK]
        res = em_push2_get(
            "/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "invt": 2,
                "secids": ",".join(f"90.{c}" for c in chunk),
                "fields": "f12,f46,f60",
            },
        )
        if not res.ok:
            return res
        as_of = as_of or res.as_of
        for r in (res.data or {}).get("diff") or []:
            code = str(r.get("f12", ""))
            open_, prev = _f(r.get("f46")), _f(r.get("f60"))
            if code and (open_ is not None or prev is not None):
                merged[code] = {"open": open_, "prev_close": prev}
    return EmResult(ok=True, data=merged, as_of=as_of)


def _fetch_open_stock_get(codes: list[str]) -> EmResult:
    """逐板块 stock/get 兜底（fields 全集，见模块 docstring）。"""
    merged, as_of = {}, ""
    for c in codes:
        res = em_push2_get(
            "/api/qt/stock/get",
            {"fltt": 2, "invt": 2, "fields": _STOCK_GET_FIELDS, "secid": f"90.{c}"},
        )
        if not res.ok:
            return res
        as_of = as_of or res.as_of
        d = res.data or {}
        merged[c] = {"open": _f(d.get("f46")), "prev_close": _f(d.get("f60"))}
    return EmResult(ok=True, data=merged, as_of=as_of)


def fetch_board_open(codes: list[str]) -> EmResult:
    """板块开盘价/昨收（缓存 60s；data = {code: {open, prev_close}}）。

    优先 ulist 批量；ulist ok 但一行都没有 → 判定不支持板块 secid，进程内退化
    stock/get（真·上游失败 rate_limited/upstream_error 原样返回，不切通道）。
    注：盘前 f46 可能整批为 '-'，此时退化后结果同样为空，行为收敛无副作用。
    """
    codes = sorted({c for c in codes if c})
    if not codes:
        return EmResult(ok=True, data={}, as_of=now_iso())

    def _fetch() -> EmResult:
        global ulist_ok
        if ulist_ok is not False:
            res = _fetch_open_ulist(codes)
            if res.ok:
                if res.data:
                    ulist_ok = True
                    return res
                ulist_ok = False  # ok 但 0 行 → 通道不支持
            else:
                return res
        return _fetch_open_stock_get(codes)

    return CACHE.get("board_open:" + ",".join(codes), BOARD_OPEN_TTL, _fetch)


def _limit_up_for(board_name: str, counts: dict | None) -> int | None:
    """池内行业名为 4 字截断原文：精确命中或板块名以截断名开头。"""
    if not counts:
        return None
    if board_name in counts:
        return counts[board_name]
    for hybk, n in counts.items():
        if board_name.startswith(hybk):
            return n
    return None


def _emit_row(row: dict, counts: dict | None) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    limit_up = _limit_up_for(row["board_name"], counts)
    if limit_up is not None:
        out["limit_up"] = limit_up
    return out


def _open_pct(open_: float | None, prev: float | None) -> float | None:
    if open_ is None or not prev:
        return None
    return round((open_ - prev) / prev * 100, 3)


def _lookup(snaps: list[EmResult], code: str, name: str):
    """行业/概念快照里找板块行，返回 (row, scope)；未命中 (None, None)。"""
    if code and not code.startswith("BK"):
        code = f"BK{code}"
    for s in snaps:
        if not s.ok or not s.data:
            continue
        row = s.data["by_code"].get(code) if code else s.data["by_name"].get(name)
        if row is not None:
            return row, s.data["scope"]
    return None, None


@RateLimiter(max_calls=20, time_window=60)
def get_board_quote(board_code: str = "", board_name: str = "") -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（R1）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文，如 元件 / 游戏Ⅱ；都不给则返回全部板块精简表
    """
    code, name = (board_code or "").strip(), (board_name or "").strip()
    try:
        snaps = [fetch_board_list(s) for s in ("行业", "概念")]
        if not any(s.ok for s in snaps):
            reason = next((s.reason for s in snaps if s.reason), "upstream_error")
            return json_unavailable(reason)

        if not code and not name:
            return _boards_digest(snaps)

        row, scope = _lookup(snaps, code, name)
        if row is None:
            return json_err_code("BOARD_NOT_FOUND", f"板块不存在: {code or name}")
        primary = next(s for s in snaps if s.ok and s.data["scope"] == scope)

        counts = zt_industry_counts()
        notes = []
        open_res = fetch_board_open([row["board_code"]])
        if open_res.ok:
            openq = (open_res.data or {}).get(row["board_code"]) or {}
        else:
            openq, notes = {}, [f"开盘数据不可用({open_res.reason})"]
        open_ = openq.get("open", row.get("_open"))
        prev = openq.get("prev_close", row.get("_prev_close"))

        out = _emit_row(row, counts)
        out.update(
            {
                "scope": scope,
                "open": open_,
                "prev_close": prev,
                "open_pct": _open_pct(open_, prev),
                "as_of": primary.as_of,
                "stale": primary.stale or open_res.stale,
                "cached": primary.cached,
                "source": _SOURCE,
            }
        )
        if notes:
            out["notes"] = notes
        if open_ is None and not notes:
            out["status"] = "no_open"  # 正常但当日无开盘（未开盘/停牌）
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_board_quote: %s", e)
        return json_err(f"查询板块行情失败: {e}")


def _boards_digest(snaps: list[EmResult]) -> str:
    """board_code/board_name 都不给：全部行业+概念精简表（按当前涨跌幅降序）。"""
    rows, primary = [], None
    for s in snaps:
        if not s.ok or not s.data:
            continue
        if primary is None:
            primary = s
        for b in s.data["boards"]:
            rows.append(
                {
                    "scope": b["_scope"],
                    "board_code": b["board_code"],
                    "board_name": b["board_name"],
                    "current_pct": b["current_pct"],
                    "main_net_yi": b["main_net_yi"],
                }
            )
    rows.sort(
        key=lambda r: (r["current_pct"] is not None, r["current_pct"] or 0),
        reverse=True,
    )
    return json_ok(
        {
            "boards": rows,
            "total": len(rows),
            "as_of": primary.as_of if primary else now_iso(),
            "stale": any(s.stale for s in snaps if s.ok),
            "cached": all(s.cached for s in snaps if s.ok),
            "source": _SOURCE,
        }
    )


@RateLimiter(max_calls=20, time_window=60)
def get_boards(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
) -> str:
    """查询板块清单排行（R2）：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct
        limit: 返回条数 ≤50，默认 20
        with_open: 是否附带开盘涨跌幅（默认 true；false 省上游请求）
    """
    try:
        if scope not in _SCOPE_FS:
            return json_err(f"scope 仅支持 行业/概念，当前: {scope}")
        if sort not in ("main_net", "chg", "open_pct"):
            return json_err(f"sort 仅支持 main_net/chg/open_pct，当前: {sort}")
        limit = max(1, min(int(limit), 50))

        snap = fetch_board_list(scope)
        if not snap.ok:
            return json_unavailable(snap.reason or "upstream_error")
        boards = list(snap.data["boards"])
        notes = []

        if with_open:
            opens = {}
            if all(b["_open"] is not None for b in boards):
                pass  # clist f17/f18 已齐，免 ulist
            else:
                open_res = fetch_board_open([b["board_code"] for b in boards])
                if open_res.ok:
                    opens = open_res.data or {}
                else:
                    notes.append(f"开盘涨跌幅不可用({open_res.reason})，open_pct 为空")
            for b in boards:
                o = opens.get(b["board_code"]) or {}
                b["open"] = o.get("open", b["_open"])
                b["prev_close"] = o.get("prev_close", b["_prev_close"])
                b["open_pct"] = _open_pct(b["open"], b["prev_close"])
        else:
            for b in boards:
                b["open"] = b["_open"]
                b["prev_close"] = b["_prev_close"]
                b["open_pct"] = _open_pct(b["_open"], b["_prev_close"])

        sort_key = {
            "main_net": lambda b: (b["main_net_yi"] is not None, b["main_net_yi"] or 0),
            "chg": lambda b: (b["current_pct"] is not None, b["current_pct"] or 0),
            "open_pct": lambda b: (b["open_pct"] is not None, b["open_pct"] or 0),
        }[sort]
        boards.sort(key=sort_key, reverse=True)

        counts = zt_industry_counts()
        emit = [_emit_row(b, counts) for b in boards[:limit]]
        return json_ok(
            {
                "scope": scope,
                "sort": sort,
                "total": snap.data["total"],
                "boards": emit,
                "notes": notes,
                "as_of": snap.as_of,
                "stale": snap.stale,
                "cached": snap.cached,
                "source": _SOURCE,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_boards: %s", e)
        return json_err(f"查询板块清单失败: {e}")
