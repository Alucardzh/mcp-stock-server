"""复盘一站式工具：fupan_v1 及其零件（klines 批量等）。"""

from datetime import datetime

from mcp_app import mcp
from utils.kline_metrics import batch_klines
from utils.lhb_aggregate import lhb_aggregate
from utils.review_common import json_err_code, json_ok, parse_day


@mcp.tool()
def get_klines_batch_tool(symbols: list[str], bars: int = 130, date: str = "") -> str:
    """个股日K指标批量（不复权；MA5/10/20、pct60、chg5/10、涨跌停价）

    Args:
        symbols: 6位代码列表（查不到的票该项为 null 并列入 warnings）
        bars: 计算窗口，默认 130
        date: 截至交易日 YYYY-MM-DD，默认今天
    """
    bad = [s for s in symbols if not (s.isdigit() and len(s) == 6)]
    if bad:
        return json_err_code("INVALID_ARG", f"代码格式非法: {bad}")
    day = parse_day(date)
    if day is None:
        return json_err_code("INVALID_ARG", f"日期格式错误: {date}")
    if day > datetime.now().date():
        return json_err_code("INVALID_ARG", f"查询日期 {day} 晚于今天")
    out = batch_klines(symbols, str(day), bars)
    return json_ok(
        {
            "date": str(day),
            "bars": bars,
            "source": "tencent_ifzq",
            **out,
        }
    )


@mcp.tool()
def get_lhb_batch_tool(date: str, symbols: list[str] = []) -> str:
    """龙虎榜批量聚合（万元）：榜净并集口径 + 席位指纹去重三分类 + diff 参考值

    Args:
        date: YYYY-MM-DD（当日席位约18:00后定稿）
        symbols: 6位代码列表；为空则返回全部上榜票
    """
    if parse_day(date) is None:
        return json_err_code("INVALID_ARG", f"日期格式错误: {date}")
    return json_ok(lhb_aggregate(date, symbols))


@mcp.tool()
def fupan_v1_tool(date: str, symbols: list[str] = [], options: dict = {}) -> str:
    """fupan_v1：晚班复盘一站式（v1 冻结；指数/宽度/资金流/三池全量/行业桶/链强度/板块/个股指标/龙虎榜）

    Args:
        date: 复盘日 YYYY-MM-DD（收盘口径；非交易日 NOT_TRADE_DAY；当日<15:02 NOT_READY）
        symbols: 需要个股指标(klines)的代码；缺省=当日涨停池全量∪传入
        options: with_pools/boards/klines/lhb/breadth/fund_flow/derivatives(默认关)，
                 chain_buckets={"电子链":["半导体","元件",...]}，kline_bars=130
    """
    from utils.fupan import fupan_v1

    return fupan_v1(date, symbols, options)
