# 全球市场监控 MCP 工具设计

- 日期：2026-09-04
- 状态：已与用户确认（方案 A + 全部 4 工具 + 主备矩阵 + MOF 缓存/代理降级 + 简版工具3/薄逻辑工具4）
- 需求来源：`docs/global-markets-mcp-requirements.md`（光明/Hermes 投研工作流，v1.0）
- 部署目标：与现有 StockMCP 同仓库同进程（生产 192.168.3.10:18881，Docker）

## 1. 目标与范围

为盘前场景（8:00-9:15）提供全球市场输入变量，4 个 MCP 工具：

1. `get_global_markets_tool`（P0）——全球快照聚合：美股/美债/汇率/亚太/商品/恐慌 六组
2. `get_stock_global_snapshot_tool`（P0）——全球个股快照：美股/日韩 preset 组
3. `get_fed_watch_tool`（P1）——美联储预期简版：2Y 收益率 + 10Y-2Y 利差 + 2Y 周变动（不做概率推算）
4. `get_global_linkage_review_tool`（P1）——共振复盘：隔夜全球 vs 当日 A 股对照表（薄逻辑，verdict 留给 agent）

范围决议（2026-09-04 用户确认）：全部 4 工具一个 spec+计划；preset 照用+顺带扩充（台积电并入 ai_chain、SMH/AIQ/BOTZ 做 etf 子组）；工具3 简版、工具4 薄逻辑。

**明确不做**：北向已停披露类数据；加息概率推算；服务端 linkage_verdict 模板；雅虎 yfinance 库依赖（自写 curl_cffi fetcher）。

## 2. 已验证数据源（2026-09-04 实测，akshare 1.18.30）

### 2.1 主备矩阵（核心原则：服务不依赖代理存活）

| 组 | 主源（直连零积分） | 备源 | 降级终点 |
| --- | --- | --- | --- |
| 美股三大+VIX+恒科 | 腾讯批量 `https://qt.gtimg.cn/q=usIXIC,usINX,usDJI,usVIX,hkHSTECH`（1 次调用，GBK，字段：现价[3]/昨收[4]/涨跌%[32]） | Yahoo `^IXIC/^GSPC/^DJI/^VIX/^HSTECH`（经代理） | null+error |
| 费城半导体 | 新浪 `index_us_stock_sina(symbol=".SOX")`（全历史日K） | Yahoo `^SOX` | null+error |
| 日经/KOSPI/DXY | 东财 `index_global_spot_em()`（1 次调用含 `100.N225/100.KS11/100.UDI`，走 proxy 为全工具唯一积分消耗） | Yahoo `^N225/^KS11`（DXY 无 Yahoo 代码，仅东财） | null+error |
| 美债 10Y/30Y/2Y/利差 | `bond_zh_us_rate()`（东财 datacenter **直连**不走 proxy hook；19 页分页约 4s；列：`美国国债收益率2年/5年/10年/30年`、`美国国债收益率10年-2年`） | Yahoo `^TNX/^TYX`（实时，快） | null+error |
| 日债 10Y+20Y/30Y | **MOF 官方 CSV 直连**（见 2.2） | MOF **经代理**（用户要求保留） | 本地缓存旧数据+标注日期 |
| USDCNH | Yahoo `USDCNH=X`（唯一离岸源，经代理） | `fx_spot_quote()` 在岸 USD/CNY 近似+标注 | null+error |
| 布伦特/WTI/黄金 | `futures_global_spot_em()`（640 合约，`futsseapi.eastmoney.com` 直连不走 hook；按名称过滤主力合约：布伦特原油/WTI 需名字映射/COMEX黄金） | Yahoo `BZ=F/CL=F/GC=F` | null+error |

### 2.2 MOF 日债源（实测）

- 当月（T+0）：`https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv`（小文件，日频，实测最新 2026/9/3）
- 全历史：`https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv`（13,291 行，1974 至今，约 1.2MB，完整曲线 1Y/2Y/3Y/4Y/5Y/6Y/7Y/8Y/9Y/10Y/15Y/20Y/25Y/30Y/40Y）
- 编码 cp932；前 2 行为表头（期限标签在第二行）；日期格式 `2026/9/3`
- 国内直连实测 HTTP 200；经代理（192.168.3.10:7890）亦通
- bp 变动 = 目标日与前一日历行差值 ×100

### 2.3 Yahoo fetcher（实测）

- 端点 `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d`，免 key
- 必须走 `YAHOO_PROXY`（国内直连/裸指纹均 403）；用 `curl_cffi`（项目已有依赖）`impersonate="chrome"` + proxies
- meta 字段：`regularMarketPrice`/`chartPreviousClose`/`currency`/`shortName`；`includePrePost=true` 取美股盘前盘后
- 实测延迟 0.4-0.6s/只；代码验证通过：`000660.KS`/`005930.KS`/`285A.T`/`NVDA`/`MRVL`/`COHR`/`^SOX`/`^TNX`/`^TYX`/`^N225`/`^KS11`/`^VIX`/`USDCNH=X`
- 代理失败/未配置：跳过所有 Yahoo 主备源走国内降级，整组照常返回

### 2.4 环境

- 新增 `YAHOO_PROXY`（env.template 默认 `http://192.168.3.10:7890`）；空=禁用 Yahoo 路径
- 生产需确认 akshare ≥ 1.18.30 且可直连腾讯/新浪/MOF/futsseapi/datacenter（与中金所直连同假设）

## 3. 架构（方案 A：平铺模块，沿用 review_* 模式）

```txt
utils/
├── review_common.py      # 已有，直接复用（safe_num/json_ok/json_err/_sanitize/parse_day/prev_trading_days/col_like）
├── global_common.py      # 新增：Yahoo fetcher（curl_cffi+代理+超时）、腾讯批量行情 fetcher、MOF fetcher（直连→代理→本地缓存三级）
├── global_markets.py     # 工具1：六组快照（分组并行、组内字段级降级）
├── global_stocks.py      # 工具2：preset 组 + Yahoo 逐只（并行）
├── global_rates.py       # 工具3：美债 2Y/利差/周变动（bond_zh_us_rate 单源）
└── global_linkage.py     # 工具4：隔夜腿（新浪历史+美债历史+MOF）vs 当日A股腿（复用 review sections）
server.py                 # 注册 4 个 *_tool（沿用 utils 无后缀 + server *_tool 包装惯例）
data/global/              # MOF 持久化缓存（gitignore 整个 data/ 目录）
tests/                    # 全 mock + live 冒烟（默认跳过）
```

## 4. 工具契约

### 4.1 `get_global_markets(groups: str = "全部") -> str`

- groups：逗号分隔或"全部"；合法值：美股/美债/汇率/亚太/商品/恐慌
- 返回 `{"success", "data": {as_of, <组名>..., notes, errors}}`；每组内单字段失败→该字段 `{close: null, error: "..."}` 不阻塞组；组级失败→`null` + errors
- 组内容（口径）：
  - 美股：indexes[{name, close, chg_pct}]×4（纳指/标普/道指/费半）+ note（"9/3收盘"）
  - 美债：us10y/us10y_chg_bp/us30y/us30y_chg_bp（bp=日差×100，源为收益率%）+ japan10y/japan10y_chg_bp/japan20y/japan30y（MOF 源，长端顺带返回）
  - 汇率：dxy/dxy_chg_pct（东财）+ usdcnh/usdcnh_chg_pct（Yahoo；降级时在岸近似并加 note）
  - 亚太：日经/KOSPI（东财）+ 恒生科技（腾讯）+ note（盘中或收盘）
  - 商品：brent/wti/gold 各 {price, chg_pct}
  - 恐慌：vix/vix_chg_pct
- 结果缓存 300 秒；单源缓存 60 秒
- as_of = 响应生成时刻（ISO8601 带 +08:00 时区）；各组数据的实际时点在该组 note 中标注（如"隔夜收盘"/"盘中快照"/数据日期）

### 4.2 `get_stock_global_snapshot(symbols: str = "", preset: str = "ai_chain") -> str`

- preset：`ai_chain`（NVDA/AVGO/MRVL/COHR/LITE/APH/ANET + 台积电 2330.TW）/ `storage`（000660.KS/005930.KS/285A.T/MU/SNDK）/ `etf`（SMH/AIQ/BOTZ）/ `all`（三组并集）；symbols 显式代码（逗号分隔，Yahoo 语法）优先于 preset
- Yahoo 并行逐只（ThreadPoolExecutor）；美股附 `after_hours_pct`（includePrePost）；单只失败→该行 `{symbol, close: null, error}`；Yahoo 整体不可用→error 信封（本工具唯一源）
- 返回 `{as_of, quotes: [{symbol, name, market, currency, close, chg_pct, after_hours_pct?}]}`

### 4.3 `get_fed_watch() -> str`

- `bond_zh_us_rate` 单源：返回 `{as_of, us2y, us10y, spread_10y_2y, us2y_chg_1w_bp, note}`（不做概率推算，note 注明"预期变化请结合 FOMC 日程解读"）

### 4.4 `get_global_linkage_review(date: str = "") -> str`

- 隔夜腿（目标日前一交易日）：纳指/SOX（新浪历史）、美债 10Y 及 bp 变动（bond_zh_us_rate 历史）、日债 10Y 及 bp（MOF 历史）、VIX（仅当日快照或 null+note，无历史源）
- 当日 A 股腿：**直接调用现有** `indices_section`/`breadth_section`/`sector_fund_flow_section`（电子/通信板块数据由 sector 全表过滤"电子"/"通信"行业行）
- 输出 `{date, overnight: {...}, a_share: {...}, notes}`；无 verdict 字段（薄逻辑）；A股腿模块级降级沿用现有 section 行为

## 5. MOF 缓存与降级（用户要求）

1. `data/global/jgbcme_all.csv`：本地存在即不重复下载（仅首次/文件损坏时）
2. 日常取值：当月 `jgbcme.csv`（TTL 3600s，内存缓存）取最新行与前一行算 bp
3. 取值链：**直连 → 经 YAHOO_PROXY 代理 → 读本地缓存**（缓存命中时 note 标注实际数据日期）
4. 全历史文件在本地缺失时按同一三级链下载并落盘

## 6. 错误处理

- 所有 fetcher try/except 包裹，失败返回 None 由上层降级；`json_ok` 已带 NaN/Inf→None 净化
- 腾讯字段索引越界/空响应→None；Yahoo 非 200/chart.error→None；MOF 非 200 或解析异常→走下一级
- 组装层沿用 notes/errors 双通道（`[组名]` 前缀）

## 7. 测试

- 单测全 mock：腾讯/Yahoo/MOF/东财 fetcher 在模块命名空间 monkeypatch；覆盖降级矩阵（主源失败走备源）、代理未配置、缓存命中、bp 计算、preset 展开
- live 冒烟（`-m live`）：工具1 全组、工具2 preset=ai_chain、MOF 三级链
- 沿用 `./.venv/Scripts/python.exe -m pytest`；测试不发起真实网络

## 8. 验收对照（需求文档三）

1. 工具1 盘前 10s 内返回、字段级不阻塞 → 并行分组+单源缓存 ✅（设计层面满足，实测最慢腿美债约 4s）
2. 工具2 preset=ai_chain 返回美股报价（扩充后 8 只）；日韩允许 error 占位 ✅
3. 工具3 至少返回 2Y+利差 ✅
4. 工具4 输入 2026-09-03 复现传导对照 ✅（数据均为历史可取）
5. 中文描述 + success/data/error 信封 + 亿元单位（本域金额场景少，汇率/收益率保留原单位%）✅
6. 需求方 skill（`akshare-tavily-stock-workflow`）的工具分配表更新——**部署后人工动作，不在本仓库范围**

## 9. 未来演进（不做）

- 加息概率推算（CME FedWatch 转引或利率期货隐含）
- 服务端 linkage_verdict 规则模板
- Stooq 等第三方源（实测国内不可达）
