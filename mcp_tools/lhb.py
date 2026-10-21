"""龙虎榜工具：榜单聚合 / 个股席位明细。"""

from mcp_app import mcp
from utils import get_lhb, get_lhb_seats


@mcp.tool()
def get_lhb_tool(date: str = "") -> str:
    """查询龙虎榜榜单（按股聚合净买卖亿元、上榜原因、东财解读；净买TOP10/净卖TOP5）

    Args:
        date: YYYY-MM-DD 默认今天；当日榜单约17:00后陆续齐全
    """
    return get_lhb(date)


@mcp.tool()
def get_lhb_seats_tool(symbol: str, date: str = "") -> str:
    """查询个股龙虎榜席位明细（买/卖前5，万元，机构/外资/游资分类）

    Args:
        symbol: 6位股票代码
        date: YYYY-MM-DD 默认今天；当日席位>=18:00才齐全
    """
    return get_lhb_seats(symbol, date)
