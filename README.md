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

本服务器支持三种 transport 模式：

#### 1. **HTTP Transport** (默认，推荐)
适合生产环境、Docker 部署和远程访问：

```bash
# 直接运行（默认 HTTP 模式，端口 8000）
uv run server.py

# 或通过环境变量指定
MCP_TRANSPORT=http MCP_PORT=8000 uv run server.py
```

**优点**：
- ✅ 稳定可靠，连接自动管理
- ✅ 支持负载均衡和反向代理
- ✅ 适合多客户端并发访问
- ✅ 易于监控和调试

#### 2. **SSE Transport** (遗留模式)
仅用于向后兼容：

```bash
MCP_TRANSPORT=sse MCP_PORT=8000 uv run server.py
```

⚠️ **注意**：SSE 是遗留模式，建议迁移到 HTTP transport

#### 3. **STDIO Transport** (本地进程)
适合本地开发和调试：

```bash
MCP_TRANSPORT=stdio uv run server.py
```

### 配置 CherryStudio

在 CherryStudio 中配置 MCP 服务器（使用 STDIO 模式）：

```json
{
  "mcpServers": {
    "mcp-akshare": {
      "isActive": true,
      "name": "mcp-akshare",
      "type": "stdio",
      "command": "uv",
      "args": ["--directory", "/path/to/your/git_dir", "run", "server.py"],
      "installSource": "unknown"
    }
  }
}
```

### Docker 部署

使用 Docker Compose 部署（HTTP 模式，端口 18881）：

```bash
docker-compose up -d
```

服务将在 `http://localhost:18881` 启动

## 工具列表

### `get_stock_symbol_by_name`

通过股票名称获取股票代码

- **name**: 股票名称，类似 '中国平安' or '平安'

### `get_stock_history`

获取股票历史数据

- **symbol**: 股票代码 (6 位数字，如 "000001")
- **start_date**: 开始日期 (YYYY-MM-DD，可选)
- **end_date**: 结束日期 (YYYY-MM-DD，可选)
- **period**: 数据周期 ("daily", "weekly", "monthly")
- **adjust**: 价格调整 ("qfq" 前复权, "hfq" 后复权, "none" 不复权)

### `get_stock_realtime`

获取实时股票数据

- **symbol**: 股票代码 (6 位数字)

### `get_stock_basic`

获取股票基本信息

- **symbol**: 股票代码 (6 位数字)

### `calculate_support_resistance`

计算支撑位和压力位

- **symbol**: 股票代码 (6 位数字)
- **n_levels**: 支撑/压力位级别数量 (1-10，默认 5)
- **lookback_period**: 分析周期天数 (30-365，默认 60)

### `get_market_index`

获取市场指数数据

- **index_code**: 指数代码 (默认 "000001" 上证指数)

## 使用示例

```python
# 通过 MCP 客户端调用
result = await client.call_tool("get_stock_realtime", {
    "symbol": "000001"
})
```

## 开发

### 项目结构

```txt
mcp-stock-server/
├── server.py     # 主服务器文件
├── pyproject.toml         # 项目配置
└── README.md             # 项目文档
```

### 依赖

- fastmcp: MCP 服务器框架
- akshare: 股票数据源
- pandas: 数据处理
- numpy: 数值计算

## 许可证

MIT License
