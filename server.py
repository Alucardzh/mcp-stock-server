#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP.

This module provides MCP-compliant server for Chinese stock data analysis
using Akshare API and FastMCP framework following PEP 723 standards.
"""

import logging
import os

from fastmcp import FastMCP

# Import our analysis modules
from utils import (
    StockCal,
    calculate_support_resistance_func,
    get_market_index,
    get_stock_basic,
    get_stock_history,
    get_stock_realtime,
    get_stock_symbol_by_name,
    get_ths_hot_list,
)
from utils.schema import StockCalLimit

# Configure logging
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Create FastMCP server instance
mcp = FastMCP("MCP Akshare Stock Server")


def format_symbol(symbol: str) -> str:
    """格式化symbol"""
    return symbol.replace("'", "").replace('"', "")


@mcp.tool
def suggestion_by_my_method(data: StockCalLimit) -> str:
    """使用自定义算法分析涨停股池，结合同花顺热度数据

    该工具会：
    1. 获取同花顺热门股票榜单
    2. 筛选符合条件的涨停股票（连板数≥2，涨停统计≥0.666）
    3. 分析股票的技术形态和历史高点
    4. 返回综合分析结果

    Args:
        data (StockCalLimit): 分析参数配置
            - limit: 返回股票数量限制 (1-100, 默认100)
            - span: 时间跨度 ("hour": 近1小时榜, "day": 今日榜, 默认"hour")
            - total_market_value: 流通市值上限 (亿元, 默认200)
            - has_front: 是否包含前排股 (True/False, 默认False)

    Returns:
        str: JSON格式的分析结果，包含消息和股票数据
    """
    get_data = StockCal(data=data)
    return get_data.get_daily_code_data()


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
def get_market_index_tool(index_code: str = "000001") -> str:
    """Get major Chinese market indices data.

    Args:
        index_code: Index code (default: 000001 for Shanghai Composite)
    """
    return get_market_index(format_symbol(index_code))


@mcp.prompt()
def stock_analysis() -> str:
    """Professional stock analysis prompt for Chinese A-share market."""
    return """
    你是一个专业的股票分析师，专门分析中国A股市场。

    请根据用户提供的股票代码，进行全面的股票分析，包括：

    1. 基本信息（公司名称、所属行业、主营业务等）
    2. 价格走势分析（历史表现、技术指标）
    3. 支撑位和压力位分析
    4. 投资建议和风险提示

    请使用严谨的分析方法，提供客观、专业的分析结果。

    可用的工具包括：
    - get_stock_basic_tool: 获取股票基本信息
    - get_stock_history_tool: 获取历史价格数据
    - calculate_support_resistance_tool: 计算支撑位和压力位
    - get_stock_realtime_tool: 获取实时价格数据
    - suggestion_by_my_method: 使用自定义算法分析涨停股池
    """


@mcp.prompt()
def market_overview() -> str:
    """Market analysis prompt for Chinese A-share market."""
    return """
    你是一个专业的市场分析师，专门分析中国A股市场整体情况。

    请根据市场指数数据，提供：

    1. 市场整体表现分析
    2. 主要指数对比（上证指数、深证成指、创业板指等）
    3. 热点板块分析
    4. 市场趋势和投资建议

    请使用数据驱动的分析方法，提供客观的市场分析。
    """


@mcp.prompt()
def limit_stock_analysis() -> str:
    """涨停股池分析提示"""
    return """
    你是一个专业的短线交易分析师，专门分析A股涨停股池。

    请根据以下参数分析涨停股票：
    - limit: 返回股票数量限制 (1-100)
    - span: 时间跨度 (hour: 近1小时榜, day: 今日榜)
    - total_market_value: 流通市值上限 (亿元)
    - has_front: 是否包含前排股 (True/False)

    分析维度包括：
    1. 涨停统计和连板数分析
    2. 热度和成交额排名
    3. 所属行业分布
    4. 技术形态分析（当前价格与历史高点的比较）
    5. 短线交易机会和风险提示

    使用suggestion_by_my_method工具获取数据，并提供专业的分析建议。
    """


def main():
    """Main entry point for the FastMCP server."""
    # FastMCP handles stdout/stderr correctly for MCP protocol
    # JSON-RPC messages go to stdout, logs go to stderr
    os.environ["PYTHONUNBUFFERED"] = "1"
    logger.info("Starting MCP Akshare Server with FastMCP")

    # 支持通过环境变量切换传输模式
    # 默认: http (Streamable HTTP) - 稳定可靠，适合生产环境
    transport = os.getenv("MCP_TRANSPORT", "http")
    port = int(os.getenv("MCP_PORT", "8000"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    if transport == "http":
        # 推荐: Streamable HTTP transport
        logger.info("Running in HTTP mode on %s:%s", host, port)
        mcp.run(transport="http", host=host, port=port)
    elif transport == "sse":
        # SSE transport (legacy, 仅用于兼容旧版本)
        logger.warning("SSE transport is legacy, consider using HTTP transport instead")
        logger.info("Running in SSE mode on %s:%s", host, port)
        mcp.run(transport="sse", host=host, port=port)
    else:
        # stdio 模式 (用于本地进程通信)
        logger.info("Running in stdio mode")
        mcp.run()


if __name__ == "__main__":
    main()
