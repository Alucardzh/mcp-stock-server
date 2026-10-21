"""资金面工具：主力资金 / 两融 / ETF。"""

from mcp_app import mcp
from utils import get_etf_daily, get_fund_flow, get_margin


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
