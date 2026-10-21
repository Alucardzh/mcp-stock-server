#!/usr/bin/env python3
"""
个股板块归属：完整行业名 + 概念标签。

数据源：efinance get_belong_board（push2 slist，混合板块）+
akshare 行业/概念板块名单（BK 代码，用于过滤混合结果，当日缓存）。
涨停池"所属行业"4字截断为上游固有，本工具提供完整口径。
"""

from datetime import datetime
import logging

from akshare import stock_board_concept_name_em, stock_board_industry_name_em

from .ef import ef_belong_board
from .review_common import json_err, json_ok
from .validate import validate_stock_symbol

logger = logging.getLogger(__name__)

_board_cache: dict = {"date": "", "concept": set(), "industry": {}}


def _refresh_board_lists():
    """行业/概念板块名单（概念=代码集合，行业=代码→名称映射），当日缓存。"""
    today = str(datetime.now().date())
    if _board_cache["date"] == today and (_board_cache["concept"] or _board_cache["industry"]):
        return
    try:
        dfc = stock_board_concept_name_em()
        _board_cache["concept"] = set(dfc["板块代码"].astype(str))
    except Exception as e:  # noqa: BLE001
        logger.warning("concept board list failed: %s", e)
    try:
        dfi = stock_board_industry_name_em()
        _board_cache["industry"] = dict(
            zip(dfi["板块代码"].astype(str), dfi["板块名称"].astype(str))
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("industry board list failed: %s", e)
    _board_cache["date"] = today


def get_stock_boards(symbol: str, with_raw: bool = False) -> str:
    """查询个股完整行业名与概念板块列表

    Args:
        symbol: 6位股票代码
        with_raw: 是否附带原始全部所属板块（含地域/属性板块）
    """
    try:
        symbol = validate_stock_symbol(symbol)
        df = ef_belong_board(symbol)
        if df is None or df.empty:
            return json_err(f"{symbol} 无所属板块数据")
        _refresh_board_lists()
        industry, concepts, raw = None, [], []
        for _, r in df.iterrows():
            code = str(r.get("板块代码") or "")
            name = str(r.get("板块名称") or "")
            if code in _board_cache["industry"]:
                industry = name
            elif code in _board_cache["concept"]:
                concepts.append(name)
            if with_raw:
                raw.append({"code": code, "name": name})
        notes = []
        if industry is None and not _board_cache["industry"]:
            notes.append("行业名单拉取失败，industry 缺失")
        if not concepts and not _board_cache["concept"]:
            notes.append("概念名单拉取失败，concepts 可能为空")
        out = {"symbol": symbol, "industry": industry, "concepts": concepts, "notes": notes}
        if with_raw:
            out["raw"] = raw
        return json_ok(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Error in get_stock_boards: %s", e)
        return json_err(f"查询板块归属失败: {e}")
