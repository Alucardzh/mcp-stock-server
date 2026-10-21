"""MCP 工具分类注册：导入即注册到 mcp_app.mcp。

新增分类模块后在此加一行导入即可；往现有分类加工具只需在对应模块加 @mcp.tool()。
"""

from . import (  # noqa: F401
    derivatives,
    funds,
    global_markets,
    lhb,
    meta,
    prompts,
    review,
    stock,
)
