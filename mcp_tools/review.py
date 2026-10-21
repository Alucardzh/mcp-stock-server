"""复盘类工具：一键复盘/涨跌结构/涨停生态/指数。"""

from mcp_app import format_symbol, mcp
from utils import (
    get_daily_review,
    get_market_breadth,
    get_market_index,
    get_zt_pool,
)


@mcp.tool()
def get_market_index_tool(index_code: str = "000001") -> str:
    """Get major Chinese market indices data.

    Args:
        index_code: Index code (default: 000001 for Shanghai Composite)
    """
    return get_market_index(format_symbol(index_code))


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
