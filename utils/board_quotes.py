#!/usr/bin/env python3
"""板块行情：R1 单板块开盘涨跌幅 + R2 板块清单排行。

上游 = 东财 push2（经 utils.push2_client → akproxy）：
- clist/get m:90 板块列表（单页上限 100 行，pz>100 也只回 100 → pn 翻页拉全量）；
- v2 实测：板块 clist f17/f18 == stock/get f46/f60 == 真实点位（如元件
  prev_close=49485.34），并非非标刻度；get_boards 不外发 clist 原值属保守
  策略，fupan_boards_section 直接外发（open/prev_close=_open/_prev_close）
  为正确行为；
- ulist.np/get 对板块 secid 的 f46/f60 返回 "-" 占位（不可用），fetch_board_open
  自动退化逐板块 stock/get（其 fields 必须全集，精简会"假失败"返回空 data——坑1）；
- 开盘涨跌幅 = (f46-f60)/f60*100；f46 缺失（未开盘/停牌）→ open_pct=null + status=no_open。

board_name 一律东财原文透传（含截断名/罗马数字），禁止规范化。
limit_up（板块内涨停家数）来自涨停池所属行业分桶（review_market.zt_industry_counts，
4 字截断名前缀匹配），算不出时省略字段。
"""

import json
import logging
from datetime import datetime
from os import getenv

from .push2_client import CACHE, EmResult, em_push2_get, now_iso
from .review_common import (
    json_err,
    json_err_code,
    json_ok,
    json_unavailable,
)
from .review_market import zt_industry_counts
from .tools import RateLimiter

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """入口限流配额 env 覆写（v4 §11.1-3①：阈值可配置）；非法回退默认。"""
    try:
        return max(1, int(getenv(name, str(default))))
    except ValueError:
        return default


# 板块清单缓存（秒）：60→300——拉全量分页后 TTL 过短导致重复冷取（v4 §11.1-3③）
BOARD_LIST_TTL = float(getenv("EM_BOARD_LIST_TTL", "300") or 300)
BOARD_OPEN_TTL = 60.0  # 09:25 前：竞价虚拟撮合价仍在变
BOARD_OPEN_TTL_POST = 3600.0  # 09:25 后：开盘价当日定稿（prev_close 恒不变）
_ULIST_CHUNK = 100  # GET URL 长度安全分块
_CLIST_PAGE_SIZE = 100  # clist 单页行数上限（实测 pz>100 也只回 100）
_CLIST_MAX_PAGES = 8  # 翻页安全上限（概念实测 531 板块 → 6 页（8 页上限容许到 800））
_OPEN_BATCH_MAX_MISSING = 20  # R2 批量开盘请求的缺口上限（超过则跳过，见 get_boards）

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


def _open_ttl() -> float:
    """开盘价缓存 TTL（读时判定；条目不存 TTL）。

    09:25 前 60s（虚拟撮合价仍在变）；09:25 后 = min(3600, 距定稿点秒数)——
    竞价期写入的条目 age 恒大于"距定稿点时长"，必然在 09:25 边界过期，
    不会把盘前 no_open/虚拟价带入开盘后。
    """
    now = datetime.now()
    boundary = now.replace(hour=9, minute=25, second=0, microsecond=0)
    if now >= boundary:
        return min(BOARD_OPEN_TTL_POST, (now - boundary).total_seconds())
    return BOARD_OPEN_TTL


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

    clist 单页上限 100 行：pn=1..N 逐页拉取（短页即末页；安全上限 8 页），
    合并全部 diff 行。data = {"scope", "boards", "by_code", "by_name",
    "total"=合并行数, "upstream_total"=上游 total（触 8 页上限时应小于它）}。
    """
    params = {
        "pz": _CLIST_PAGE_SIZE,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": _SCOPE_FS[scope],
        "fields": _CLIST_FIELDS,
    }

    def _fetch() -> EmResult:
        rows, as_of, upstream_total = [], "", None
        for pn in range(1, _CLIST_MAX_PAGES + 1):
            res = em_push2_get("/api/qt/clist/get", {**params, "pn": pn})
            if not res.ok:
                return res
            as_of = as_of or res.as_of
            data = res.data or {}
            if upstream_total is None:
                upstream_total = data.get("total")
            diff = data.get("diff") or []
            rows.extend(diff)
            if len(diff) < _CLIST_PAGE_SIZE:
                break
            if upstream_total and len(rows) >= upstream_total:
                break  # 恰好整页凑满（total%100==0）：下一页必为空页，免打
        boards = [
            _normalize_board_row(scope, r)
            for r in rows
            if str(r.get("f12", "")).startswith("BK")
        ]
        snapshot = {
            "scope": scope,
            "boards": boards,
            "by_code": {b["board_code"]: b for b in boards},
            "by_name": {b["board_name"]: b for b in boards},
            "total": len(boards),
            "upstream_total": upstream_total,
        }
        return EmResult(ok=True, data=snapshot, as_of=as_of)

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
                merged[code] = {
                    "open": open_,
                    "prev_close": prev,
                    "current": None,
                    "f59": None,
                }
    return EmResult(ok=True, data=merged, as_of=as_of)


def _fetch_open_stock_get(codes: list[str]) -> EmResult:
    """逐板块 stock/get 兜底（fields 全集，见模块 docstring）。

    保底截断：最多取前 20 个（codes 已排序）——逐请求 pacing 1.5s，大量代码
    会拖垮延迟；真正的规模防护在调用方（get_boards 缺口>20 直接跳过批量获取，
    R1 只查单板块）。正常路径不会触达该截断。
    """
    merged, as_of = {}, ""
    for c in codes[:_OPEN_BATCH_MAX_MISSING]:
        res = em_push2_get(
            "/api/qt/stock/get",
            {"fltt": 2, "invt": 2, "fields": _STOCK_GET_FIELDS, "secid": f"90.{c}"},
        )
        if not res.ok:
            return res
        as_of = as_of or res.as_of
        d = res.data or {}
        merged[c] = {
            "open": _f(d.get("f46")),
            "prev_close": _f(d.get("f60")),
            "current": _f(d.get("f43")),
            "f59": _int_or_none(d.get("f59")),
        }
    return EmResult(ok=True, data=merged, as_of=as_of)


def fetch_board_open(codes: list[str]) -> EmResult:
    """板块开盘价/昨收（TTL 时间感知：09:25 前 60s / 后 3600s；
    data = {code: {open, prev_close, current, f59}}）。
    current/f59 仅 stock/get 退化通道有值，ulist 通道恒为 None。

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

    return CACHE.get("board_open:" + ",".join(codes), _open_ttl(), _fetch)


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


def _scale_block(openq: dict) -> dict | None:
    """缩放核对块：fltt=2 值已格式化，raw = round(value × 10^f59) 无损重建。"""
    f59 = openq.get("f59")
    open_, prev = openq.get("open"), openq.get("prev_close")
    if f59 is None or open_ is None or prev is None:
        return None
    divisor = 10 ** int(f59)
    return {
        "raw_f46": int(round(open_ * divisor)),
        "raw_f60": int(round(prev * divisor)),
        "divisor": divisor,
        "f59": int(f59),
    }


def _lookup2(loaded: list, code: str, name: str):
    """精确→前缀两段查找。返回 (row, scope, matched_by, candidates)。

    candidates 非空 = 前缀多命中（row=None，调用方应返回 ambiguous 信封）。
    前缀方向：完整板块名 startswith(查询名)——客户端规则存东财 4 字截断名。
    """
    if code and not code.startswith("BK"):
        code = f"BK{code}"
    ok_snaps = [s for s in loaded if s.ok and s.data]
    for s in ok_snaps:
        row = s.data["by_code"].get(code) if code else s.data["by_name"].get(name)
        if row is not None:
            return row, s.data["scope"], "exact", None
    if name:
        hits = [
            (b, s.data["scope"])
            for s in ok_snaps
            for b in s.data["boards"]
            if str(b["board_name"]).startswith(name)
        ]
        if len(hits) == 1:
            return hits[0][0], hits[0][1], "prefix", None
        if len(hits) > 1:
            cands = [
                {
                    "board_code": b["board_code"],
                    "board_name": b["board_name"],
                    "scope": sc,
                }
                for b, sc in hits
            ]
            return None, None, "prefix", cands
    return None, None, "", None


def _json_ambiguous(name: str, candidates: list) -> str:
    """前缀多命中：缺选择，不是错误——让调用方从候选里二次选择。"""
    return json.dumps(
        {
            "success": True,
            "data": None,
            "status": "ambiguous",
            "query": name,
            "candidates": candidates,
        },
        ensure_ascii=False,
        indent=2,
    )


_LIGHT_FIELDS = {"open", "prev_close", "open_pct", "current"}


def _board_quote_light(code: str, fields: set) -> str:
    """fields 轻量路径：仅 board_code，跳过清单/涨停池，直连开盘价通道。

    无清单无法校验板块存在性：无效代码会得到 open=None（no_open 语义），
    轻量模式约定调用方使用清单/warmup 获得的有效 board_code。
    """
    if not code.startswith("BK"):
        code = f"BK{code}"
    open_res = fetch_board_open([code])
    if not open_res.ok:
        return json_unavailable(open_res.reason)
    openq = (open_res.data or {}).get(code) or {}
    out = {
        "board_code": code,
        "as_of": open_res.as_of,
        "cached": open_res.cached,
        "stale": open_res.stale,
        "source": _SOURCE,
    }
    vals = {
        "open": openq.get("open"),
        "prev_close": openq.get("prev_close"),
        "current": openq.get("current"),
    }
    vals["open_pct"] = _open_pct(vals["open"], vals["prev_close"])
    for f in sorted(fields):
        out[f] = vals[f]
    if vals["open"] is None:
        out["status"] = "no_open"
    return json_ok(out)


def warmup_board_cache(board_codes: list[str] = []) -> str:
    """竞价前预热：双 scope 清单 + 指定板块开盘价 + 涨停池分桶（limit_up 依赖）。"""
    try:
        lists = {}
        for scope in ("行业", "概念"):
            s = fetch_board_list(scope)
            lists[scope] = "ok" if s.ok else f"unavailable({s.reason})"
        codes = sorted({c.strip() for c in board_codes or [] if c and c.strip()})
        opens = {"requested": len(codes), "ok": 0}
        for c in codes:
            cc = c if c.startswith("BK") else f"BK{c}"
            res = fetch_board_open([cc])  # 逐码预热：键与后续单码查询一致
            if res.ok:
                opens["ok"] += 1
            elif "reason" not in opens:
                opens["reason"] = res.reason
        counts = zt_industry_counts()
        return json_ok(
            {
                "lists": lists,
                "opens": opens,
                "pool": "ok" if counts else "unavailable",
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in warmup_board_cache: %s", e)
        return json_err(f"预热板块缓存失败: {e}")


_MEMBERS_FIELDS = "f12,f14,f2,f3"
_MEMBERS_PAGE = 100
_MEMBERS_MAX_PAGES = 20  # 2000 只安全阀（最大板块~500 成分股）


@RateLimiter(max_calls=_env_int("EM_RATE_BOARD_MEMBERS", 20), time_window=60)
def get_board_members(board_code: str, limit: int = 100, offset: int = 0) -> str:
    """板块成分股清单（G6）：分页 + 当日缓存 + truncated 标记

    Args:
        board_code: 东财板块代码，如 BK0459
        limit: 返回条数 ≤600，默认 100
        offset: 跳过前 N 条（分页）
    """
    from datetime import datetime as _dt

    code = (board_code or "").strip()
    if not code:
        return json_err_code("INVALID_ARG", "board_code 不能为空")
    if not code.startswith("BK"):
        code = f"BK{code}"
    try:
        limit = max(1, min(int(limit), 600))
        offset = max(0, int(offset))
        key = f"members:{code}:{_dt.now().date()}"

        def _fetch() -> EmResult:
            rows, as_of, total = [], "", 0
            for pn in range(1, _MEMBERS_MAX_PAGES + 1):
                res = em_push2_get(
                    "/api/qt/clist/get",
                    {
                        "pn": pn,
                        "pz": _MEMBERS_PAGE,
                        "po": 1,
                        "np": 1,
                        "fltt": 2,
                        "invt": 2,
                        "fid": "f3",
                        "fs": f"b:{code}",
                        "fields": _MEMBERS_FIELDS,
                    },
                )
                if not res.ok:
                    return res
                as_of = as_of or res.as_of
                diff = (res.data or {}).get("diff") or []
                if pn == 1:
                    total = int((res.data or {}).get("total") or 0)
                rows.extend(diff)
                if len(diff) < _MEMBERS_PAGE:
                    break
                if total and len(rows) >= total:
                    break  # 恰好整页凑满：下一页必空 data→rate_limited，免打
            if total == 0:
                # 空成分股 = 无效板块代码（不缓存，便于新板块代码日后生效）
                return EmResult(ok=False, reason="not_found")
            return EmResult(ok=True, data={"rows": rows, "total": total}, as_of=as_of)

        res = CACHE.get(key, 86400, _fetch)
        if not res.ok:
            if res.reason == "not_found":
                return json_err_code("BOARD_NOT_FOUND", f"板块不存在或无成分股: {code}")
            return json_unavailable(res.reason)
        rows = res.data["rows"]
        total = res.data["total"]
        items = [
            {
                "code": str(r.get("f12", "")),
                "name": str(r.get("f14", "")),
                "price": _f(r.get("f2")),
                "chg_pct": _f(r.get("f3")),
            }
            for r in rows[offset : offset + limit]
        ]
        has_more = offset + limit < total
        return json_ok(
            {
                "board_code": code,
                "count": total,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
                "truncated": False,
                "items": items,
                "as_of": res.as_of,
                "cached": res.cached,
                "stale": res.stale,
                "source": _SOURCE,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_board_members: %s", e)
        return json_err(f"查询板块成分股失败: {e}")


@RateLimiter(max_calls=_env_int("EM_RATE_BOARD_QUOTE", 40), time_window=60)
def get_board_quote(
    board_code: str = "", board_name: str = "", fields: list[str] = []
) -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（R1）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文或 4 字截断名（前缀匹配：唯一命中标 matched_by，
            多命中返回 status:"ambiguous"+candidates）
            查找顺序：行业快照优先（命中即不拉概念，省 6 页分页）；
            行业前缀唯一命中会短路概念侧的精确命中。
        fields: 轻量模式字段列表，仅 {open, prev_close, open_pct, current} 子集
            且须用 board_code 查询（跳过清单/涨停池，秒级返回）
    """
    code, name = (board_code or "").strip(), (board_name or "").strip()
    try:
        fs = {f.strip() for f in fields or [] if f and f.strip()}
        if fs and not fs <= _LIGHT_FIELDS:
            bad = sorted(fs - _LIGHT_FIELDS)
            return json_err_code(
                "INVALID_ARG",
                f"轻量 fields 仅支持 {sorted(_LIGHT_FIELDS)}，非法: {bad}",
            )
        if fs and name:
            return json_err_code("INVALID_ARG", "轻量模式仅支持 board_code 查询")
        if fs and not code:
            return json_err_code("INVALID_ARG", "轻量模式需要 board_code")
        if fs:
            return _board_quote_light(code, fs)

        if not code and not name:
            snaps = [fetch_board_list(s) for s in ("行业", "概念")]
            if not any(s.ok for s in snaps):
                return json_unavailable(
                    next((s.reason for s in snaps if s.reason), "upstream_error")
                )
            return _boards_digest(snaps)

        # lazy scope：先行业（行业票省 6 页概念分页），未命中/多命中再拉概念
        loaded = [fetch_board_list("行业")]
        row, scope, matched_by, candidates = _lookup2(loaded, code, name)
        if row is None:  # 未命中或多命中：拉概念后跨双 scope 重扫（候选更全）
            loaded.append(fetch_board_list("概念"))
            row, scope, matched_by, candidates = _lookup2(loaded, code, name)
        if candidates:
            return _json_ambiguous(name, candidates)
        if row is None:
            if any(not s.ok for s in loaded):
                # 单 scope 快照失败：板块可能在缺失的那组里，缺数据≠不存在
                return json_unavailable(
                    next((s.reason for s in loaded if not s.ok), "upstream_error")
                )
            return json_err_code("BOARD_NOT_FOUND", f"板块不存在: {code or name}")
        primary = next(s for s in loaded if s.ok and s.data["scope"] == scope)

        counts = zt_industry_counts()
        notes = []
        # 板块开盘价恒走 fetch_board_open（ulist 对板块无效 → 退化 stock/get）；
        # clist f17/f18 为非标刻度，原始值不得回退混入。
        open_res = fetch_board_open([row["board_code"]])
        if open_res.ok:
            openq = (open_res.data or {}).get(row["board_code"]) or {}
        else:
            openq, notes = {}, [f"开盘数据不可用({open_res.reason})"]
        open_ = openq.get("open")
        prev = openq.get("prev_close")

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
                "current": openq.get("current"),
                "scale": _scale_block(openq),
                "source": _SOURCE,
            }
        )
        if matched_by == "prefix":
            out["matched_by"] = "prefix"
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


@RateLimiter(max_calls=_env_int("EM_RATE_BOARDS", 40), time_window=60)
def get_boards(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
    offset: int = 0,
) -> str:
    """查询板块清单排行（R2）：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct / code / name
            （code/name 为字典序升序，便于全量翻页）
        limit: 返回条数 ≤600，默认 20
        with_open: 是否附带开盘涨跌幅（默认 true；false 省上游请求）。
            open_pct 用 clist f17/f18 比值（非标刻度，比值不受影响）；原始
            open/prev_close 仅在真实渠道（stock/get）有值时输出，否则 null。
        offset: 按 sort 排序后跳过前 N 条（分页；越界返回空列表，钳制 ≥0）
    """
    try:
        if scope not in _SCOPE_FS:
            return json_err(f"scope 仅支持 行业/概念，当前: {scope}")
        if sort not in ("main_net", "chg", "open_pct", "code", "name"):
            return json_err(
                f"sort 仅支持 main_net/chg/open_pct/code/name，当前: {sort}"
            )
        limit = max(1, min(int(limit), 600))
        offset = max(0, int(offset))

        snap = fetch_board_list(scope)
        if not snap.ok:
            return json_unavailable(snap.reason or "upstream_error")
        boards = [
            dict(b) for b in snap.data["boards"]
        ]  # 缓存行按引用共享，先浅拷贝再改
        notes = []

        if with_open:
            opens = {}
            missing = [
                b for b in boards if b["_open"] is None or b["_prev_close"] is None
            ]
            if not missing:
                pass  # clist f17/f18 已齐：仅用其比值，免（对板块无效的）ulist 请求
            elif len(missing) <= _OPEN_BATCH_MAX_MISSING:
                # 只打缺失板块：全量会让 stock/get 兜底的 20 只截断挤掉真正
                # 缺口的部分，且白烧 ulist 分块探测。
                open_res = fetch_board_open([b["board_code"] for b in missing])
                if open_res.ok:
                    opens = open_res.data or {}
                else:
                    notes.append(
                        f"开盘涨跌幅获取失败({open_res.reason})，open_pct 为 clist 比值口径"
                    )
            else:
                # 缺口过大（如盘前 f17 整批为 '-'）：逐板块 stock/get 的 pacing 代价
                # 不可接受（概念 500+ 只 ≈ 十几分钟），跳过批量获取，
                # open_pct 一律 clist 比值口径（缺者 None）。
                notes.append(
                    f"开盘价缺口过大(缺{len(missing)}/总{len(boards)})，"
                    "跳过批量获取，open_pct 为 clist 比值口径"
                )
            for b in boards:
                o = opens.get(b["board_code"]) or {}
                # 原始 open/prev_close 仅来自真实渠道（stock/get/ulist）；
                # clist f17/f18 非标刻度不外发，仅用于 open_pct 比值（刻度不变）。
                b["open"] = o.get("open")
                b["prev_close"] = o.get("prev_close")
                b["open_pct"] = _open_pct(
                    o.get("open", b["_open"]), o.get("prev_close", b["_prev_close"])
                )
        else:
            for b in boards:
                b["open"] = None  # clist 非标刻度不外发
                b["prev_close"] = None
                b["open_pct"] = _open_pct(b["_open"], b["_prev_close"])

        sort_key = {
            "main_net": lambda b: (b["main_net_yi"] is not None, b["main_net_yi"] or 0),
            "chg": lambda b: (b["current_pct"] is not None, b["current_pct"] or 0),
            "open_pct": lambda b: (b["open_pct"] is not None, b["open_pct"] or 0),
            "code": lambda b: b[
                "board_code"
            ],  # 字典序（升序，供客户端稳定翻页全量拉取）
            "name": lambda b: b["board_name"],
        }[sort]
        boards.sort(key=sort_key, reverse=sort not in ("code", "name"))

        counts = zt_industry_counts()
        emit = [_emit_row(b, counts) for b in boards[offset : offset + limit]]
        has_more = offset + limit < snap.data["total"]
        return json_ok(
            {
                "scope": scope,
                "sort": sort,
                "total": snap.data["total"],
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
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


def fupan_boards_section() -> dict | None:
    """fupan_v1 boards 段：行业+概念全量行（无 limit 截断），open_pct 为 clist 比值。

    行 = _emit_row 形态 + open/prev_close(常为 None) + open_pct。
    单 scope 失败 → 该 scope 为 {"status":"unavailable","reason"}；双失败 → None。
    """
    snaps = {"industry": fetch_board_list("行业"), "concept": fetch_board_list("概念")}
    if not any(s.ok for s in snaps.values()):
        return None
    counts = zt_industry_counts()
    out = {"as_of": "", "stale": False, "cached": True, "source": _SOURCE}
    for scope, s in snaps.items():
        if not s.ok:
            out[scope] = {
                "status": "unavailable",
                "reason": s.reason or "upstream_error",
            }
            continue
        boards = [dict(b) for b in s.data["boards"]]  # 缓存引用共享，先拷贝
        for b in boards:
            b["open"] = b["_open"]
            b["prev_close"] = b["_prev_close"]
            b["open_pct"] = _open_pct(b["_open"], b["_prev_close"])
        out[scope] = [_emit_row(b, counts) for b in boards]
        out["as_of"] = max(out["as_of"], s.as_of)
        out["stale"] = out["stale"] or s.stale
        out["cached"] = out["cached"] and s.cached
    return out
