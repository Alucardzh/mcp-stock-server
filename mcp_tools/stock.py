"""个股类工具：行情/基础信息/分钟分时/竞价/板块归属/支撑阻力/热度榜。"""

from mcp_app import format_symbol, mcp
from utils import (
    calculate_support_resistance_func,
    get_stock_auction,
    get_stock_basic,
    get_stock_boards,
    get_stock_history,
    get_stock_intraday,
    get_stock_realtime,
    get_stock_symbol_by_name,
    get_ths_hot_list,
)


@mcp.tool
def get_ths_hot_list_tool(span: str = "hour", limit: int = 100) -> str:
    """Get hot list from 同花顺

    Args:
        span (str): default=hour, only two choice[day, hour]: 1.hour means 近1小时榜, 2.day means 今日榜
        limit: a number range from 1 to 100, default=100, control return list len
    """
    return get_ths_hot_list(span, limit)


@mcp.tool()
def get_stock_symbol_by_name_tool(name: str) -> str:
    """Get a Chinese stock symbol by name
    Args:
        name: The name of the stock, stock name like '中国平安' or '平安'
    Returns:
        The stock symbol: 6-digit stock symbol (e.g., '000001'), None if not found
    """
    return get_stock_symbol_by_name(name)


@mcp.tool()
def get_stock_history_tool(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str = "daily",
    adjust: str = "qfq",
) -> str:
    """Get historical stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001') or Chinese stock name
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        period: Data period - daily, weekly, or monthly (default: daily)
        adjust: Price adjustment - qfq, hfq, or none (default: qfq)
    """
    return get_stock_history(
        format_symbol(symbol), start_date, end_date, period, adjust
    )


@mcp.tool()
def get_stock_realtime_tool(symbol: str) -> str:
    """Get real-time stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001') or Chinese stock name
    """
    return get_stock_realtime(format_symbol(symbol))


@mcp.tool()
def get_stock_basic_tool(symbol: str) -> str:
    """Get basic information about a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001') or Chinese stock name
    """
    return get_stock_basic(format_symbol(symbol))


@mcp.tool()
def calculate_support_resistance_tool(
    symbol: str, n_levels: int = 5, lookback_period: int = 60
) -> str:
    """Calculate support and resistance levels for a stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001') or Chinese stock name
        n_levels: Number of support/resistance levels to identify (1-10, default: 5)
        lookback_period: Analysis period in days (30-365, default: 60)
    """
    return calculate_support_resistance_func(
        format_symbol(symbol), n_levels, lookback_period
    )


@mcp.tool()
def get_stock_intraday_tool(
    symbol: str, period: str = "1", mode: str = "summary", date: str = ""
) -> str:
    """查询个股分钟分时（1分钟仅当日、5分钟可回溯历史；默认摘要模式）

    Args:
        symbol: 6位股票代码
        period: "1"/"5" 分钟粒度，默认"1"
        mode: summary=竞价价/开盘30分钟/高低点时间/涨停触碰统计；full=全量bar
        date: YYYY-MM-DD，默认今天
    """
    return get_stock_intraday(symbol, period, mode, date)


@mcp.tool()
def get_stock_auction_tool(symbol: str, date: str = "", mode: str = "summary") -> str:
    """查询个股集合竞价分时（9:15-9:25 虚拟撮合；当日拉取即归档，历史读CSV归档）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD，默认今天
        mode: summary=9:15/9:20/9:25 三段轨迹+撮合价量/高开幅度；detail=逐笔快照
    """
    return get_stock_auction(symbol, date, mode)


@mcp.tool()
def get_stock_boards_tool(symbol: str, with_raw: bool = False) -> str:
    """查询个股完整行业名与概念板块列表（修复涨停池行业4字截断的完整口径）

    Args:
        symbol: 6位股票代码
        with_raw: 是否附带原始全部所属板块（含地域/属性板块）
    """
    return get_stock_boards(symbol, with_raw)
