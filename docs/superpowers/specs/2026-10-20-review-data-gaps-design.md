# 复盘数据缺口补齐设计

- 日期：2026-10-20
- 状态：草案（待用户确认）
- 范围：StockMCP 新增/增强 6 项数据能力（复盘四缺口 + 竞价分时 + 龙虎榜收编）+ 内建竞价 CSV 归档

## 1. 背景与评估结论（2026-10-20 实测）

| 需求 | 评估结论 | 证据 |
| --- | --- | --- |
| 三池日期参数 | **已存在**，无需开发 | `get_zt_pool` 自 e236075 起带 date + 交易日回退；实测 `date="2026-10-16"` 返回 61 只（含代理链路）。用户"只出当日"的观察疑为远程旧镜像，随本期重部署一并解决 |
| 封板时间 | 免费可得，仅字段白名单未放行 | akshare 涨停池原始返回自带 首次封板时间/最后封板时间（HHMMSS），被 `review_market.py` 的 `_POOL_FIELDS` 过滤 |
| 行业/概念粒度 | 一次调用可解 | 截断（"汽车零部"）是东财 push2ex 上游固有；`ef.stock.get_belong_board`（push2 slist）返回完整行业名+概念+地域+属性板块混合体（实测 600825 得 23 项） |
| 龙虎榜 | 榜单 efinance 有封装；席位无 | `get_daily_billboard` 打 datacenter-web（RPT_DAILYBILLBOARD_DETAILS，含东财解读）；席位明细两家皆无封装 |
| 竞价分时 9:15-9:25 | **当日可得**；历史免费源无档 | push2 `details/get`（efinance `get_deal_detail`）实测当日分笔从 09:15:00 起，竞价阶段每行=虚拟撮合快照（虚拟匹配价+累计匹配量，手）；无 date 参数；trends2 `iscca=1` 实测无盘前点（仅多盘后 30 点） |
| 分钟分时 | 当日 1 分钟 / 历史 5 分钟 | efinance `get_quote_history(klt=1)` 实测仅当日 240 根，首根开盘价=集合竞价撮合价 |

## 2. 数据源选型（用户已拍板：efinance 能用尽用）

选型规则：**efinance 有封装 → 主通道，akshare 备用；efinance 无 → akshare 独走；两家皆无（席位明细）→ 裸 API + 补域名**。两库东财流量同被 `akshare_proxy_patch` 按 hook_domains 接管，连通性无差别，差别仅在封装有无与健壮性（akshare 解析层崩过 `stock_individual_info_em`）。

| 能力 | 主通道 | 备用 | 历史 | 域名/hook |
| --- | --- | --- | --- | --- |
| 涨停等五池 | — | akshare `stock_zt_pool_em` 等 5 个 | ✅ date | push2ex（走 proxy，已有） |
| 封板时间字段 | — | 同上原始字段 | ✅ | 同上 |
| 分钟分时 | ef `get_quote_history(klt=1/5)` | akshare `stock_zh_a_hist_min_em` | 1min 仅当日 / 5min 历史 | push2his（已 hook） |
| 竞价分时（当日） | push2 `details/get` 直连（requests；efinance 封装会丢弃第 5 段方向字段） | — | ❌ 仅当日 | push2（已 hook） |
| 行业+概念 | ef `get_belong_board` | akshare 概念名单（过滤用） | 实时 | push2（已 hook） |
| 龙虎榜榜单 | ef `get_daily_billboard` | akshare `stock_lhb_detail_em` | ✅ | datacenter-web（榜单不需新 hook，走 efinance session 已被接管） |
| 龙虎榜席位 | — | akshare / 裸 URL（lhb_fetch.py 同源） | ✅ | datacenter-web（**需加 hook_domains**） |

## 3. 工具设计

### 3.1 `get_zt_pool` 增强（改动最小，P0）

- `_POOL_FIELDS` 的"涨停/炸板"白名单补两列：`首次封板时间`、`最后封板时间`（akshare 已 zfill(6) 格式化）。
- 模块②"最早上板"识别：调用方按首次封板时间排序即得，历史日同样有效，无需新接口。
- 行业 4 字截断不在本工具修（见 3.4），items 保留原字段作兼容。

### 3.2 `get_stock_intraday_tool`（新，分钟分时，P1）

- 参数：`symbol`、`period=1|5`（分钟）、`mode=summary|full`（默认 summary）、`date`（5min 可历史；1min 仅当日，历史日返回降级说明）。
- summary 输出：开盘价（=竞价撮合价）、竞价量（首根 bar 成交量）、开盘 30 分钟 OHLC/量额、全天高低点及时间、尾盘 30 分钟统计、涨停价触碰时间线（涨停价按板块规则：主板 ±10% / 创业板科创 ±20% / 北交 ±30% / ST ±5%，四舍五入到分）。
- full：全量分钟 bar（1min 为 240 行），文档警示 token 占用。
- 实现：`utils/ef.py` 的 `_KLT_MAP` 补分钟档 `{"1":1,"5":5,"15":15,"30":30,"60":60}`；主 ef 备 akshare（沿用 etf 双通道模式）。

### 3.3 `get_stock_auction_tool`（新，竞价分时，P1）

- 参数：`symbol`、`date`（默认当日）、`mode=summary|detail`（默认 summary）。
- 主通道：push2 `details/get` 直连（requests，保留原始五段：时间/价格/成交量/单数/方向）→ 切 9:15-9:25 段。
- summary 输出：昨收（prePrice）、竞价撮合价、竞价量/额、高开幅度、9:15/9:20/9:25 三档价格轨迹（各段末价格+累计匹配量）、9:20-9:25 真实意愿段标注（9:20 前可撤单，假单噪音大）。
- detail：竞价段逐笔快照（几十~几百行）。
- 日期语义（读写穿透归档，见 §6）：
  - `date=当日` → 实时拉取 + 落盘 CSV 归档；
  - `date=历史日` → 读本地归档 CSV，无归档则报错说明（免费源无档，无法回补）。

### 3.4 `get_stock_boards_tool`（新，行业+概念，P1）

- 参数：`symbol`、`with_raw=False`。
- 主通道 ef `get_belong_board`；输出：`industry`（完整名）、`concepts`（过滤后概念列表）、`raw`（可选全部板块）。
- 概念过滤：与概念板块 BK 代码名单求交集（剔除"昨日高振幅/趋势股"类动态属性板块与地域板块）；名单用 akshare `stock_board_concept_name_em` 拉取，当日缓存。
- 模块⑦聚类口径（纯概念 vs 概念+行业）由报告层决定，数据层两个字段都给。

### 3.5 `get_lhb_tool`（新，龙虎榜榜单，P2）

- 参数：`date`（默认当日）。
- 主 ef `get_daily_billboard`（单日区间）+ 备 akshare `stock_lhb_detail_em`。
- 输出：上榜家数、按股聚合净买/卖（亿元）、上榜原因、东财解读；净买 TOP / 净卖 TOP 摘要。

### 3.6 `get_lhb_seats_tool`（新，席位明细，P2）

- 参数：`symbol`、`date`。
- 通道：akshare `stock_lhb_stock_detail_em(symbol, date, flag="买入"/"卖出")`（打 datacenter-web，需加 hook_domains）；若 akproxy 不放行，兜底照搬 `temp/lhb_fetch.py` 裸 URL（同源，已在 cron 环境验证）。
- 输出：买/卖前 5 席位、金额（万元）、机构/外资/游资分类（沿用 lhb_fetch.py 的 classify 规则）。
- 文档注明：当日数据 ≥18:00 拉取席位才齐全。

## 4. 基建变更

- `utils/tools.py` 的 `install_patch` hook_domains += `datacenter-web.eastmoney.com`（仅席位需要；**前置冒烟：akproxy 是否放行**，不放行则席位走裸 URL+显式代理，hook 不加）。
- docker-compose 加 volume 挂载（竞价归档持久化，见 §6），归档根路径经 env `AUCTION_ARCHIVE_ROOT` 可配（默认 `/app/data/auction`）。
- README 工具列表补 5 个新工具条目。
- 不引入数据库与调度器，沿用平铺模块 + 无状态实时拉取（竞价归档是唯一的本地状态，纯文件）。

## 5. 部署

- 本机 `docker build` + `push docker.sihanfu.cn:15999/mcp-stock-server:latest`；用户在远程 `docker compose pull && up -d`（新 volume 挂载随 compose 生效，宿主机目录自动创建）。
- 旧镜像"只出当日"问题随之消除；部署后回归：`list_tools`、`get_zt_pool(date=历史)`、新工具各一次冒烟。

## 6. 竞价历史归档（StockMCP 内建，CSV）

- 归档机制内建在 `get_stock_auction_tool`（读写穿透）：当日首次拉取即落盘；历史查询直接读盘，省去重复请求。与 DailyReview 无关。
- 目录形式：`{AUCTION_ARCHIVE_ROOT}/{code}/{date}.csv`，每股一个目录、每日一个文件。
- CSV 列（details/get 原始五段透传，无加工）：`时间,价格,成交量,单数,方向`。昨收等元数据不落盘，需要时从日 K 补（历史恒可查）。
- 持久化：docker-compose 挂 volume（`./data/auction:/app/data/auction`），容器重建不丢档。
- 上线日起积累；更早历史无法回补。
- YAGNI：先不做查询/聚合层，仅为当下反复查询节约时间。

## 7. 通达信调研结论（2026-10-20 实测）

- pytdx（自带 12 个公共服务器）与 mootdx（自动选优）双路实测：TCP 可达但全部查询返回空 → 当前网络环境下经典 7709 协议不可用（服务器迁移/协议老化）。
- 即使可用，对本项目的价值仅"历史 1 分钟线 / 历史分笔"；且 raw TCP 无法过 HTTP 代理，容器出网策略另需适配。
- 结论：本期不引入。若未来"历史分钟线/历史竞价"成为硬需求，评估方向为付费 tick 源或 TDX 专用 TCP 转发。

## 8. 测试

- pytest 单测：`_POOL_FIELDS` 补列后的输出断言（沿用 `tests/test_review_market.py` monkeypatch 模式）；新工具各配 mock 单测（efinance 返回 fixture）。
- 本地 MCP 冒烟注意已知坑：SSE 挂起（用 python client 而非 curl）、GBK 编码。
- 部署后容器冒烟：席位（datacenter-web 经 akproxy）、竞价（当日）、分钟（当日+历史 5min）。

## 9. 不做清单（YAGNI）

美股资金流（无免费源）、港股+南向、历史分钟主力（东财无档；当日已有 `get_today_bill` 方案，需要时再挂）、通达信。

## 10. 开放问题

- akproxy 是否放行 datacenter-web —— 实现首日冒烟，决定席位通道走 akshare 还是裸 URL（不影响其余工具）。
