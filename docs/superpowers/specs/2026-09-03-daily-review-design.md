# A股每日复盘工具设计

- 日期：2026-09-03
- 状态：已与用户确认（方案 A + 全量七模块 + 口径决策）
- 范围：在 StockMCP（FastMCP + akshare 1.18.30）中新增"一键复盘"聚合工具与若干细分工具

## 1. 目标

每天收盘后一次调用即可获取当日 A 股复盘全貌，并保留按模块深挖的细分工具。七个模块：

1. 指数涨跌 + 成交额（上证/深成/创业板/科创50/北证50）
2. 涨跌结构（涨跌家数、涨停/跌停/炸板、连板梯队）
3. 大盘 + 板块主力资金
4. 国家队 ETF 份额变化（复用现有 `etf.py`）
5. 期指基差 + 主力席位净持仓变化（IF/IH/IC/IM + IO/MO/HO）
6. 期权 PCR
7. 两融余额与净变化（T-1）

## 2. 数据源评估（2026-09-03 实测）

| 模块 | 接口 | 历史日期 | 通道 |
| --- | --- | --- | --- |
| 指数快照 | `stock_zh_index_spot_em` | 当日 | 东财（走 proxy，耗积分） |
| 指数历史 | `index_zh_a_hist` | ✅ | 东财（走 proxy） |
| 涨跌家数/活跃度 | `stock_market_activity_legu` | ❌ 仅当日 | 乐咕（直连） |
| 涨停/强势/跌停/炸板/昨涨停池 | `stock_zt_pool_em` 等 5 个 | ✅ date 参数 | 东财（走 proxy） |
| 大盘主力资金 | `stock_market_fund_flow` | ✅（历史序列，取目标日） | 东财（走 proxy） |
| 板块主力资金 | `stock_sector_fund_flow_rank` | ❌ 仅 今日/5日/10日 | 东财（走 proxy） |
| 两融 | `stock_margin_sse` / `stock_margin_szse` | ✅ 区间参数 | 交易所（直连） |
| 期货日行情/主力合约 | `futures_zh_daily_sina` / `futures_display_main_sina` | ✅ / 当日 | 新浪（直连） |
| 期货席位排名 | `ak.get_cffex_rank_table(date, vars_list)` | ✅（自 20100416，每日 16:30 更新） | 中金所官网（直连） |
| 期权席位排名 | 自写官网 CSV：`http://www.cffex.com.cn/sj/ccpm/YYYYMM/DD/{IO|MO|HO}_1.csv`（GBK） | ✅ | 中金所官网（直连） |
| 期权日统计（量/持仓→PCR） | `option_daily_stats_sse` / `option_daily_stats_szse` | ✅ date 参数 | 交易所（直连） |
| 中金所股指期权行情 | `option_cffex_hs300/zz1000/sz50_*_sina` | ✅ | 新浪（直连） |
| 国家队 ETF | 现有 `etf.py`（`fund_etf_spot_em` 等） | ✅ | 东财（走 proxy） |

关键事实：

- `akshare-proxy-patch 0.5.0` 只 hook 东财四个域名（fund/push2/push2his/emweb.eastmoney.com），其余数据源直连、零积分。
- 中金所排名含 `中信期货(代客)` 等前 20 会员，经纪席位口径（该期货公司名下全部客户的合计，非自营）。
- 北向资金自 2024-08-19 起交易所停止披露净买入与个股明细，仅剩成交总额，不纳入本设计。

## 3. 架构（方案 A：平铺模块 + 无状态实时拉取）

```txt
utils/
├── etf.py                 # 已有，国家队 ④；暴露内部数据函数供聚合复用
├── review_market.py       # ① 指数 ② 涨跌结构/涨停生态
├── review_funds.py        # ③ 主力资金 ⑦ 两融
├── review_derivatives.py  # ⑤ 期指基差+席位 ⑥ 期权PCR
└── review.py              # 聚合 orchestrator（并行、降级、缓存、合并 JSON）
```

不引入数据库与调度器；每日复盘实时拉取。落库方案（原方案 B）留作未来演进。

### 工具注册（server.py）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `get_daily_review_tool` | `date`（默认今天） | 一键复盘，聚合七模块 |
| `get_market_breadth_tool` | `date` | 涨跌家数 + 涨停/跌停/炸板统计 |
| `get_zt_pool_tool` | `pool`（涨停/炸板/跌停/强势/昨涨停）、`date` | 池明细 + 连板梯队 |
| `get_fund_flow_tool` | `scope`（大盘/板块）、`indicator`（今日/5日/10日） | 主力资金 |
| `get_margin_tool` | `market`（沪/深/沪深）、`days` | 两融余额与净变化 |
| `get_cffex_rank_tool` | `var`（IF/IH/IC/IM/IO/MO/HO）、`date`、`member` | 席位排名，可按会员过滤 |
| `get_index_derivatives_tool` | `date` | 四品种基差 + PCR |

### 聚合行为

- **并行拉取**：ThreadPoolExecutor 七模块并发，总耗时 ≈ 最慢单模块（预计 5-10 秒）。
- **模块级降级**：单模块失败或该日期无数据 → 该段置 null，写入 `errors` / `notes`，整体不失败（沿用 `etf.py` 的 notes 模式）。历史日期下"板块资金流"与"涨跌家数"固定降级并说明。
- **缓存**：聚合结果 10 分钟 TTL；单模块 60 秒 TTL（复用 `CachedData`）。
- **限流**：沿用 `RateLimiter` / `with_retry` 装饰器模式。
- **输出**：`{date, generated_at, indices, breadth, fund_flow, national_team, derivatives, margin, notes, errors}` 的 JSON 字符串，明细行数设上限（参考 `MAX_DETAIL_ITEMS=30`）。

## 4. 口径决策（定死，避免歧义）

- **基差** = 现货指数收盘价 − 期货主力合约结算价；同时给出年化基差（按剩余到期日折算）。
- **PCR**：认沽/认购的成交量比、持仓量比各一个；上交所、深交所、中金所分开列，不混算。
- **席位摘要**：聚合里只给净持仓变化 Top3 会员；完整前 20 看细分工具。
- **两融**：标注 T-1（交易所隔日发布）。
- **主力资金**：东财口径（超大单+大单净额，推断口径非真实机构），输出中注明。

## 5. 错误处理

- 逐接口 try/except，失败信息进 `errors[模块名]`；全部模块失败才整体报错。
- 非交易日/当日未到更新时间（如席位 16:30 前）：自动回退最近有数据的交易日，并在 `notes` 标注实际数据日期。
- IO/MO/HO CSV 拉取：GBK 解码、HTTP 非 200/空文件视为无数据。

## 6. 测试

- pytest + mock akshare 返回 DataFrame：覆盖降级逻辑、非交易日回退、PCR 与基差（含年化）计算、JSON 结构契约。
- 真实接口冒烟测试打 `live` 标记，默认不跑。

## 7. 成本与运维

- 聚合一次约打 6-8 个东财接口（耗 proxy 积分）；乐咕/新浪/中金所/交易所直连零积分。10 分钟聚合缓存防重复消耗。
- 后续若积分消耗过大，优先减少聚合内东财调用数（如涨停池只拉 3 个），或将方案 B（落库）提上日程。

## 8. 未来演进（不在本期）

- 方案 B：每日定时落库（SQLite/parquet），复盘查库，支持长历史与回测。
- 隐含波动率：无现成接口，需基于期权价格自算（BS 或更简近似）。
- 国家队 ETF 名单扩容（红利 ETF、2024-2025 新增持有品种）。
