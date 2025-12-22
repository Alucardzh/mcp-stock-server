#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP.

This module provides MCP-compliant server for Chinese stock data analysis
using Akshare API and FastMCP framework following PEP 723 standards.
"""

import os
import logging
from fastmcp import FastMCP
# Import our analysis modules
from utils import (
    get_stock_history,
    get_stock_realtime,
    get_stock_basic,
    calculate_support_resistance_func,
    get_market_index,
    get_stock_symbol_by_name,
    get_ths_hot_list
)


# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Create FastMCP server instance
mcp = FastMCP("MCP Akshare Stock Server")


@mcp.tool
def get_ths_hot_list_tool(span: str, limit: int = 100) -> str:
    """Get hot list from 同花顺

    Args:
        span (str): only two choice[day, hour]: 1.hour means 近1小时榜, 2.day means 今日榜
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
    adjust: str = "qfq"
) -> str:
    """Get historical stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
        start_date: Start date in YYYY-MM-DD format (optional)
        end_date: End date in YYYY-MM-DD format (optional)
        period: Data period - daily, weekly, or monthly (default: daily)
        adjust: Price adjustment - qfq, hfq, or none (default: qfq)
    """
    return get_stock_history(symbol, start_date, end_date, period, adjust)



@mcp.tool()
def get_stock_realtime_tool(symbol: str) -> str:
    """Get real-time stock data for a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    return get_stock_realtime(symbol)




@mcp.tool()
def get_stock_basic_tool(symbol: str) -> str:
    """Get basic information about a Chinese stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
    """
    return get_stock_basic(symbol)



@mcp.tool()
def calculate_support_resistance_tool(
    symbol: str,
    n_levels: int = 5,
    lookback_period: int = 60
) -> str:
    """Calculate support and resistance levels for a stock.

    Args:
        symbol: 6-digit stock symbol (e.g., '000001')
        n_levels: Number of support/resistance levels to identify (1-10, default: 5)
        lookback_period: Analysis period in days (30-365, default: 60)
    """
    return calculate_support_resistance_func(symbol, n_levels, lookback_period)



@mcp.tool()
def get_market_index_tool(index_code: str = "000001") -> str:
    """Get major Chinese market indices data.

    Args:
        index_code: Index code (default: 000001 for Shanghai Composite)
    """
    return get_market_index(index_code)



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


def main():
    """Main entry point for the FastMCP server."""
    # FastMCP handles stdout/stderr correctly for MCP protocol
    # JSON-RPC messages go to stdout, logs go to stderr
    os.environ["PYTHONUNBUFFERED"] = "1"
    logger.info("Starting MCP Akshare Server with FastMCP")
    mcp.run()


if __name__ == "__main__":
    main()
