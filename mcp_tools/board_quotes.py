"""板块行情类工具：单板块开盘涨跌幅 / 板块清单排行。"""

from mcp_app import mcp
from utils import get_board_quote, get_boards


@mcp.tool()
def get_board_quote_tool(board_code: str = "", board_name: str = "") -> str:
    """查询单个板块行情：开盘涨跌幅/主力净流入/涨跌家数/领涨股（竞价核心）

    Args:
        board_code: 东财板块代码，如 BK0459（与 board_name 二选一）
        board_name: 东财板块名原文，如 元件 / 游戏Ⅱ；都不给则返回全部板块精简表
            （支持 4 字截断名前缀匹配：唯一命中返回并标 matched_by:"prefix"，
            多命中返回 status:"ambiguous"+candidates）
    """
    return get_board_quote(board_code, board_name)


@mcp.tool()
def get_boards_tool(
    scope: str = "行业",
    sort: str = "main_net",
    limit: int = 20,
    with_open: bool = True,
    offset: int = 0,
) -> str:
    """查询板块清单排行：主力净流入/涨跌幅/开盘涨跌幅排序

    Args:
        scope: 行业（默认）/ 概念
        sort: main_net（默认）/ chg / open_pct / code / name（code/name 为字典序升序，便于全量翻页）
        limit: 返回条数 ≤600，默认 20
        with_open: 附带开盘涨跌幅（默认 true）
        offset: 按 sort 排序后跳过前 N 条（分页取排行尾部；越界返回空列表）
    """
    return get_boards(scope, sort, limit, with_open, offset)
