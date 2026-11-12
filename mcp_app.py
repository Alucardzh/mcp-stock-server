#!/usr/bin/env python3
"""FastMCP 实例与应用级说明（instructions）。

工具按分类注册在 mcp_tools/ 各模块（导入即注册），server.py 仅作入口。
新增工具：在 mcp_tools/ 对应分类模块加 @mcp.tool() 即可；
新增分类：建文件并在 mcp_tools/__init__.py 加一行导入。
"""

from fastmcp import FastMCP

SERVER_INSTRUCTIONS = """A股行情与复盘数据服务器（东财数据源，经 akshare/efinance 代理通道）。

工具发现：不确定用哪个工具时，先调 list_tools_tool 枚举全部工具（名称/用途/参数）。
代码与名称互查：get_stock_symbol_by_name_tool。

【复盘】
- get_daily_review_tool(date)：一键复盘，七模块并行聚合（指数/涨跌结构/资金/国家队ETF/期指基差+席位/期权PCR/两融）；已废弃(deprecated)，晚班复盘请用 fupan_v1_tool
- fupan_v1_tool(date, symbols, options)：晚班复盘一站式（v1 冻结）：指数/宽度/资金流/三池全量/行业桶/链强度(chain_buckets)/板块/个股日K指标(不复权)/龙虎榜；段级 unavailable 降级，缺数据≠零值
- get_klines_batch_tool(symbols, bars, date)：日K指标批量；get_lhb_batch_tool(date, symbols)：龙虎榜聚合（万元，指纹去重三分类）
- get_zt_pool_tool(pool, date, full)：涨停生态五池（涨停/炸板/跌停/强势/昨涨停），支持历史日期；涨停/炸板池含首次封板时间/最后封板时间（HHMMSS），"最早上板"按首次封板时间排序；full=true 返回全量池（链强度/炸板率/晋级率须全量，默认截断60条）
- get_market_breadth_tool(date)：涨跌家数（仅当日）+ 涨跌停/炸板家数 + 连板梯队

【板块】
- get_board_quote_tool(board_code|board_name)：单板块开盘涨跌幅/主力净流入/涨跌家数/领涨股/涨停家数；board_name 必须东财原文（如 游戏Ⅱ）；查不到=BOARD_NOT_FOUND（配置错），限流=status:unavailable（缺数据≠条件不达标，标待人工判），未开盘=no_open
- get_boards_tool(scope, sort, limit≤200, offset, with_open)：行业/概念板块排行（main_net/chg/open_pct 排序；offset 按排序跳过前 N 条分页）

【个股】
- get_stock_realtime_tool / get_stock_history_tool / get_stock_basic_tool：实时快照 / 历史K线 / 基础信息
- get_stock_intraday_tool(symbol, period, mode, date)：分钟分时。1分钟仅当日、5分钟可回溯历史；summary 给竞价价/开盘尾盘30分钟/全天高低点时间/涨停触碰时间线，full 给全量bar
- get_stock_auction_tool(symbol, date, mode)：集合竞价 9:15-9:25 虚拟撮合过程（9:20后为真实意愿段）。当日拉取即落CSV归档，历史日期读归档（无归档则无法回补）
- get_stock_boards_tool(symbol)：完整行业名+概念板块列表（涨停池"所属行业"为4字截断，题材聚类用本工具）
- calculate_support_resistance_tool：支撑/阻力位；get_ths_hot_list_tool：同花顺热度榜

【龙虎榜】
- get_lhb_tool(date)：榜单按股聚合净买/卖（亿元）、上榜原因、东财解读、净买TOP10/净卖TOP5
- get_lhb_seats_tool(symbol, date)：买/卖前5席位（万元，机构/外资/游资分类）；当日数据建议>=18:00查询

【资金与衍生品】
- get_fund_flow_tool：大盘+板块主力资金；get_etf_daily_tool：国家队ETF资金与份额；get_margin_tool：两融（T-1）
- get_index_derivatives_tool：期指基差+期权PCR；get_cffex_rank_tool：中金所前20席位；get_fed_watch_tool：美债利差

【全球】
- get_global_markets_tool：美股/美债/汇率/亚太/商品/VIX 六组快照；get_global_linkage_review_tool：隔夜与当日A股共振对照；get_stock_global_snapshot_tool：光模块/存储链美股快照

【通用约定】date 参数一律 YYYY-MM-DD、默认今天；金额单位亿元（席位为万元）；全部返回 JSON 信封 {success, data|error}。"""

mcp = FastMCP("MCP Akshare Stock Server", instructions=SERVER_INSTRUCTIONS)


def format_symbol(symbol: str) -> str:
    """格式化symbol"""
    return symbol.replace("'", "").replace('"', "")
