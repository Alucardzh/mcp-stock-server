# MCP Akshare Stock Server

基于 FastMCP 的中国股票数据分析 MCP 服务器，提供完整的股票数据获取和技术分析功能。

## 功能特性

- 🔄 **实时股票数据** - 获取 A 股实时价格和交易信息
- 📊 **历史数据** - 查询股票历史价格和技术指标
- 📈 **支撑压力位** - 自动计算支撑位和压力位
- 🏛️ **市场指数** - 获取主要市场指数数据
- 🔍 **股票基本信息** - 查询股票基本信息和行业分类
- 📊 **ETF 单日数据** - `get_etf_daily_tool` 默认查询国家队ETF（汇金重仓8只核心宽基，
  也可指定单只/多只/全市场），返回涨跌幅/成交额/规模/主力资金/份额变化及合并汇总

## 快速开始

### 环境要求

- uv (推荐)

### 安装和运行

```bash
git clone https://github.com/Alucardzh/mcp-stock-server.git
cd mcp-stock-server
uv sync
uv run server.py
```

### Transport 模式

本服务器使用 **HTTP Transport**（Streamable HTTP）作为唯一的传输方式，适合生产环境、Docker 部署和远程访问：

```bash
# 直接运行（默认监听 0.0.0.0:8000）
uv run server.py

# 或通过环境变量指定 host
MCP_HOST=0.0.0.0 uv run server.py
```

**优点**：
- ✅ 稳定可靠，连接自动管理
- ✅ 支持负载均衡和反向代理
- ✅ 适合多客户端并发访问
- ✅ 易于监控和调试

> ℹ️ SSE 与 STDIO 传输方式已弃用，统一使用 HTTP 模式。

### 配置 CherryStudio

在 CherryStudio 中配置 MCP 服务器（使用 HTTP 模式）：

```json
{
  "mcpServers": {
    "mcp-akshare": {
      "isActive": true,
      "name": "mcp-akshare",
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "installSource": "unknown"
    }
  }
}
```

> 将 `url` 替换为实际部署地址，Docker 部署时通常映射到 `http://localhost:18881/mcp`。

### Docker 部署

使用 Docker Compose 部署（HTTP 模式，端口 18881）：

```bash
docker-compose up -d
```

服务将在 `http://localhost:18881` 启动

## 环境变量

项目通过 `.env` 文件（参考 `env.template`）配置运行所需的环境变量。请复制模板并填入真实值：

```bash
cp env.template .env
```

| 变量名 | 必填 | 说明 |
| --- | --- | --- |
| `AKPROXY_TOKEN` | ✅ | akshare-proxy 服务的鉴权 Token，用于通过 `akshare-proxy-patch` 访问行情数据。在 [akshare-proxy](http://101.201.173.125:47001) 获取 |
| `MCP_HOST` | ❌ | HTTP 服务监听地址，默认 `0.0.0.0` |

> ⚠️ `.env` 已在 `.gitignore` 中忽略，请勿提交真实 Token。

## 工具列表

> 下述工具名称均带 `_tool` 后缀（与 `server.py` 中的注册名一致）。

### `get_stock_symbol_by_name_tool`

通过股票名称获取股票代码

- **name**: 股票名称，类似 '中国平安' or '平安'

### `get_stock_history_tool`

获取股票历史数据

- **symbol**: 股票代码 (6 位数字，如 "000001") 或中文名称
- **start_date**: 开始日期 (YYYY-MM-DD，可选)
- **end_date**: 结束日期 (YYYY-MM-DD，可选)
- **period**: 数据周期 ("daily", "weekly", "monthly")
- **adjust**: 价格调整 ("qfq" 前复权, "hfq" 后复权, "none" 不复权)

### `get_stock_realtime_tool`

获取实时股票数据

- **symbol**: 股票代码 (6 位数字) 或中文名称

### `get_stock_basic_tool`

获取股票基本信息

- **symbol**: 股票代码 (6 位数字) 或中文名称

### `calculate_support_resistance_tool`

计算支撑位和压力位

- **symbol**: 股票代码 (6 位数字) 或中文名称
- **n_levels**: 支撑/压力位级别数量 (1-10，默认 5)
- **lookback_period**: 分析周期天数 (30-365，默认 60)

### `get_market_index_tool`

获取市场指数数据

- **index_code**: 指数代码 (默认 "000001" 上证指数)

### `get_ths_hot_list_tool`

获取同花顺热门股票榜单

- **span**: 时间跨度 ("hour" 近 1 小时榜, "day" 今日榜，默认 "hour")
- **limit**: 返回数量 (1-100，默认 100)

### `suggestion_by_my_method`

使用自定义算法分析涨停股池，结合同花顺热度数据（参数为 `StockCalLimit` 对象）

- **limit**: 返回股票数量限制 (1-100，默认 100)
- **span**: 时间跨度 ("hour" 近 1 小时榜, "day" 今日榜)
- **total_market_value**: 流通市值上限 (亿元，默认 200)
- **has_front**: 是否包含前排股 (默认 false)

### `get_akproxy_token_info`

查询 akshare-proxy 服务的积分剩余额度（无需参数）。

### `get_daily_review_tool`

一键复盘：当日 A 股全貌（七模块并行聚合，单模块失败不影响整体）

- **date**: 查询日期 YYYY-MM-DD，默认今天；历史日期时板块资金流、涨跌家数自动降级

返回七段：`indices`（五大指数）、`breadth`（涨跌家数/涨停/跌停/炸板/连板梯队）、
`fund_flow`（大盘+板块主力资金，东财口径）、`national_team`（国家队ETF份额变化与估算净申购）、
`derivatives`（IF/IH/IC/IM 基差含年化、期权 PCR、IF/IO 席位摘要）、`margin`（两融余额，T-1）、
以及 `notes`（降级说明）与 `errors`（模块级错误）。

### `get_market_breadth_tool`

市场涨跌结构（涨跌家数仅当日；涨停/跌停/炸板支持历史日期）

- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_zt_pool_tool`

涨停生态股池明细（金额单位亿元；无数据自动回退最近交易日）

- **pool**: 涨停/炸板/跌停/强势/昨涨停，默认"涨停"
- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_fund_flow_tool`

主力资金（东财口径：超大单+大单净额）

- **scope**: "全部"(大盘+板块) / "大盘" / "板块"，默认"全部"
- **indicator**: "今日" / "5日" / "10日"（仅板块口径），默认"今日"
- **date**: 查询日期 YYYY-MM-DD，默认今天

### `get_margin_tool`

两融余额（交易所 T-1 披露）

- **market**: "沪深"(合计) / "沪" / "深"，默认"沪深"
- **days**: 最近 N 个交易日序列(1-30)，默认 10
- **date**: 截止日期 YYYY-MM-DD，默认今天

### `get_cffex_rank_tool`

中金所前 20 会员成交持仓排名（经纪席位口径，直连中金所官网零积分）

- **var**: IF/IH/IC/IM（股指期货）或 IO/MO/HO（股指期权），默认 IF
- **date**: 查询日期 YYYY-MM-DD，默认今天；非交易日自动回退
- **member**: 会员名子串过滤（如 "中信"），默认不过滤

### `get_index_derivatives_tool`

股指衍生品指标：四大期指基差（含年化）+ 期权 PCR（分交易所）

- **date**: 查询日期 YYYY-MM-DD，默认今天

## 使用示例

```python
# 通过 MCP 客户端调用（HTTP 模式）
result = await client.call_tool("get_stock_realtime_tool", {
    "symbol": "000001"
})
```

## 开发

### 项目结构

```txt
mcp-stock-server/
├── server.py          # 主服务器文件
├── utils/             # 数据获取与分析模块
├── pyproject.toml     # 项目配置
├── env.template       # 环境变量模板
├── docker-compose.yml # Docker Compose 配置
├── Dockerfile         # 容器构建文件
└── README.md          # 项目文档
```

### 依赖

- fastmcp: MCP 服务器框架
- akshare: 股票数据源
- akshare-proxy-patch (>=0.5.0): akshare 数据代理补丁，需配合 `AKPROXY_TOKEN` 使用
- pandas: 数据处理
- numpy: 数值计算

## 许可证

MIT License
