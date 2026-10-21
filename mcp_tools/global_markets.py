"""全球市场工具：六组快照 / 隔夜共振 / 海外个股链。"""

from mcp_app import mcp
from utils import (
    get_global_linkage_review,
    get_global_markets,
    get_stock_global_snapshot,
)


@mcp.tool()
def get_global_markets_tool(groups: str = "全部") -> str:
    """全球市场快照：美股(含费半)/美债(含日债10Y-30Y)/汇率(DXY+离岸人民币)/亚太(日经KOSPI恒科)/商品(布伦特WTI黄金)/恐慌(VIX) 六组并行聚合

    盘前场景一键拉取，判断当日A股输入性风险。单组失败不影响其他组(见errors/notes)。
    国内直连为主源，Yahoo(经代理)为备源。

    Args:
        groups: 逗号分隔组名(美股/美债/汇率/亚太/商品/恐慌)，默认"全部"
    """
    return get_global_markets(groups)


@mcp.tool()
def get_stock_global_snapshot_tool(symbols: str = "", preset: str = "ai_chain") -> str:
    """全球个股快照：光模块/存储链海外对标组（腾讯主源，Yahoo兜底走akproxy或YAHOO_PROXY）

    Args:
        symbols: 显式Yahoo代码(逗号分隔，如 "NVDA,000660.KS")，优先于preset
        preset: ai_chain(NVDA/AVGO/MRVL/COHR/LITE/APH/ANET/台积电) /
                storage(SK海力士/三星/铠侠/美光/闪迪) / etf(SMH/AIQ/BOTZ) / all
    """
    return get_stock_global_snapshot(symbols, preset)


@mcp.tool()
def get_global_linkage_review_tool(date: str = "") -> str:
    """共振复盘：隔夜全球(美股/美债/日债) vs 当日A股对照表（判读留给AI）

    Args:
        date: A股交易日 YYYY-MM-DD，默认今天
    """
    return get_global_linkage_review(date)
