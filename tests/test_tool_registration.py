"""工具注册冒烟：新板块工具已挂到 FastMCP 实例，且 utils 导出可用。"""

import asyncio


def test_new_board_tools_registered():
    from mcp_app import mcp
    import mcp_tools  # noqa: F401  导入即注册

    tools = asyncio.run(mcp.get_tools())
    names = set(tools)
    assert "get_board_quote_tool" in names
    assert "get_boards_tool" in names
    assert "get_zt_pool_tool" in names


def test_utils_exports():
    from utils import get_board_quote, get_boards, get_zt_pool  # noqa: F401


def test_klines_tool_registered():
    from mcp_app import mcp
    import mcp_tools  # noqa: F401

    tools = asyncio.run(mcp.get_tools())
    assert "get_klines_batch_tool" in set(tools)


def test_fupan_tool_registered():
    from mcp_app import mcp
    import mcp_tools  # noqa: F401

    tools = asyncio.run(mcp.get_tools())
    assert "fupan_v1_tool" in set(tools)


def test_date_param_descriptions_carry_format():
    """需求v3 N1：date 参数 schema description 写明 YYYY-MM-DD（不试错可知）。"""
    from mcp_app import mcp
    import mcp_tools  # noqa: F401

    tools = asyncio.run(mcp.get_tools())
    for name in ("fupan_v1_tool", "get_daily_review_tool"):
        desc = tools[name].parameters["properties"]["date"].get("description", "")
        assert "YYYY-MM-DD" in desc, f"{name}: {desc!r}"
