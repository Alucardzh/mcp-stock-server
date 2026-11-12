"""板块行情类工具：单板块开盘涨跌幅 / 板块清单排行。"""

from mcp_app import mcp
from utils import get_board_quote, get_boards


@mcp.tool()
def get_board_quote_tool(board_code: str = "", board_name: str = "") -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（竞价核心）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文，如 元件 / 游戏Ⅱ；都不给则返回全部板块精简表
    """
    return get_board_quote(board_code, board_name)


@mcp.tool()
def get_boards_tool(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
) -> str:
    """查询板块清单排行：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct
        limit: 返回条数 ≤50，默认 20
        with_open: 附带开盘涨跌幅（默认 true）
    """
    return get_boards(scope, sort, limit, with_open)
