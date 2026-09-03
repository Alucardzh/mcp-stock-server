#!/usr/bin/env python3
"""
一键复盘聚合器：并行拉取七大模块并合并为一份 JSON。

模块：indices(指数) / breadth(涨跌结构) / fund_flow(主力资金) /
national_team(国家队ETF, 复用 etf.get_etf_daily) / derivatives(基差+PCR+席位) /
margin(两融)。

- ThreadPoolExecutor 并行，总耗时 ≈ 最慢单模块；
- 模块级降级：单模块失败进 errors，notes 弹出加 [模块名] 前缀；
- 聚合结果按日期缓存 600 秒（收盘后数据不变，防重复消耗东财积分）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import logging

from .etf import get_etf_daily
from .review_common import json_err, json_ok, parse_day
from .review_derivatives import derivatives_section
from .review_funds import fund_flow_section, margin_section
from .review_market import breadth_section, indices_section
from .tools import CachedData, RateLimiter

logger = logging.getLogger(__name__)

_review_cache: dict[str, CachedData] = {}


def _national_team_section(day) -> dict:
    """④ 国家队ETF：复用 etf.get_etf_daily（JSON 解析，不改动该模块）"""
    raw = json.loads(get_etf_daily("", str(day)))
    if not raw.get("success"):
        raise ValueError(str(raw.get("error", "ETF数据获取失败"))[:120])
    data = raw["data"]
    merged = data.get("merged", {})
    return {
        "date": data.get("date"),
        "total_est_net_subscription_yi": merged.get("est_net_subscription_yi"),
        "total_share_change_yi": merged.get("total_share_change_yi"),
        "total_main_inflow_yi": merged.get("total_main_inflow_yi"),
        "avg_change_pct": merged.get("avg_change_pct"),
        "top_net_subscription": merged.get("top_net_subscription"),
        "top_net_redemption": merged.get("top_net_redemption"),
    }


@RateLimiter(max_calls=6, time_window=60)
def get_daily_review(date: str = "") -> str:
    """一键复盘：当日 A 股全貌（七模块并行聚合）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时部分模块自动降级
              （板块资金流、涨跌家数仅当日），详见返回中的 notes。
    """
    try:
        day = parse_day(date)
        if day is None:
            return json_err(f"日期格式错误: {date}，请使用 YYYY-MM-DD")
        if day > datetime.now().date():
            return json_err(f"查询日期 {day} 晚于今天，无法查询未来数据")
        key = str(day)
        cached = _review_cache.get(key)
        if cached is not None and not cached.is_expired():
            return cached.data

        def run(name, fn):
            try:
                data = fn(day)
                mod_notes = data.pop("notes", [])
                return name, data, [f"[{name}] {n}" for n in mod_notes], None
            except Exception as e:  # noqa: BLE001
                logger.warning("section %s failed: %s", name, e)
                return name, None, [], str(e)

        # 注意：jobs 必须在函数体内引用模块全局名（indices_section 等），
        # 不能用模块级常量持有函数引用，否则测试 monkeypatch 不生效。
        jobs = [
            ("indices", indices_section),
            ("breadth", breadth_section),
            ("fund_flow", fund_flow_section),
            ("derivatives", derivatives_section),
            ("margin", margin_section),
            ("national_team", _national_team_section),
        ]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda nf: run(nf[0], nf[1]), jobs))
        data = {
            "date": key,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        notes, errors = [], {}
        for name, section, mod_notes, err in results:
            data[name] = section
            notes.extend(mod_notes)
            if err:
                errors[name] = err
        if all(data.get(name) is None for name, _ in jobs):
            return json_err(f"{day} 全部模块均无数据(可能非交易日)")
        if notes:
            data["notes"] = notes
        if errors:
            data["errors"] = errors
        out = json_ok(data)
        _review_cache[key] = CachedData(out, ttl=600)
        return out
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_daily_review: %s", e)
        return json_err(f"一键复盘失败: {e}")
