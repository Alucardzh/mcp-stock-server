# MCP Akshare Stock Server

基于 FastMCP 的中国股票数据分析 MCP 服务器，提供完整的股票数据获取和技术分析功能。

## 功能特性

- 🔄 **实时股票数据** - 获取 A 股实时价格和交易信息
- 📊 **历史数据** - 查询股票历史价格和技术指标
- 📈 **支撑压力位** - 自动计算支撑位和压力位
- 🏛️ **市场指数** - 获取主要市场指数数据
- 🔍 **股票基本信息** - 查询股票基本信息和行业分类

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
