#!/usr/bin/env python3
"""
MCP Akshare Stock Data Server using FastMCP — 入口。

实例与 instructions 在 mcp_app.py；工具按分类注册在 mcp_tools/（导入即注册）：
- mcp_tools/meta.py           服务器元工具（list_tools / akproxy额度）
- mcp_tools/stock.py          个股（行情/基础/分钟分时/竞价/板块/支撑阻力/热度榜）
- mcp_tools/review.py         复盘（一键复盘/涨跌结构/涨停生态/指数）
- mcp_tools/lhb.py            龙虎榜（榜单/席位）
- mcp_tools/funds.py          资金面（主力资金/两融/ETF）
- mcp_tools/derivatives.py    衍生品（中金所席位/基差PCR/美债利差）
- mcp_tools/global_markets.py 全球（六组快照/隔夜共振/海外个股链）
- mcp_tools/prompts.py        分析师 prompts

新增工具：在对应分类模块加 @mcp.tool()（函数透传 utils 层实现）；
新增分类：建文件并在 mcp_tools/__init__.py 加一行导入。
"""

import logging
import os

# Configure logging
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
HOST = os.getenv("MCP_HOST", "0.0.0.0")

import mcp_tools  # noqa: E402  # 导入即注册全部工具与 prompts（须在 logging 配置后）
from mcp_app import mcp  # noqa: E402


def main():
    """Main entry point for the FastMCP server."""
    os.environ["PYTHONUNBUFFERED"] = "1"
    logger.info("Starting MCP Akshare Server in HTTP mode on %s:8000", HOST)
    mcp.run(transport="http", host=HOST, port=8000)


if __name__ == "__main__":
    main()
