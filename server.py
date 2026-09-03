#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP.

This module provides MCP-compliant server for Chinese stock data analysis
using Akshare API and FastMCP framework following PEP 723 standards.
"""

import logging
import os
import urllib.request

from fastmcp import FastMCP

# Import our analysis modules
from utils import (
    StockCal,
    calculate_support_resistance_func,
    get_cffex_rank,
    get_daily_review,
    get_etf_daily,
    get_fund_flow,
    get_index_derivatives,
    get_margin,
    get_market_breadth,
    get_market_index,
    get_stock_basic,
    get_stock_history,
    get_stock_realtime,
    get_stock_symbol_by_name,
    get_ths_hot_list,
    get_zt_pool,
)
from utils.schema import StockCalLimit

# Configure logging
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
HOST = os.getenv("MCP_HOST", "0.0.0.0")

# Create FastMCP server instance
mcp = FastMCP("MCP Akshare Stock Server")


def format_symbol(symbol: str) -> str:
    """格式化symbol"""
    return symbol.replace("'", "").replace('"', "")


@mcp.tool()
def get_akproxy_token_info() -> str:
    """查询 akshare-proxy 服务的 积分剩余额度。"""
    token = os.getenv("AKPROXY_TOKEN", "")
    if not token:
        return "AKPROXY_TOKEN 未配置"
    url = f"http://101.201.173.125:47001/api/token/{token}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        return f"查询失败: {e}"


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


@mcp.tool()
def get_etf_daily_tool(symbols: str = "", date: str = "") -> str:
    """查询 ETF 单日数据（涨跌幅、成交额、规模、主力资金、份额变化）并返回合并汇总

    典型用途：跟踪"国家队"ETF（汇金重仓宽基ETF）的资金与涨跌情况。

    查询范围（symbols 参数）：
    - 默认: symbols="" / "all" / "全部" / "国家队" = 全部国家队ETF（8只核心宽基）
    - 单只: symbols="510300"
    - 多只: symbols="510300,159915"（逗号分隔）
    - 全市场: symbols="全市场" 或 "market"（约1600只，仅支持当日查询）

    Args:
        symbols: 默认(空)/"all"/"全部"/"国家队"=国家队全部ETF；"全市场"/"market"=全市场ETF；
                 或6位ETF代码（逗号分隔）
        date: 查询日期 YYYY-MM-DD，默认今天（盘中为实时快照）。
              历史日期需指定代码（最多20只，国家队默认8只可直接查）；全市场仅支持当日。

    Returns:
        JSON字符串：
        - items: 每只ETF明细（价格/涨跌幅/成交额/规模/主力净流入/份额/份额变化/估算净申购额），
                 超过30只时省略
        - merged: 合并数据（总规模/总成交额/平均与规模加权涨跌幅/涨跌家数/主力净流入合计/
                  份额净变化/估算净申购额/涨幅榜/跌幅榜/净申购榜/净赎回榜）
        - 单只查询时附带全市场规模与成交额排名（single_rank）
    """
    return get_etf_daily(symbols, date)


@mcp.tool()
def get_daily_review_tool(date: str = "") -> str:
    """一键复盘：当日 A 股全貌（七模块并行聚合）

    返回 indices(五大指数) / breadth(涨跌家数+涨停跌停炸板+连板梯队) /
    fund_flow(大盘+板块主力资金) / national_team(国家队ETF份额与净申购) /
    derivatives(期指基差+期权PCR+IF/IO席位摘要) / margin(两融余额T-1)。
    单模块失败不影响整体（见 errors/notes）。

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时板块资金流、涨跌家数自动降级。
    """
    return get_daily_review(date)


@mcp.tool()
def get_market_breadth_tool(date: str = "") -> str:
    """查询市场涨跌结构：涨跌家数(仅当日)、涨停/跌停/炸板家数、连板梯队

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天。历史日期时涨跌家数不可用(降级说明)。
    """
    return get_market_breadth(date)


@mcp.tool()
def get_zt_pool_tool(pool: str = "涨停", date: str = "") -> str:
    """查询涨停生态股池明细（金额单位: 亿元）

    Args:
        pool: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
        date: 查询日期 YYYY-MM-DD，默认今天；无数据时自动回退最近交易日
    """
    return get_zt_pool(pool, date)


@mcp.tool()
def get_fund_flow_tool(
    scope: str = "全部", indicator: str = "今日", date: str = ""
) -> str:
    """查询主力资金：大盘（沪深两市净流入）与/或行业板块排名

    Args:
        scope: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
        indicator: "今日" / "5日" / "10日"（仅板块口径），默认"今日"
        date: 查询日期 YYYY-MM-DD，默认今天
    """
    return get_fund_flow(scope, indicator, date)


@mcp.tool()
def get_margin_tool(market: str = "沪深", days: int = 10, date: str = "") -> str:
    """查询两融（融资余额，T-1 披露）

    Args:
        market: "沪深"(合计) / "沪" / "深"，默认"沪深"
        days: 返回最近 N 个交易日余额序列(1-30)，默认10
        date: 截止日期 YYYY-MM-DD，默认今天
    """
    return get_margin(market, days, date)


@mcp.tool()
def get_cffex_rank_tool(var: str = "IF", date: str = "", member: str = "") -> str:
    """查询中金所前20会员成交持仓排名（经纪席位口径）

    Args:
        var: IF/IH/IC/IM(股指期货) 或 IO/MO/HO(股指期权)，默认 IF
        date: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退
        member: 会员名子串过滤（如 "中信"），默认不过滤
    """
    return get_cffex_rank(var, date, member)


@mcp.tool()
def get_index_derivatives_tool(date: str = "") -> str:
    """查询股指衍生品指标：四大期指基差（含年化）+ 期权PCR（分交易所）

    Args:
        date: 查询日期 YYYY-MM-DD，默认今天
    """
    return get_index_derivatives(date)


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
    os.environ["PYTHONUNBUFFERED"] = "1"
    logger.info("Starting MCP Akshare Server in HTTP mode on %s:8000", HOST)
    mcp.run(transport="http", host=HOST, port=8000)


if __name__ == "__main__":
    main()
