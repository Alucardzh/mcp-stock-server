"""复盘一站式工具：fupan_v1 及其零件（klines 批量等）。"""

from datetime import datetime

from mcp_app import mcp
from utils.kline_metrics import batch_klines
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
