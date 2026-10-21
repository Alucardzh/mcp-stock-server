"""衍生品类工具：中金所席位 / 期指基差与期权PCR / 美债利差。"""

from mcp_app import mcp
from utils import get_cffex_rank, get_fed_watch, get_index_derivatives


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


@mcp.tool()
def get_fed_watch_tool() -> str:
    """美联储预期简版：美债2Y收益率 + 10Y-2Y利差 + 2Y周变动(bp)，不做概率推算"""
    return get_fed_watch()
