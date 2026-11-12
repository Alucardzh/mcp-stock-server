# 东财限流感知与板块数据 —— MCP 服务端升级设计

**日期**：2026-11-12
**需求来源**：`temp/MCP升级需求文档_东财限流与板块数据_2026-11-12.md`（R1~R5）
**状态**：设计已与用户确认

---

## 1. 背景与目标

2026-11-12 早盘竞价任务因东财 push2 接口对客户端出口 IP 限流（空响应形态），决策延迟 11 分 52 秒。
升级目标：**易限流的东财接口调用全部收归 MCP 服务端**，服务端做限流感知调度、缓存与降级，
并以明确的语义（`unavailable` / `no_open` / `stale`）区分「数据缺失」与「条件不满足」。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| R1/R2 上游通道 | **全走 akproxy**（复用现有 `akshare_proxy_patch` 接管的 requests；板块行情每刷新周期 ≈3 个上游请求，60s 缓存下积分消耗可控） |
| R3 交付形态 | **不新建 `get_pool_full_tool`**，只给现有 `get_zt_pool_tool` 加 `full` 参数（默认 false 保持截断 60 条，true 返回全量）。字段名沿用现有中文名，客户端自行映射 |
| 重试语义 | **分层重试**：服务端空响应 → 退避 ≥4s → 第二轮 → 仍空则 `status:"unavailable"`（终态，客户端不重试）；客户端只对入口级 `success:false, error_type:"RateLimitError"` 做退避重试；业务错误（`BOARD_NOT_FOUND`）不重试 |
| 范围 | 本期仅服务端 + 响应契约。R4 客户端 `mcp_fetch` 封装与脚本迁移属 Hermes 仓库，不在本 repo |

### 现状事实（设计依据）

1. `get_zt_pool_tool` 上游（akshare → `push2ex.eastmoney.com/getTopic*Pool`）**本就返回全量**，
   截断 60 条是服务端自己的 `MAX_POOL_ROWS`（`utils/review_market.py`）。push2ex 不在
   akproxy hook 域名列表内，容器直连且目前正常——R3 无需新通道。
2. `akshare_proxy_patch` 按 URL **子串**匹配：`push2.eastmoney.com`（含 `82.push2.`）、
   `push2his`、`datacenter-web` 等走 akproxy；服务端 `requests.get(...push2...)` 自动被接管。
3. 现有基建可复用：`CachedData`（单槽 TTL）、`RateLimiter`（入口限流）、`with_retry`（网络异常重试）。
   缺：多 key 缓存、single-flight、空响应识别、`as_of/stale/cached` 元数据。

## 3. 架构

```
mcp_tools/boards.py        get_board_quote_tool / get_boards_tool（新分类模块）
        │
utils/board_quotes.py      板块快照构建（clist 列表 + ulist 开盘价 → 组装）
        │
utils/push2_client.py      东财 push2 统一客户端（新基建，唯一上游出口）
        │  pacing / 空响应识别 / 退避 / TTL 缓存 / single-flight / stale 降级
        ▼
requests（已被 akshare_proxy_patch 接管）──► akproxy ──► 东财 push2

utils/review_market.py     get_zt_pool 加 full 参数 + 三池缓存 + as_of/stale/cached
```

### 3.1 `utils/push2_client.py`（新增）

东财 push2 家族请求的唯一出口，全部走被 patch 接管的 `requests`（→ akproxy）。

**核心行为**：

- **空响应识别**：HTTP 200 但 `data` 为 null/空 → 判为 `rate_limited`
  （东财限流的实际形态；akproxy 出口下同样以此形态出现）。
- **家族级 pacing**：连续上游请求间强制最小间隔，默认 1.5s，
  env `EM_PUSH2_MIN_INTERVAL` 可调。冷启动一轮（clist 行业 + clist 概念 + ulist 批量）
  ≈ 3 请求 × 1.5s ≈ 4.5s；命中缓存后瞬时返回。
- **退避**：空响应 → sleep ≥4s → 第二轮 → 仍空 → 返回 `unavailable`（终态），不无限重试。
- **多 key TTL 缓存 + single-flight**：`threading.Lock` + per-key in-flight 标记，
  并发同 key 只出一次上游请求；**上游失败不写缓存**。
- **stale 降级**：缓存过期后重拉失败但存在旧缓存 → 返回旧数据 + `stale:true`；
  彻底无缓存才返回 `unavailable`。

**接口草案**：

```python
def em_push2_get(path: str, params: dict, *, cache_ttl: float | None = None) -> EmResult
# EmResult: data, ok, reason ("rate_limited"|"upstream_error"), as_of, cached, stale
```

### 3.2 `utils/board_quotes.py`（新增）

两个上游请求构建板块快照：

1. **clist 板块列表**：`fs=m:90+t:2`（行业）/ `m:90+t:3`（概念），`pz=600`，
   `fields=f12,f14,f2,f3,f62,f104,f105,f128,f140,f141`
   → 板块代码/名称/最新价/当前涨跌幅/主力净流入/上涨家数/下跌家数/领涨股名/代码/涨跌幅。
   缓存 60s。`main_net_yi = f62 / 1e8`。
2. **ulist 批量开盘价**：`api/qt/ulist.np/get?secids=90.BK0459,...&fields=f12,f46,f60`
   → 一次请求拿 N 个板块的开盘/昨收，`open_pct = (f46-f60)/f60*100`。
   **实现期验证点**：ulist 是否支持板块 secid；不支持则退化为逐板块 `stock/get`
   （fields 必须全集：`f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f62,f104,f105,f107,f168,f169`）。

**`limit_up`（板块内涨停家数）**：clist 无现成字段。从涨停全量池按 `所属行业`
**前缀匹配**（容忍池内 4 字截断名）计算，算不出则省略字段，响应不报错。

### 3.3 `mcp_tools/boards.py`（新增分类模块）

**`get_board_quote_tool(board_code: str = "", board_name: str = "")`**（R1，最高优先）

- 都不给 → 返回全部行业+概念板块精简表，行结构与 `get_boards_tool` 的 `boards[]` 一致
  （复用同一快照，含行业+概念两组）。
- 查不到板块 → `success:false, error_code:"BOARD_NOT_FOUND"`。
- f46 为空（未开盘/停牌）→ `data.open_pct=null, status:"no_open"`。
- `board_name` **原文透传**（含 `游戏Ⅱ` 罗马数字、`自动化设` 截断名，不做规范化/补全）。
- 返回字段：`board_code/board_name/scope/open/prev_close/open_pct/current_pct/main_net_yi/
  up/down/limit_up?/leader_name/leader_code/leader_pct/as_of/stale/cached/source`。

**`get_boards_tool(scope="行业", sort="main_net", limit=20, with_open=true)`**（R2）

- `scope`: `行业`/`概念`；`sort`: `main_net`/`chg`/`open_pct`；`limit ≤ 50`。
- `with_open=false` 时跳过 ulist 请求（省一次上游调用）。
- 板块清单接口失败：整个列表 `unavailable`；仅 ulist 失败：返回列表 + note「开盘涨跌幅不可用」。

### 3.4 `utils/review_market.py` 改造（R3）

- `get_zt_pool` 加 `full: bool = false`；false 保持 `MAX_POOL_ROWS=60` 截断（现行为），
  true 返回全部行。工具签名 `get_zt_pool_tool` 同步透传。
- 三池加 TTL 缓存 + single-flight：当日 60s，历史日期 600s（数据不变）。
  缓存 key = `(pool, date)`。
- 响应加 `as_of`/`stale`/`cached`；沿用现有中文列名与金额亿元口径。

## 4. 响应契约（R4/R5 的服务端部分）

所有新/改工具的数据负载统一带：

| 字段 | 含义 |
|---|---|
| `as_of` | 数据获取时刻，ISO8601 +08:00 |
| `stale` | 是否陈旧（stale 降级时 true） |
| `cached` | 是否缓存命中（命中时 `as_of` 为原始获取时刻） |
| `source` | 如 `eastmoney_push2_clist` / `eastmoney_push2ex` |

错误三态：

| 情况 | 形态 | 客户端行为 |
|---|---|---|
| 业务错误 | `success:false, error_code:"BOARD_NOT_FOUND"`（另有可读 `error`） | 不重试 |
| 上游失败、无缓存 | `success:true, data:null, status:"unavailable", reason:"rate_limited"\|"upstream_error"` | 终态，不重试，标「待人工判」 |
| 上游失败、有旧缓存 | 正常 data + `stale:true` | 可用但标记陈旧 |
| 入口限流 | `success:false, error_type:"RateLimitError"`（现有 `RateLimiter`） | 退避后重试 |

## 5. 测试与验收

**单元测试**（mock requests，不依赖网络）：

- 空响应识别 → 退避 → 第二轮成功 / 两轮失败返回 `unavailable`
- single-flight：并发 N 线程同 key，上游只被打一次
- `BOARD_NOT_FOUND` / `no_open` / 原文透传（`游戏Ⅱ`）/ `full=true` 全量 / stale 降级
- 失败不写缓存（失败后下一次请求仍会打上游）

**实现期实测验证**（服务端冒烟）：

1. ulist.np/get 对 `90.BKxxxx` secid + f46/f60 的支持性；
2. clist `m:90` 下 f62/f104/f105/f128/f140/f141 是否齐返；
3. akproxy 出口下空响应的实际 JSON 形态。

**验收对照**（需求文档 A1~A8 中服务端部分）：

- A1 `get_board_quote_tool(board_name="元件")` 开盘涨跌幅与 f46/f60 手工计算一致（<0.01pp）
- A2 高频调用 20 次不整体失败（限流时 `unavailable` 而非抛错）
- A3 `board_name` 原文返回（`游戏Ⅱ`/`自动化设`）
- A4 `full=true` 时 `count == len(items)`（2026-11-12 涨停 85 / 炸板 19）
- A5/A6 全量池可复算链强度/宽度指标（电子链桶、炸板率 18.27%、晋级率 20.43%）
- A7 缓存命中 `cached:true` 且 `as_of` 为原始时间

### 验证实测（2026-11-12 11:30–11:33 +08:00，事件日盘中/午间；探测脚本 `scripts/probe_em_board_fields.py`，冒烟 `tests/test_live_board_smoke.py -m live` → 4 passed）

**实现期验证点结论**：

1. **clist m:90 f17/f18 支持**（数值形态，fltt=2 已格式化）→ **开盘价主通道 = clist**，
   `board_quotes` 的 clist-open 捷径为生产主路径，无需额外开盘请求。
   实测：BK0459 元件 f17=49405.61 / f18=49485.34；BK1514 水产养殖 f17=615.58 / f18=615.20；
   行业/概念两清单前 100 行 f17/f18 数值行均 100/100。f62/f104/f105/f128/f140/f141 全部返回。
2. **ulist 认 90.BK secid 但不开价**：返回行（f12/f14 正确）但 f46/f60 恒为 `"-"` 占位符 →
   ulist 不可作板块开盘通道；进程内 `ulist_ok=False` 自动退化 stock/get。
   stock/get 对板块 secid 返回全数值（f46=49405.61 / f60=49485.34，与 clist f17/f18 同值交叉验证）。
3. **空响应/形态**：本轮未触发限流，未捕获 akproxy 空响应实例；fltt=2 数值直接为 float
   （f2=643.07），`"-"` 为字段占位符（`_f()` 转 None，实测 ulist 即此形态）。
   - 附带发现：clist 单页上限 **100 行**（`pz=600` 实返 100，total=496，pn=2 起有后续页）→
     现快照按 f3 排序仅覆盖前 100 板块，按名查询落在 100 名外会 `BOARD_NOT_FOUND`；
     补全需分页 5 页（与 pacing 叠加冷启动 ≈7.5s/清单），列为后续优化项，不阻塞本期验收。

**A1~A7 验收实测**：

- A1 ✓ `get_board_quote(board_name="元件")` open=49405.61 / prev_close=49485.34 /
  **open_pct=-0.161%**，与 f17/f18 手工复算 -0.161% 一致（偏差 0.00pp < 0.01pp）
- A2 ✓ 连续 20 次调用零抛错（live 冒烟 4 测 6.78s 全过；缓存 + RateLimiter 20/60s 下无异常信封）
- A3 ✓ board_name 原文透传：清单实测含 `电子化学品Ⅱ`/`电子化学品Ⅲ`/`航空装备Ⅱ`/`航空装备Ⅲ`；
  涨停池所属行业同为原文（`房地产开`/`农产品加`/`游戏Ⅱ` 4 字截断名原样）
- A4 ✓ full=true 时 count == len(items)：涨停 **46==46**（11:32 午间快照；事件日收盘终值 85，
  盘中池持续演变属预期）、炸板 **19**（与已知 19 一致）
- A5/A6 ✓（服务端就绪，复算在客户端）：全量池 46 行不截断，13 字段含
  `连板数`/`炸板次数`/`涨停统计`/`所属行业` 复算原料齐备；电子链分桶午间快照：元件 1、其余电子类 0
- A7 ✓ 二次调用 `cached:true` 且 `as_of` 保持原始获取时刻（两调均 2026-11-12T11:32:55+08:00）

## 6. 非目标

- 不做板块成分股枚举（496 板块枚举会加剧限流）。
- 不做实盘账户/持仓接口。
- 除 `get_zt_pool_tool` 加 `full` 参数外，不改现有工具的既有返回结构。
- 客户端 `mcp_fetch` 封装与 Hermes 脚本迁移不在本 repo。
- 龙虎榜（datacenter-web）与腾讯 gtimg 保持现状。

## 7. 部署

无新依赖（requests/pandas/akshare/efinance 已有）。实现后照常构建镜像推送
`docker.sihanfu.cn:15999/mcp-stock-server`，远程 Docker 部署验收。
