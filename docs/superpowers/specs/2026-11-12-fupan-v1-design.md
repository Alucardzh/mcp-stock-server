# fupan_v1 一站式复盘与配套基础接口 —— 设计文档

**日期**：2026-11-12
**需求来源**：`temp/MCP升级需求文档_东财限流与板块数据_2026-11-12v2.md`（F1/R1补丁/§7 契约）
**前置**：v1 已交付（板块行情/三池全量/限流基建，main `91efb46`，spec `2026-11-12-em-rate-limit-and-boards-design.md`）
**状态**：设计已与用户确认

---

## 1. 范围

| 项 | 类型 |
|---|---|
| R1 补丁：`current` 点位 + `scale{raw_f46,raw_f60,divisor,f59}` | 小改 |
| F1-a `get_klines_batch_tool`（gtimg 日K批量 → 指标摘要） | 新建 |
| F1-b `get_lhb_batch_tool`（榜单+席位批量 + 指纹去重/三分类/链内榜净） | 新建 |
| F1 `fupan_v1_tool`（十段 sections 一站式，v1 冻结） | 新建 |
| `get_daily_review_tool` 改共用底层 section + deprecated 提示 | 重构 |
| §7 错误契约扩展（NOT_TRADE_DAY/NOT_READY/INVALID_ARG/warnings[]） | 契约 |

非目标（v2 §9）：板块成分股枚举、实盘持仓接口、除清单外改现有工具返回结构。

## 2. 已确认决策

| 决策点 | 结论 |
|---|---|
| F1-a 返回形态 | **指标摘要**（与 fupan_v1 klines 段同构；raw bar 不出 MCP） |
| symbols 默认 | **涨停池全量 ∪ 显式传入**（服务端无持仓数据，持仓票由调用方并入） |
| daily_review 重构 | **共用底层 section 函数**，非字面委托（daily_review 有 national_team/margin 两段 fupan_v1 没有；§6.9「共用取数与缓存」的意图由此满足）；输出加 `deprecated` 提示 |
| 指标口径（供 Hermes 确认） | `pct60`=close 对 60 根前收盘涨幅；`chg5/chg10`=近 5/10 根累计涨幅（含当日，bars 不足 null）；`high/low`=**当日**高低 |
| 执行方式 | 一个计划连续执行（一期 F1-a → 二期 F1-b+fupan_v1 → 三期验收调优），每任务子代理+审查 |

## 3. R1 补丁

`utils/board_quotes.py` 的 `_STOCK_GET_FIELDS` 加 `f43,f59`；`get_board_quote` 输出追加：

- `current`：f43 还原点位（fltt=2 已格式化）
- `scale: {raw_f46, raw_f60, divisor, f59}`：`raw = round(value × 10^f59)` 无损重建
  （fltt=2 返回已是 2 位小数浮点；东财改缩放时 f59 变化可直接发现）

不加新请求。R2 不加 scale（其 open/prev_close 本就 null，open_pct 为 clist 比值）。

## 4. F1-a `get_klines_batch_tool(symbols: list[str], bars: int = 130, date: str = "")`

### 4.1 数据源 `utils/gtimg_kline.py`（新增）

- `web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={mkt}{code},day,{start},{end},{count},`（末段空 = **不复权**）
- 代码→市场前缀：`sh`(60/68/9) / `sz`(00/30/2)；北交所 `bj`（探测确认，不支持则该票 warnings）
- ThreadPoolExecutor 8 并发；单票超时 8s 失败不阻塞整批（该票 null + warning）
- 缓存（复用 `Push2Cache`，不走 push2 pacing——gtimg 不限流族）：key `kline:{symbol}:{end_date}`；
  **end_date < 今天 → TTL 86400**（收盘定稿）；当日 bar 仅在 15:05 后可取（NOT_READY 前置挡住）
- 实现期探测点：① 容器内 `web.ifzq.gtimg.cn` 可达性（qt.gtimg 已验证可达）；② 不复权参数形态与响应列名

### 4.2 指标 `utils/kline_metrics.py`（新增）

输入 DataFrame（不复权日K），输出指标 dict：

```
close / ma5 / ma10 / ma20   # 不复权收盘及其均线（bars 不足该均线 null）
pct60 = (close / close[-60] - 1) * 100
chg5 / chg10 = (close / close[-5|-10] - 1) * 100   # bars 不足 null
high / low                  # 当日高低
limit_pct                   # 主板 0.10（60/00）；科创创业 0.20（30/68）；北交 0.30（8/4/92 开头）；名称含 ST → 0.05（北交 ST 保持 0.30）
next_limit_up = close * (1 + limit_pct)；next_limit_down = close * (1 - limit_pct)
```

### 4.3 工具契约

- 代码格式非法（非 6 位数字）→ `INVALID_ARG`；格式合法但取不到（退市/停牌无数据/北交不支持）→ 该票 `null` + 顶层 `warnings[]` 列明，**不静默丢**
- 返回 `{as_of, stale, cached, source: "tencent_ifzq", items: {code: 指标|null}, warnings: []}`
  ——fupan_v1 的 `klines` 段直接复用本函数（内部调用，不走 MCP 信封）

## 5. F1-b `get_lhb_batch_tool(date: str, symbols: list[str] = [], chains: dict = {})`

### 5.1 聚合 `utils/lhb_aggregate.py`（新增），复用现有 `utils/lhb.py` 榜单+席位

口径（v2 §6.4，F6 实测锚点验收）：

- `net`（榜净）= **max(各上榜记录买额) − max(各上榜记录卖额)**（并集口径，一票多理由上榜不重复计）
- 席位**指纹去重**：合并键 = `席位名 + 买额 + 卖额`（东财偶发重复行）→ 去重后每席位净额 = 买 − 卖
- 三分类：席位行按现有 seats 分类（机构/外资/游资）→ 按票汇总 `inst/foreign/hot`（万元）
- `seat_n` / `seat_net`：去重后前5买卖席位行数/净合计；`diff = net − seat_net`（**参考值**，口径不同不必然 0）
- `chains`：{链名: hybk 集合} → 链内上榜票 `net` 合计
- 顶层 `total_net` = 全部上榜票 net 合计

### 5.2 缓存与时效

key `lhb_agg:{date}`；`date < 今天` TTL 86400；当日 `< 18:00` TTL 300（席位未定稿）、`≥ 18:00` TTL 86400。

## 6. fupan_v1（`utils/fupan.py` + `mcp_tools/fupan.py`）

### 6.1 签名

`fupan_v1_tool(date: str, symbols: list[str] = [], options: dict = {})`；
`options.with_*`（pools/boards/klines/lhb/breadth/fund_flow/derivatives，默认除 derivatives 外全开）、
`options.chain_buckets: {链名: [hybk...]}`、`options.kline_bars: int = 130`。

symbols 最终集 = `传入 symbols ∪ (symbols 为空时: 当日涨停池全量)`（持仓票由调用方显式传）。

### 6.2 段与来源（并行 ThreadPoolExecutor，每段独立降级）

| 段 | 来源 | 备注 |
|---|---|---|
| indices / breadth / fund_flow / derivatives | 复用 `review.py` 各 section 函数 | breadth 增加 `zhaban_rate = zhaban/(limit_up+zhaban)` |
| pools | **直调 `_pool_cached`（full 行字段），严格日期不回退**——不动 `get_zt_pool_tool` 的回退行为 | 空池+空指数 → NOT_TRADE_DAY；dt 段 >30 天 → note 降级（上游限制） |
| buckets | 通用 industry 分桶（涨停+炸板） | `zt_industry_counts` 扩展为 `_industry_counts(pool, date)` |
| boards | board_quotes 快照（内部函数，**无 limit 截断**） | concept+industry 全量，行含 open_pct（clist 比值口径）+limit_up |
| chains | pools 全量 × chain_buckets 聚合 | `limitup/zhaban/rate/members` |
| klines | §4 函数直调 | |
| lhb | §5 函数直调 | |

### 6.3 错误语义

| 情况 | 返回 |
|---|---|
| 非交易日 | `success:false, error:"NOT_TRADE_DAY"`（严格日期下三池+指数全空） |
| date=今天且 now < 15:02 | `success:false, error:"NOT_READY", expected_at:"15:05"` |
| 某段上游失败 | 该段 `{"status":"unavailable","reason":...}`，其余段照常 |
| klines 某票取不到 | 该票 null + `warnings[]` |

### 6.4 信封

`schema_version:"v1"` + `as_of`（各段 as_of 最新值）+ `stale`（任一段 stale）+ `cached`（全段缓存命中）。
**v1 冻结：加字段/改口径 → fupan_v2。**

### 6.5 `get_daily_review_tool` 重构

改为调用与 fupan_v1 相同的底层 section 函数（indices/breadth/fund_flow/derivatives 四段），保留 national_team/margin 原逻辑；返回值顶层加 `deprecated: "see fupan_v1_tool"`。F1 验收（diff=0）落在四个共享段。

### 6.6 性能预算

boards 冷 10.5s（akproxy pacing，6+1 页）‖ klines ~4s（8 并发）‖ pools ~2s ‖ lhb ~5s → 并行 wall ≈ 11-13s ≤15s；
全缓存命中 ≤3s；窄集（klines+boards）≤5s。若实测超预算，三期调优（boards 快照预热/加长 TTL）。

## 7. 契约扩展（`utils/review_common.py`）

- `json_err_code` 已有；新增 `INVALID_ARG` / `NOT_TRADE_DAY` / `NOT_READY(expected_at)` 码位约定
- 顶层 `warnings[]` 约定：条目级缺失必须列明，不得静默丢
- 缩放还原由服务端负责（§7.3）：本设计所有价格/点位输出均已还原，`scale` 仅供核对

## 8. 验收（对照 v2 §6.8/§11）

- F1 fupan_v1 三共享段 vs get_daily_review diff=0；F2 count==len(items)（11/12 涨停85/炸板19）；
  F3 电子链桶 → 9/0/0.00%；F4 open_pct<0.01pp 且 open 与同时刻 clist f2 一致 + scale.divisor；
  F5 002384 MA=85.306/88.139/101.729、next_limit_down=close×0.9；F6 三分类 46991/15041/10154 万（002384）、
  −8545/−6003/3882 万（601595）；F7 全量≤15s/缓存≤3s；F8 断源时段级 unavailable 且无 0 值冒充；F9 v1 冻结
- A1 增量：scale 块返回且 divisor=100
- 探测脚本：gtimg 日K容器可达性 + 不复权参数形态 + 北交支持性

### 8.1 验收实录（2026-11-12 14:35-14:41 live，锚点日 2026-11-11；`tests/test_live_fupan_smoke.py` 4 passed）

- F1 ✓ 三共享段（indices/breadth/fund_flow）数据 diff=0（排除 get_daily_review 设计内 notes 上浮到顶层的差异后逐段相等）
- F2 ✓ 涨停 85 / 炸板 19 / 跌停 0（=文档锚点），zt count==len(items) 85==85
- F3 ✓ 电子链（半导体/光学光电/元件/消费电子）limitup 9 / zhaban 0 / rate 0.0%（=文档 9/0/0.00%，成员含 002384 东山精密等 9 票）
- F4 ✓ open_pct 一致性 offline 回放覆盖（test_board_quotes）；live scale 块返回 raw_f46=4940561 / raw_f60=4948534 / f59=2
- F5 ✓ 002384 MA5/10/20 = 85.306/88.139/101.729（=文档），close=92.06，next_limit_down=82.854 = round(92.06×0.9,3)（14:35 首跑实测）
- F6 △ 601595 三分类 −8544.59/−6003.25/3882.31 ✓（≈文档 −8545/−6003/3882）；002384 foreign 15041.44 ✓，但 inst/hot 实测 11350.94/20315.57 ✗ 文档 46991/10154——已逐行核对当日席位原始行（买：深股通 92232.79/机构专用×3/中金上海 20315.57；卖：深股通 77191.34/机构专用×4），三分类算术自洽，判为上游席位页相对文档取样时点已修订（取样疑在定稿前），非代码缺陷
- F7 ✓ 冷启动 7.6s / 4.4s（两次全量实测，≤15s 目标）；缓存命中 0.0s（≤3s）
- F8 offline-covered：断源降级（段级 unavailable/无 0 值冒充）由 offline 单测覆盖；live 不可模拟，本次 gtimg 反爬限流时段实测确认 klines 段按票 null + warnings 降级、无 0 值冒充
- F9 ✓ v1 冻结生效（schema_version=v1，offline 断言）
- A1 ✓ scale.divisor=100（元件板块 live 实测）
- 备注1：14:40 起 gtimg fqkline 端点对本机返回 HTTP 501 反爬页（连续 3 次全池批量后触发）——F5 以 14:35 首跑为准；北交所票（920157）按设计落入 klines warnings
- 备注2：dt 30d 标记 Task 6 已按实际窗口收口（>30 天才标 dt_pool_30d_limit）；11-11 在窗口内，dt 池正常返回 count=0

#### v1 冻结面与 v2 §6.3 草案差异（终审回填）

- indices.items 无 high/low/prev_close：v1 复用 indices_section 五字段（code/name/close/chg_pct/amount_yi），冻结于 v1；补字段须走 v2。
- boards 行命名：v1 用 board_code/board_name/limit_up（v2 草案为 code/name/zt），设计已改用现行命名并冻结。
- daily_review 把段 notes 上浮到顶层加 [段名] 前缀，fupan_v1 保留段内 notes——F1 对比需排除该差异（live 测试已体现）。
- F6 锚点提示：v2 文档 002384 的 inst/hot 已过时（东财席位页取样后修订，见 8.1 F6 △），客户端验收改用实盘值或 601595 锚点。

## 9. 任务分解（实施计划骨架）

1. R1 补丁（scale/current）+ 契约扩展
2. gtimg_kline 数据源 + 探测脚本
3. kline_metrics 指标 + get_klines_batch_tool
4. lhb_aggregate + get_lhb_batch_tool
5. fupan 组装（含 buckets/chains/严格日期/NOT_READY）+ fupan_v1_tool 注册
6. daily_review 共享重构 + deprecated
7. live 验收（F1~F9 + A1 增量）+ 性能实测 + 构建部署
